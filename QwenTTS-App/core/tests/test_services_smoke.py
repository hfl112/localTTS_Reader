import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.services.performance import get_performance_profile
from core.services.saved_items_service import SavedItemsService
from core.services.podcast_service import PodcastService
from core.services.podcast_jobs import PodcastJobStore
from core.services.runtime_log import RuntimeEventLog
from core.state.runtime_state import RuntimeState


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
