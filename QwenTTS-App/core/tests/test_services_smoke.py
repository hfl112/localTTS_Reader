import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.services.performance import get_performance_profile
from core.services.saved_items_service import SavedItemsService
from core.services.podcast_service import PodcastService
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
