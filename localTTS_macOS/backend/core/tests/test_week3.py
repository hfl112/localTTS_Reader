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

