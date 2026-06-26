"""Step 3+4+5 acceptance (CONTEXT.md §4): InferenceEngine.run_loop dispatch.

Drives run_loop with FakeBackend and in-process queue.Queue stand-ins for the
mp queues — no GPU, no real processes. Covers: read lane protocol, read
priority over the podcast lane, sentinel passthrough, task_id invalidation,
and podcast-lane chunk-file output.
"""

import os
import queue
import threading
import time

import numpy as np

from core.inference.engine import InferenceEngine, _IDLE
from core.inference.model_backend import FakeBackend


class _Val:
    def __init__(self, v):
        self.value = v


def _wait_for(path, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path) or os.path.exists(path + ".err"):
            return
        time.sleep(0.02)


class FakeShared:
    def __init__(self, current_task_id=1):
        self.text_q = queue.Queue()
        self.podcast_q = queue.Queue()
        self.audio_q = queue.Queue()
        self.stop_event = threading.Event()
        self.current_task_id = _Val(current_task_id)
        self.vram_mb = _Val(0.0)
        self.frames = 0
        self.errors = []

    def note_audio_frame(self):
        self.frames += 1

    def set_error(self, m):
        self.errors.append(m)


def _engine(tmp_path):
    be = FakeBackend(frames=2)
    eng = InferenceEngine(be, cache_dir=str(tmp_path), models_path=None)
    return eng


def _drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


_PROFILE = lambda name: {"chunk_sleep": 0.0}


# --- read lane: protocol preserved (frames tagged + CHUNK_DONE) ---

def test_read_lane_emits_frames_and_chunk_done(tmp_path):
    eng = _engine(tmp_path)
    sh = FakeShared(current_task_id=1)
    sh.text_q.put({"task_id": 1, "chunk_index": 0, "text": "你好", "config": {"voice": "Serena"}})
    sh.text_q.put(None)  # break

    eng.run_loop(sh, sentinel="SENT", profile_fn=_PROFILE)

    items = _drain(sh.audio_q)
    assert items[-1] == "CHUNK_DONE"
    tuples = [it for it in items if isinstance(it, tuple)]
    assert tuples and all(t[0] == 1 and t[1] == 0 for t in tuples)
    assert sh.frames == len(tuples) > 0


def test_read_lane_task_id_invalidation(tmp_path):
    eng = _engine(tmp_path)
    sh = FakeShared(current_task_id=5)
    # Task for an old session id -> must be skipped, no frames, no CHUNK_DONE.
    sh.text_q.put({"task_id": 2, "chunk_index": 0, "text": "x", "config": {}})
    sh.text_q.put(None)

    eng.run_loop(sh, sentinel="SENT", profile_fn=_PROFILE)

    assert sh.frames == 0
    assert _drain(sh.audio_q) == []


def test_sentinel_passthrough(tmp_path):
    eng = _engine(tmp_path)
    sh = FakeShared()
    sh.text_q.put("SENT")
    sh.text_q.put(None)

    eng.run_loop(sh, sentinel="SENT", profile_fn=_PROFILE)
    assert _drain(sh.audio_q) == ["SENT"]


# --- read priority: text_q drained before podcast_q ---

def test_next_task_prefers_read_over_podcast(tmp_path):
    eng = _engine(tmp_path)
    sh = FakeShared()
    sh.podcast_q.put({"job_id": "j", "chunk_index": 0, "text": "p", "config": {}})
    sh.text_q.put({"task_id": 1, "chunk_index": 0, "text": "r", "config": {}})

    task, is_podcast = eng._next_task(sh, idle_unload_sec=600, last_active=0)
    assert is_podcast is False and task["text"] == "r"


# --- podcast lane: synth full chunk -> chunk file + result signal ---

def test_podcast_lane_writes_chunk_file(tmp_path):
    eng = _engine(tmp_path)
    sh = FakeShared()
    chunk_file = os.path.join(str(tmp_path), "chunk_00000.npy")

    t = threading.Thread(
        target=eng.run_loop,
        kwargs={"shared_state": sh, "sentinel": "SENT", "profile_fn": _PROFILE},
        daemon=True,
    )
    t.start()
    sh.podcast_q.put(
        {"job_id": "job1", "chunk_index": 0, "chunk_file": chunk_file,
         "text": "对话一句", "config": {"voice": "Ryan"}}
    )
    _wait_for(chunk_file)
    sh.text_q.put(None)  # stop the loop
    t.join(timeout=5)

    assert os.path.exists(chunk_file), "engine must write the podcast chunk file"
    assert not os.path.exists(chunk_file + ".err")
    data = np.load(chunk_file)
    assert data.ndim == 2 and data.shape[1] == 2  # stereo, WAV-writer compatible


def test_podcast_lane_does_not_touch_audio_q(tmp_path):
    eng = _engine(tmp_path)
    sh = FakeShared()
    chunk_file = os.path.join(str(tmp_path), "c.npy")
    t = threading.Thread(
        target=eng.run_loop,
        kwargs={"shared_state": sh, "sentinel": "SENT", "profile_fn": _PROFILE},
        daemon=True,
    )
    t.start()
    sh.podcast_q.put({"job_id": "j", "chunk_index": 0, "chunk_file": chunk_file, "text": "x", "config": {}})
    _wait_for(chunk_file)
    sh.text_q.put(None)
    t.join(timeout=5)

    assert _drain(sh.audio_q) == [], "podcast frames must not reach the player audio_q"
