import asyncio
import multiprocessing as mp
import os
import signal
import sys
import tempfile
import threading
import time


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.services.runtime_supervisor import RuntimeSupervisor, stop_process
from core.services.podcast_service import PodcastService
from core.state.runtime_state import RuntimeState


def _stubborn_process(ready):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    ready.set()
    while True:
        time.sleep(0.1)


class FakeProcess:
    def __init__(self, *, alive=True, stop_on_terminate=True):
        self._alive = alive
        self.stop_on_terminate = stop_on_terminate
        self.pid = 123
        self.join_calls = []
        self.terminate_calls = 0
        self.kill_calls = 0

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        self.join_calls.append(timeout)

    def terminate(self):
        self.terminate_calls += 1
        if self.stop_on_terminate:
            self._alive = False

    def kill(self):
        self.kill_calls += 1
        self._alive = False


class FakeQueue:
    def __init__(self):
        self.items = []
        self.closed = False
        self.joined = False

    def put(self, item):
        self.items.append(item)

    def close(self):
        self.closed = True

    def join_thread(self):
        self.joined = True


class DummyState:
    def __init__(self):
        self.stop_event = threading.Event()
        self.text_q = FakeQueue()
        self.audio_q = FakeQueue()


class DummyPlayback:
    def __init__(self):
        self.stop_calls = 0
        self.shutdown_calls = 0

    def begin_shutdown(self):
        self.stop_calls += 1

    def shutdown(self, join_timeout):
        self.shutdown_calls += 1


class DummyPodcasts:
    def __init__(self):
        self.shutdown_calls = 0

    def shutdown(self, **kwargs):
        self.shutdown_calls += 1


class DummyPlayer:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


class DummyJobStore:
    def __init__(self):
        self.updates = []

    def update(self, job_id, **fields):
        self.updates.append((job_id, fields))


def test_stop_process_joins_gracefully_exited_process():
    process = FakeProcess(alive=False)

    result = stop_process(process, graceful_timeout=0.1, terminate_timeout=0.1)

    assert result == "joined"
    assert process.terminate_calls == 0
    assert process.kill_calls == 0
    assert process.join_calls == [0.1, 0]


def test_stop_process_escalates_to_kill_and_reaps():
    process = FakeProcess(alive=True, stop_on_terminate=False)

    result = stop_process(process, graceful_timeout=0.1, terminate_timeout=0.1)

    assert result == "killed"
    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert process.is_alive() is False
    assert process.join_calls == [0.1, 0.1, 0.1, 0]


def test_stop_process_kills_real_unresponsive_child():
    context = mp.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=_stubborn_process, args=(ready,))
    process.start()
    try:
        assert ready.wait(5)
        result = stop_process(
            process,
            graceful_timeout=0.05,
            terminate_timeout=0.1,
        )
        assert result == "killed"
        assert not process.is_alive()
        assert process.exitcode is not None
    finally:
        if process.is_alive():
            process.kill()
        process.join(1)


def test_runtime_supervisor_shutdown_is_complete_and_idempotent():
    asyncio.run(_assert_runtime_supervisor_shutdown_is_complete_and_idempotent())


async def _assert_runtime_supervisor_shutdown_is_complete_and_idempotent():
    state = DummyState()
    playback = DummyPlayback()
    podcasts = DummyPodcasts()
    player = DummyPlayer()
    jobs = DummyJobStore()
    active_urls = {"https://example.com": {"job_id": "url-1"}}
    supervisor = RuntimeSupervisor(
        shared_state=state,
        player=player,
        playback_service=playback,
        podcast_service=podcasts,
        url_job_store=jobs,
        active_url_tasks=active_urls,
        graceful_timeout=0,
        terminate_timeout=0,
        thread_timeout=0.1,
    )
    supervisor.inference_process = FakeProcess(alive=True)
    supervisor.start_thread(lambda stop: stop.wait(), name="dummy-runtime-thread")

    task_started = asyncio.Event()

    async def long_url_task():
        task_started.set()
        await asyncio.Event().wait()

    task = supervisor.create_task(long_url_task(), job_id="url-1")
    await task_started.wait()

    await supervisor.shutdown()
    await supervisor.shutdown()

    assert task.cancelled()
    assert supervisor.accepting_requests is False
    assert state.stop_event.is_set()
    assert state.text_q.items == [None]
    assert state.audio_q.items == [None]
    assert state.text_q.closed and state.text_q.joined
    assert state.audio_q.closed and state.audio_q.joined
    assert playback.stop_calls == 1
    assert playback.shutdown_calls == 1
    assert podcasts.shutdown_calls == 1
    assert player.close_calls == 1
    assert active_urls == {}
    assert jobs.updates[-1][0] == "url-1"
    assert jobs.updates[-1][1]["stage"] == "interrupted"


def test_podcast_shutdown_reaps_workers_and_stops_manager_thread():
    with tempfile.TemporaryDirectory() as tmp:
        service = PodcastService(
            podcasts_dir=os.path.join(tmp, "podcasts"),
            podcast_chunk_dir=os.path.join(tmp, "chunks"),
            runtime_state=RuntimeState(),
            active_url_tasks={},
        )
        process = FakeProcess(alive=True, stop_on_terminate=False)
        service.active_procs.append(process)
        service.active_tasks["abc"] = process

        service.shutdown(graceful_timeout=0, terminate_timeout=0.1)

        assert process.terminate_calls == 1
        assert process.kill_calls == 1
        assert service.active_procs == []
        assert service.active_tasks == {}
        assert service.worker_shutdown_event.is_set()
        assert not service._manager_thread.is_alive()
