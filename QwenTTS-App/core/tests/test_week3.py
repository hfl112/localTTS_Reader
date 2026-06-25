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

