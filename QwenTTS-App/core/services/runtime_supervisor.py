import asyncio
import inspect
import os
import signal
import threading
from collections.abc import Coroutine
from typing import Any, Callable


def stop_process(
    process: Any,
    *,
    graceful_timeout: float = 2.0,
    terminate_timeout: float = 2.0,
) -> str:
    """Reap a multiprocessing-style process with graceful escalation."""
    if process is None:
        return "missing"

    try:
        process.join(graceful_timeout)
    except (AssertionError, OSError, ValueError):
        return "unavailable"

    if not process.is_alive():
        process.join(0)
        return "joined"

    try:
        process.terminate()
    except (AttributeError, OSError):
        pass
    process.join(terminate_timeout)
    if not process.is_alive():
        process.join(0)
        return "terminated"

    try:
        process.kill()
    except AttributeError:
        pid = getattr(process, "pid", None)
        if pid:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    process.join(terminate_timeout)
    if process.is_alive():
        return "alive"
    process.join(0)
    return "killed"


class RuntimeSupervisor:
    """Owns backend processes, tasks, threads, queues, and shutdown order."""

    def __init__(
        self,
        *,
        shared_state: Any,
        player: Any,
        playback_service: Any,
        podcast_service: Any,
        url_job_store: Any | None = None,
        active_url_tasks: dict[str, dict] | None = None,
        event_log: Any | None = None,
        graceful_timeout: float = 2.0,
        terminate_timeout: float = 2.0,
        thread_timeout: float = 2.0,
    ) -> None:
        self.shared_state = shared_state
        self.player = player
        self.playback_service = playback_service
        self.podcast_service = podcast_service
        self.url_job_store = url_job_store
        self.active_url_tasks = active_url_tasks
        self.event_log = event_log
        self.graceful_timeout = graceful_timeout
        self.terminate_timeout = terminate_timeout
        self.thread_timeout = thread_timeout

        self.shutdown_event = threading.Event()
        self.inference_process: Any | None = None
        self.threads: list[threading.Thread] = []
        self.async_tasks: dict[asyncio.Task, str | None] = {}
        self.accepting_requests = True

        self._state_lock = threading.Lock()
        self._shutdown_future: asyncio.Future | None = None

    def start_inference(self, target: Callable, args: tuple[Any, ...]) -> Any:
        import multiprocessing as mp

        if self.inference_process is not None:
            raise RuntimeError("inference process already started")
        process = mp.Process(target=target, args=args, daemon=True)
        process.start()
        self.inference_process = process
        self._record("inference_process_started", pid=process.pid)
        return process

    def start_thread(
        self,
        target: Callable[[threading.Event], None],
        *,
        name: str,
    ) -> threading.Thread:
        thread = threading.Thread(
            target=target,
            args=(self.shutdown_event,),
            name=name,
            daemon=True,
        )
        thread.start()
        self.threads.append(thread)
        return thread

    def create_task(
        self,
        coroutine: Coroutine[Any, Any, Any],
        *,
        job_id: str | None = None,
    ) -> asyncio.Task:
        if not self.accepting_requests:
            if inspect.iscoroutine(coroutine):
                coroutine.close()
            raise RuntimeError("runtime is shutting down")
        task = asyncio.create_task(coroutine)
        self.async_tasks[task] = job_id
        task.add_done_callback(self._forget_task)
        return task

    async def shutdown(self) -> None:
        loop = asyncio.get_running_loop()
        with self._state_lock:
            if self._shutdown_future is None:
                self._shutdown_future = loop.create_future()
                owner = True
            else:
                owner = False
            shutdown_future = self._shutdown_future

        if not owner:
            await asyncio.shield(shutdown_future)
            return

        try:
            await self._shutdown_once()
        finally:
            if not shutdown_future.done():
                shutdown_future.set_result(None)

    async def _shutdown_once(self) -> None:
        self.accepting_requests = False
        self.shutdown_event.set()
        self._record("runtime_shutdown_started")

        try:
            if self.playback_service is not None:
                self.playback_service.begin_shutdown()
        except Exception as error:
            self._record("runtime_shutdown_error", component="playback", error=str(error))

        try:
            await self._cancel_async_tasks()
        except Exception as error:
            self._record("runtime_shutdown_error", component="url_tasks", error=str(error))

        if self.podcast_service is not None:
            try:
                self.podcast_service.shutdown(
                    graceful_timeout=self.graceful_timeout,
                    terminate_timeout=self.terminate_timeout,
                )
            except Exception as error:
                self._record("runtime_shutdown_error", component="podcasts", error=str(error))

        try:
            self.shared_state.stop_event.set()
            self.shared_state.text_q.put(None)
        except Exception as error:
            self._record("runtime_shutdown_error", component="inference_signal", error=str(error))

        try:
            result = stop_process(
                self.inference_process,
                graceful_timeout=self.graceful_timeout,
                terminate_timeout=self.terminate_timeout,
            )
        except Exception as error:
            result = "error"
            self._record(
                "runtime_shutdown_error",
                component="inference_process",
                error=str(error),
            )
        self._record("inference_process_stopped", result=result)

        try:
            self.shared_state.audio_q.put(None)
        except Exception:
            pass

        if self.playback_service is not None:
            try:
                self.playback_service.shutdown(join_timeout=self.thread_timeout)
            except Exception as error:
                self._record(
                    "runtime_shutdown_error",
                    component="playback_threads",
                    error=str(error),
                )

        for thread in self.threads:
            if thread is not threading.current_thread():
                thread.join(self.thread_timeout)

        if self.player is not None:
            try:
                self.player.close()
            except Exception as error:
                self._record("runtime_shutdown_error", component="player", error=str(error))

        self._close_queues()
        self._record("runtime_shutdown_finished")

    async def _cancel_async_tasks(self) -> None:
        current = asyncio.current_task()
        tracked = {
            task: job_id
            for task, job_id in self.async_tasks.items()
            if task is not current and not task.done()
        }
        for task in tracked:
            task.cancel()

        if tracked:
            done, pending = await asyncio.wait(
                list(tracked), timeout=self.thread_timeout
            )
            for task in done:
                self._mark_url_job_interrupted(tracked[task])
            for task in pending:
                self._mark_url_job_interrupted(tracked[task])

        if self.active_url_tasks is not None:
            self.active_url_tasks.clear()

    def _mark_url_job_interrupted(self, job_id: str | None) -> None:
        if job_id and self.url_job_store is not None:
            try:
                self.url_job_store.update(
                    job_id,
                    status="failed",
                    stage="interrupted",
                    error="backend shutdown interrupted URL job",
                )
            except Exception:
                pass

    def _forget_task(self, task: asyncio.Task) -> None:
        self.async_tasks.pop(task, None)

    def _close_queues(self) -> None:
        for queue_name in ("text_q", "audio_q"):
            queue = getattr(self.shared_state, queue_name, None)
            if queue is None:
                continue
            try:
                queue.close()
            except (AttributeError, OSError, ValueError):
                continue
            try:
                queue.join_thread()
            except (AttributeError, AssertionError, OSError, ValueError):
                pass

    def start_watchdog(self, loop: asyncio.AbstractEventLoop) -> None:
        """Starts a background thread to read from a watchdog FD."""
        fd_str = os.environ.get("TTS_WATCHDOG_FD")
        if not fd_str:
            return

        try:
            fd = int(fd_str)
        except ValueError:
            self._record("watchdog_start_error", error="invalid watchdog FD value")
            return

        try:
            import fcntl
            flags = fcntl.fcntl(fd, fcntl.F_GETFD)
            fcntl.fcntl(fd, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)
        except Exception as error:
            self._record("watchdog_flags_error", error=str(error))

        async def shutdown_after_watchdog() -> None:
            await self.shutdown()
            if os.environ.get("TTS_WATCHDOG_EXIT_PROCESS") == "1":
                os.kill(os.getpid(), signal.SIGTERM)

        def watchdog_loop() -> None:
            self._record("watchdog_started", fd=fd)
            try:
                data = os.read(fd, 1)
                if not data:
                    self._record("watchdog_eof_detected")
                    asyncio.run_coroutine_threadsafe(shutdown_after_watchdog(), loop)
            except Exception as error:
                self._record("watchdog_read_error", error=str(error))
                asyncio.run_coroutine_threadsafe(shutdown_after_watchdog(), loop)

        thread = threading.Thread(
            target=watchdog_loop,
            name="watchdog-thread",
            daemon=True,
        )
        thread.start()

    def _record(self, event: str, **fields: Any) -> None:
        if self.event_log is not None:
            try:
                self.event_log.record(event, **fields)
            except Exception:
                pass
