import asyncio
import os
import sys
import threading
import time
from fastapi.testclient import TestClient

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.services.runtime_supervisor import RuntimeSupervisor
from core.backend import app, init_runtime_services


def test_management_token_middleware():
    os.environ["TTS_MANAGEMENT_TOKEN"] = "test-token-123"
    # 确保初始化服务，否则 /stop 里引用的一些服务可能是 None
    init_runtime_services()
    try:
        client = TestClient(app)
        # 1. 无 token 应当返回 401
        response = client.post("/stop")
        assert response.status_code == 401
        assert "invalid management token" in response.json()["detail"]

        # 2. 错误 token 应当返回 401
        response = client.post("/stop", headers={"x-management-token": "wrong-token"})
        assert response.status_code == 401

        # 3. 正确 token 应当返回 200
        response = client.post("/stop", headers={"x-management-token": "test-token-123"})
        assert response.status_code == 200
    finally:
        os.environ.pop("TTS_MANAGEMENT_TOKEN", None)


def test_legacy_loopback_client_bypasses_management_token():
    os.environ["TTS_MANAGEMENT_TOKEN"] = "native-client-token"
    os.environ["TTS_LEGACY_LOOPBACK_CLIENTS"] = "1"
    init_runtime_services()
    try:
        client = TestClient(app)
        response = client.post("/stop")
        assert response.status_code == 200
    finally:
        os.environ.pop("TTS_MANAGEMENT_TOKEN", None)
        os.environ.pop("TTS_LEGACY_LOOPBACK_CLIENTS", None)


class DummyState:
    def __init__(self):
        self.stop_event = threading.Event()
        self.text_q = None
        self.audio_q = None


def test_watchdog_eof_triggers_shutdown():
    fd_read, fd_write = os.pipe()
    os.environ["TTS_WATCHDOG_FD"] = str(fd_read)

    try:
        loop = asyncio.new_event_loop()
        state = DummyState()
        supervisor = RuntimeSupervisor(
            shared_state=state,
            player=None,
            playback_service=None,
            podcast_service=None,
            graceful_timeout=0.01,
            terminate_timeout=0.01,
        )

        supervisor.start_watchdog(loop)
        
        # 关闭写端产生 EOF
        os.close(fd_write)

        # 运行 loop 允许 run_coroutine_threadsafe 的任务执行
        async def wait_and_stop():
            await asyncio.sleep(0.1)

        loop.run_until_complete(wait_and_stop())

        # 验证是否成功触发了 shutdown 并更新了状态
        assert supervisor.accepting_requests is False
        assert state.stop_event.is_set()

    finally:
        os.environ.pop("TTS_WATCHDOG_FD", None)
        try:
            os.close(fd_read)
        except OSError:
            pass
        loop.close()
