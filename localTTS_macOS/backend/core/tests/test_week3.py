import asyncio
import os
import sys
import tempfile
import time
from fastapi.testclient import TestClient

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.paths import RuntimePaths
from core.storage import Storage
from core.backend import app, init_runtime_services


def test_runtime_paths_resolution():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["TTS_APP_SUPPORT_PATH"] = tmp
        try:
            paths = RuntimePaths()
            assert paths.app_support_path == os.path.abspath(tmp)
            assert paths.data_path == os.path.join(paths.app_support_path, "Data")
            assert paths.cache_path == os.path.join(paths.app_support_path, "Cache")
            assert paths.podcasts_path == os.path.join(paths.app_support_path, "Podcasts")
            assert paths.models_path == os.path.join(paths.app_support_path, "Models")
            assert paths.logs_path == os.path.join(paths.app_support_path, "Logs")

            assert os.path.exists(paths.data_path)
            assert os.path.exists(paths.cache_path)
            assert os.path.exists(paths.podcasts_path)
            assert os.path.exists(paths.models_path)
            assert os.path.exists(paths.logs_path)
        finally:
            os.environ.pop("TTS_APP_SUPPORT_PATH", None)


def test_runtime_paths_bundled_resource_env_overrides_win():
    """资源路径优先用环境变量（打包/自定义），不应回落到按 repo 深度推断的默认值。"""
    with tempfile.TemporaryDirectory() as tmp:
        ref = os.path.join(tmp, "BundledRef")
        mlx = os.path.join(tmp, "BundledMLX")
        ff = os.path.join(tmp, "ffmpeg")
        os.environ["TTS_APP_SUPPORT_PATH"] = tmp
        os.environ["TTS_REFERENCE_PATH"] = ref
        os.environ["MLX_AUDIO_PATH"] = mlx
        os.environ["TTS_FFMPEG_PATH"] = ff
        try:
            paths = RuntimePaths()
            # env 覆盖生效：指向给定路径，而非按 repo 深度推断的默认 reference/mlx_audio
            assert paths.reference_path == os.path.abspath(ref)
            assert paths.mlx_audio_path == os.path.abspath(mlx)
            assert paths.ffmpeg_path == os.path.abspath(ff)
        finally:
            for k in ("TTS_APP_SUPPORT_PATH", "TTS_REFERENCE_PATH", "MLX_AUDIO_PATH", "TTS_FFMPEG_PATH"):
                os.environ.pop(k, None)


def test_storage_atomic_save():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(data_dir=tmp)

        config = {"voice": "Ryan", "temperature": 0.5}
        storage.save_config(config)
        assert storage.load_config() == config

        state = {
            "current_article": {
                "title": "Test Title",
                "chunks": ["hello"],
                "current_index": 0,
            },
            "history": [],
        }
        storage.save_state(state)
        assert storage.load_state() == state


def test_storage_load_is_corruption_safe_and_isolated():
    import glob
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(data_dir=tmp)
        # 1) 缺文件返回独立深拷贝：改返回值不污染默认/后续读取
        st = storage.load_state()
        st["current_article"]["current_index"] = 99
        assert storage.default_state["current_article"]["current_index"] == 0
        assert storage.load_state()["current_article"]["current_index"] == 0
        # 2) 损坏文件被备份且回退默认（不静默覆盖、不抛异常）
        with open(storage.state_path, "w") as f:
            f.write("{not valid json,,,")
        recovered = storage.load_state()
        assert recovered["current_article"]["current_index"] == 0
        assert glob.glob(storage.state_path + ".corrupt.*"), "corrupt file should be backed up"


def test_snapshot_and_status_expose_agreeing_playback_status():
    """ADR-003 A2: /snapshot and /status both expose playback_status from the
    one owner (PlaybackService.playback_status()), so they always agree, and it
    reflects the real player ground truth (not a hardcoded value)."""
    import core.backend as backend_mod

    init_runtime_services()
    client = TestClient(app)
    p = backend_mod.player
    assert p is not None

    # Force a "playing" ground truth directly on the real player.
    p.is_paused = False
    p.is_prebuffering = False
    p.playback_finished_event.clear()  # is_running() -> True
    snap = client.get("/snapshot").json()
    status = client.get("/status").json()
    assert snap["playback_status"] == "playing"
    assert status["playback_status"] == "playing"
    assert snap["playback_status"] == status["playback_status"]

    # Not running -> idle, on both endpoints.
    p.playback_finished_event.set()
    assert client.get("/snapshot").json()["playback_status"] == "idle"
    assert client.get("/status").json()["playback_status"] == "idle"


def test_playback_commands_return_new_status(monkeypatch):
    """ADR-003 A3: /pause /resume /stop return the new playback_status in the
    body so the UI applies it optimistically (no ~500ms poll lag)."""
    import core.backend as backend_mod

    # State-changing POSTs are auth-gated; use the loopback dev bypass for the
    # TestClient ("testclient" host is loopback under pytest).
    monkeypatch.setenv("TTS_LEGACY_LOOPBACK_CLIENTS", "1")
    init_runtime_services()
    client = TestClient(app)
    p = backend_mod.player
    assert p is not None
    p.playback_finished_event.clear()  # running
    p.is_prebuffering = False
    p.is_paused = False

    assert client.post("/pause").json()["playback_status"] == "paused"
    assert client.post("/resume").json()["playback_status"] in ("playing", "generating")
    assert client.post("/stop").json()["playback_status"] == "idle"


def test_snapshot_contract():
    """ADR-003 A4: cross-seam contract. ① playback_status present+valid on both
    endpoints; ② steady-state equivalence between computed status and the wire
    is_paused alias (driven synchronously on the real player — NOT via the async
    play() path, which has a set_main-leads-start transient that would be flaky);
    ③ /snapshot carries the keys the frontend consumes."""
    import core.backend as backend_mod

    init_runtime_services()
    client = TestClient(app)
    p = backend_mod.player
    assert p is not None
    VALID = {"idle", "generating", "playing", "paused"}

    # ① present + valid on both endpoints
    snap = client.get("/snapshot").json()
    st = client.get("/status").json()
    assert snap["playback_status"] in VALID
    assert st["playback_status"] in VALID

    # ③ frontend-consumed key set present
    for k in (
        "main_title", "main_progress", "main_is_playing", "is_paused",
        "playback_status", "status_code", "current_article_chunks",
        "current_article_index", "instance_id",
    ):
        assert k in snap, f"/snapshot missing frontend-consumed key: {k}"

    # ② steady-state equivalence (running, paused, prebuffering) -> expected status
    cases = [
        (False, False, False, "idle"),
        (True,  False, False, "playing"),
        (True,  False, True,  "generating"),
        (True,  True,  False, "paused"),
    ]
    for running, paused, prebuf, expect in cases:
        if running:
            p.playback_finished_event.clear()
        else:
            p.playback_finished_event.set()
        p.is_paused = paused
        p.is_prebuffering = prebuf
        s = client.get("/snapshot").json()
        assert s["playback_status"] == expect, (running, paused, prebuf, s["playback_status"])
        # the legacy wire alias never contradicts the computed truth
        assert s["is_paused"] == paused
        if expect == "paused":
            assert s["is_paused"] is True


def test_restart_mode_replays_from_start_and_noops_when_empty(monkeypatch):
    """ADR-003 F2: the play button when idle restarts the CURRENT article from
    the beginning (start_idx=0, current_index reset to 0). With no article it
    must be a safe no-op, not a KeyError 500."""
    import core.backend as backend_mod

    monkeypatch.setenv("TTS_LEGACY_LOOPBACK_CLIENTS", "1")
    init_runtime_services()
    client = TestClient(app)

    # No current article → safe no-op (this used to KeyError 500).
    st = backend_mod.storage.load_state()
    st.pop("current_article", None)
    backend_mod.storage.save_state(st)
    r = client.post("/read", json={"text": "RESTART_MODE"})
    assert r.status_code == 200
    assert r.json().get("status") == "noop"

    # With an article at index 2 → restart from 0.
    st = backend_mod.storage.load_state()
    st["current_article"] = {"title": "T", "chunks": ["a", "b", "c"], "current_index": 2}
    backend_mod.storage.save_state(st)
    r = client.post("/read", json={"text": "RESTART_MODE"})
    assert r.status_code == 200
    assert r.json().get("status") == "ok"
    assert backend_mod.storage.load_state()["current_article"]["current_index"] == 0


def test_new_routes():
    init_runtime_services()
    client = TestClient(app)

    # 1. Test /health
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert "instance_id" in data
    assert "pid" in data
    assert "accepting_requests" in data

    # 2. Test /snapshot
    response = client.get("/snapshot")
    assert response.status_code == 200
    data = response.json()
    assert "status_code" in data
    assert "instance_id" in data

    # 3. Test /settings (GET)
    response = client.get("/settings")
    assert response.status_code == 200
    config = response.json()
    assert "voice" in config

    # 4. Test /settings (PATCH) and Management Token verification
    os.environ["TTS_MANAGEMENT_TOKEN"] = "token-456"
    try:
        # Middleware intercepts unauthorized setting updates
        response = client.patch("/settings", json={"voice": "Ryan"})
        assert response.status_code == 401

        # Allowed with correct token
        response = client.patch(
            "/settings",
            json={"voice": "Ryan"},
            headers={"x-management-token": "token-456"},
        )
        assert response.status_code == 200
        assert response.json()["config"]["voice"] == "Ryan"
    finally:
        os.environ.pop("TTS_MANAGEMENT_TOKEN", None)

    # 5. Test /control/heartbeat
    response = client.post("/control/heartbeat")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    # 6. Test /control/shutdown
    import unittest.mock
    with unittest.mock.patch("os.kill") as mock_kill:
        response = client.post("/control/shutdown")
        assert response.status_code == 200
        assert response.json() == {"status": "shutting_down"}
