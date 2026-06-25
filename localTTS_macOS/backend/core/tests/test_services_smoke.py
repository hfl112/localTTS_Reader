import os
import sys
import tempfile
import multiprocessing as mp
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
URL_READER_ROOT = os.path.abspath(os.path.join(ROOT, "URL-Reader"))
if URL_READER_ROOT not in sys.path:
    sys.path.insert(0, URL_READER_ROOT)

from core.api_models import (
    GenerateSinglePodcastRequest,
    PlaySavedRequest,
    ReadRequest,
    ReadUrlRequest,
)
from core.services.performance import get_performance_profile
from core.services import podcast_service as podcast_service_module
from core.services.saved_items_service import SavedItemsService
from core.services.podcast_service import PodcastService
from core.services.podcast_jobs import PodcastJobStore
from core.services.runtime_log import RuntimeEventLog
from core.services.playback_service import PlaybackController
from core.services.url_jobs import UrlJobStore
from core.state.runtime_state import RuntimeState
from reader_service import cache_key, clean_markdown_content, extract_title, title_for_mode


def test_performance_profile_defaults_to_balanced():
    assert get_performance_profile("quiet")["name"] == "quiet"
    assert get_performance_profile("missing")["name"] == "balanced"


def test_runtime_state_snapshot_and_podcast_buffer():
    state = RuntimeState()
    state.set_main(is_playing=True, title="Title", progress="1/2")
    state.set_current_media(podcast="a.wav", md5="abc")
    state.set_podcast_file("/tmp/out.wav")
    state.append_podcast_audio("chunk")

    snapshot = state.snapshot()
    assert snapshot["main_is_playing"] is True
    assert snapshot["main_title"] == "Title"
    assert snapshot["podcast_buffer_chunks"] == 1

    podcast_file, buffer = state.consume_podcast_buffer()
    assert podcast_file == "/tmp/out.wav"
    assert buffer == ["chunk"]
    assert state.snapshot()["podcast_buffer_chunks"] == 0


def test_saved_items_service_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        service = SavedItemsService(tmp)
        count = service.save("hello world", source="test", voice="Serena", title="Hello")
        assert count == 1
        items = service.load()
        assert items[0]["title"] == "Hello"

        text, voice, md5 = service.selected_text([0])
        assert text == "hello world"
        assert voice == "Serena"
        assert md5 == items[0]["md5"]

        assert service.delete(md5=md5)
        assert service.load() == []


def test_podcast_service_file_ops():
    with tempfile.TemporaryDirectory() as tmp:
        podcasts_dir = os.path.join(tmp, "podcasts")
        os.makedirs(podcasts_dir)
        path = os.path.join(podcasts_dir, "podcast_单篇_web_Title_abcd1234_1.wav")
        with open(path, "wb") as f:
            f.write(b"RIFF")

        service = PodcastService(
            podcasts_dir=podcasts_dir,
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=RuntimeState(),
            active_url_tasks={},
        )

        listed = service.list_files()
        assert listed[0]["filename"] == os.path.basename(path)
        assert service.find_file(os.path.basename(path)) == path
        assert service.toggle_pin(os.path.basename(path))["status"] == "ok"
        assert service.delete("pinned_" + os.path.basename(path))["status"] == "ok"


def test_podcast_pause_state_allows_long_paused_frontend():
    with tempfile.TemporaryDirectory() as tmp:
        state = RuntimeState()
        state.set_main(is_playing=True)
        state.last_active_time = time.time() - 180
        service = PodcastService(
            podcasts_dir=os.path.join(tmp, "podcasts"),
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=state,
            active_url_tasks={},
            is_frontend_active=lambda: False,
            # 电源策略设为 allow，使本用例与运行机器是否插电解耦（此前在电池供电
            # 的机器上会走 battery 分支返回 (True,"battery") 而误失败）。
            get_battery_policy=lambda: "allow",
        )

        should_pause, reason = service._pause_state()
        assert should_pause is False
        assert reason == "none"


def test_podcast_pause_state_blocks_active_frontend():
    with tempfile.TemporaryDirectory() as tmp:
        state = RuntimeState()
        state.last_active_time = time.time() - 180
        service = PodcastService(
            podcasts_dir=os.path.join(tmp, "podcasts"),
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=state,
            active_url_tasks={},
            is_frontend_active=lambda: True,
        )

        should_pause, reason = service._pause_state()
        assert should_pause is True
        assert reason == "frontend_active"


def test_podcast_pause_state_ignores_device_switching():
    with tempfile.TemporaryDirectory() as tmp:
        state = RuntimeState()
        state.last_active_time = time.time() - 10
        service = PodcastService(
            podcasts_dir=os.path.join(tmp, "podcasts"),
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=state,
            active_url_tasks={},
            is_frontend_active=lambda: True,
            is_device_switching=lambda: True,
        )

        should_pause, reason = service._pause_state()
        assert should_pause is False
        assert reason == "device_switching"


def test_podcast_battery_policy_pause_blocks_on_battery(monkeypatch):
    monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: True)
    with tempfile.TemporaryDirectory() as tmp:
        state = RuntimeState()
        state.last_active_time = time.time() - 180
        service = PodcastService(
            podcasts_dir=os.path.join(tmp, "podcasts"),
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=state,
            active_url_tasks={},
            get_battery_policy=lambda: "pause",
        )

        should_pause, reason = service._pause_state()
        assert should_pause is True
        assert reason == "battery"


def test_podcast_battery_policy_quiet_allows_and_forces_quiet(monkeypatch):
    monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: True)
    with tempfile.TemporaryDirectory() as tmp:
        state = RuntimeState()
        state.last_active_time = time.time() - 180
        service = PodcastService(
            podcasts_dir=os.path.join(tmp, "podcasts"),
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=state,
            active_url_tasks={},
            get_battery_policy=lambda: "quiet",
        )

        should_pause, reason = service._pause_state()
        assert should_pause is False
        assert reason == "none"

        config = service._apply_battery_policy_to_config(
            {"performance_profile": "fast", "model": "Qwen3-TTS-1.7B-8bit"}
        )
        assert config["performance_profile"] == "quiet"
        assert config["model"] == "Qwen3-TTS-0.6B"


def test_podcast_battery_policy_allow_does_not_pause(monkeypatch):
    monkeypatch.setattr(podcast_service_module, "is_on_battery_power", lambda: True)
    with tempfile.TemporaryDirectory() as tmp:
        state = RuntimeState()
        state.last_active_time = time.time() - 180
        service = PodcastService(
            podcasts_dir=os.path.join(tmp, "podcasts"),
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=state,
            active_url_tasks={},
            get_battery_policy=lambda: "allow",
        )

        should_pause, reason = service._pause_state()
        assert should_pause is False
        assert reason == "none"


def test_runtime_event_log_recent_events():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "runtime_events.jsonl")
        log = RuntimeEventLog(path, max_events=2)

        log.record("first", value=1)
        log.record("second", value=2)
        log.record("third", value=3)

        events = log.recent(limit=10)
        assert [event["event"] for event in events] == ["second", "third"]
        assert events[-1]["value"] == 3


def test_podcast_job_store_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        store = PodcastJobStore(os.path.join(tmp, "podcast_jobs.json"))

        store.create(
            job_id="job-1",
            kind="single",
            md5="abc",
            title="Title",
            source="web",
        )
        assert store.active_for_md5("abc")

        store.update("job-1", status="done", output_path="/tmp/out.wav")
        jobs = store.list()
        assert jobs[0]["status"] == "done"
        assert jobs[0]["output_path"] == "/tmp/out.wav"
        assert not store.active_for_md5("abc")

        store.create(
            job_id="job-2",
            kind="batch",
            md5="def",
            title="Batch",
            source="web",
        )
        store.mark_unfinished_failed("restart")
        assert store.list()[0]["status"] == "failed"
        assert store.list()[0]["error"] == "restart"


def test_api_models_keep_backward_compatible_defaults():
    read = ReadRequest(text="hello")
    assert read.voice is None
    assert read.from_saved is False
    assert read.performance_profile is None

    read_url = ReadUrlRequest(url="https://example.com", translate=True)
    assert read_url.effective_mode() == "translate"
    assert read_url.action() == "read"
    assert ReadUrlRequest(url="x", save=True).action() == "save"
    assert ReadUrlRequest(url="x", save=True, podcast=True).action() == "podcast"

    podcast = GenerateSinglePodcastRequest(text="hello")
    assert podcast.source == "web"
    assert podcast.performance_profile == "quiet"

    first = PlaySavedRequest()
    second = PlaySavedRequest()
    first.indices.append(1)
    assert second.indices == []


class DummySharedState:
    def __init__(self):
        self.audio_q = mp.Queue()
        self.stop_event = mp.Event()
        self.current_task_id = mp.Value("i", 0)


class DummyPlayer:
    def __init__(self):
        self.audio_queue = mp.Queue()
        self.stop_count = 0

    def stop(self):
        self.stop_count += 1


def test_playback_controller_invalidates_old_sessions():
    shared_state = DummySharedState()
    player = DummyPlayer()
    controller = PlaybackController(shared_state, player)

    first_session, first_task = controller.start_new_session()
    assert controller.can_feed_audio(first_session, first_task)

    second_session, second_task = controller.start_new_session()
    assert not controller.can_feed_audio(first_session, first_task)
    assert controller.can_feed_audio(second_session, second_task)
    assert player.stop_count == 2

    controller.stop_current_session()
    assert not controller.can_feed_audio(second_session, second_task)
    assert shared_state.stop_event.is_set()


def test_url_job_store_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        store = UrlJobStore(os.path.join(tmp, "url_jobs.json"))
        store.create(
            job_id="url-1",
            url="https://example.com",
            mode="podcast-discuss",
            action="podcast",
            has_html=True,
        )
        store.update("url-1", status="running", stage="gemini", text_chars=120)
        assert store.list()[0]["stage"] == "gemini"
        assert store.list()[0]["text_chars"] == 120

        store.mark_unfinished_failed("restart")
        assert store.list()[0]["status"] == "failed"
        assert store.list()[0]["stage"] == "interrupted"


def test_reader_service_helpers_are_stable():
    assert cache_key("a", "bc") != cache_key("ab", "c")
    title_res = title_for_mode("translate", "Title")
    assert title_res.startswith("[译·") or title_res.startswith("[翻译]")
    assert title_res.endswith("Title")
    assert title_for_mode("original", "Title") == "Title"


def test_reader_service_cleans_references_and_web_links():
    raw = """# Title

Useful [article link](https://example.com/a?x=1) text.

<iframe src="https://tracker.example/embed"></iframe>

Bare URL https://tracking.example/path should go.

## References

[^1]: A long citation https://doi.org/example
"""
    cleaned = clean_markdown_content(raw)
    assert "Useful article link text." in cleaned
    assert "iframe" not in cleaned
    assert "https://" not in cleaned
    assert "References" not in cleaned
    assert "long citation" not in cleaned

    zh_cleaned = clean_markdown_content("正文\n\n## 参考文献\n\n[文章](https://example.com)")
    assert zh_cleaned == "正文"


def test_reader_service_does_not_use_references_as_title():
    raw = """![image](https://example.com/image.jpg)

Short article body.

## References

[^1]: Citation.
"""
    assert extract_title(raw) == ""
    assert extract_title(clean_markdown_content(raw)) == ""
    assert title_for_mode("translate", extract_title(clean_markdown_content(raw))) == ""
