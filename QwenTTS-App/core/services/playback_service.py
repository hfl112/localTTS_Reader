import threading
import time
from typing import Any, Callable

import numpy as np


class PlaybackController:
    """Single owner for playback session invalidation and audio queue cleanup."""

    def __init__(self, shared_state: Any, pcm_player: Any):
        self.shared_state = shared_state
        self.player = pcm_player
        self._lock = threading.Lock()
        self._session_id = 0

    def _next_session(self) -> tuple[int, int]:
        with self._lock:
            self._session_id += 1
            session_id = self._session_id
        with self.shared_state.current_task_id.get_lock():
            self.shared_state.current_task_id.value += 1
            task_id = self.shared_state.current_task_id.value
        return session_id, task_id

    def start_new_session(self) -> tuple[int, int]:
        self.shared_state.stop_event.set()
        if self.player is not None:
            self.player.stop()
        session_id, task_id = self._next_session()
        self.drain_audio_queue()
        self.shared_state.stop_event.clear()
        return session_id, task_id

    def stop_current_session(self) -> None:
        self._next_session()
        self.shared_state.stop_event.set()
        if self.player is not None:
            self.player.stop()
        self.drain_audio_queue()

    def is_current(self, session_id: int, task_id: int | None = None) -> bool:
        with self._lock:
            session_matches = session_id == self._session_id
        if task_id is None:
            return session_matches
        return session_matches and task_id == self.shared_state.current_task_id.value

    def can_feed_audio(self, session_id: int, task_id: int) -> bool:
        return (
            not self.shared_state.stop_event.is_set()
            and self.is_current(session_id, task_id)
        )

    def drain_audio_queue(self) -> None:
        while True:
            try:
                self.shared_state.audio_q.get_nowait()
            except Exception:
                break

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            session_id = self._session_id
        return {
            "playback_session_id": session_id,
            "current_task_id": self.shared_state.current_task_id.value,
            "stop_event": self.shared_state.stop_event.is_set(),
            "audio_qsize": self._safe_qsize(self.shared_state.audio_q),
            "player_qsize": self._safe_qsize(self.player.audio_queue) if self.player else 0,
        }

    @staticmethod
    def _safe_qsize(q: Any) -> int:
        try:
            return q.qsize()
        except Exception:
            return -1


class PlaybackService:
    def __init__(
        self,
        *,
        shared_state: Any,
        player: Any,
        storage: Any,
        runtime_state: Any,
        sentinel: str,
        get_text_hash: Callable[[str], str],
        get_performance_profile: Callable[[str | None], dict[str, Any]],
        event_log: Any | None = None,
    ) -> None:
        self.shared_state = shared_state
        self.player = player
        self.storage = storage
        self.runtime_state = runtime_state
        self.sentinel = sentinel
        self.get_text_hash = get_text_hash
        self.get_performance_profile = get_performance_profile
        self.event_log = event_log
        self.controller = PlaybackController(shared_state, player)
        self._shutdown_event = threading.Event()
        self._thread_lock = threading.Lock()
        self._threads: set[threading.Thread] = set()

    def _start_thread(self, target: Callable, args: tuple[Any, ...], name: str) -> None:
        if self._shutdown_event.is_set():
            raise RuntimeError("playback service is shutting down")

        def run() -> None:
            try:
                target(*args)
            finally:
                with self._thread_lock:
                    self._threads.discard(threading.current_thread())

        thread = threading.Thread(target=run, name=name, daemon=True)
        with self._thread_lock:
            self._threads.add(thread)
        thread.start()

    def start_new_session(self) -> tuple[int, int]:
        session_id, task_id = self.controller.start_new_session()
        self._record_event("playback_session_started", session_id=session_id, task_id=task_id)
        return session_id, task_id

    def stop_current_session(self) -> None:
        self.controller.stop_current_session()
        self._record_event(
            "playback_session_stopped",
            current_task_id=self.shared_state.current_task_id.value,
        )

    def snapshot(self) -> dict[str, Any]:
        return self.controller.snapshot()

    def pause(self) -> None:
        self.player.pause()
        self._record_event("playback_paused")

    def resume(self) -> None:
        self.player.resume()
        self._record_event("playback_resumed")

    def restart_device(self) -> None:
        self.player.restart_device()
        self._record_event("audio_device_restarted")

    def start_tts_thread(
        self,
        *,
        session_id: int,
        task_id: int,
        start_idx: int,
        chunks: list[Any],
        config: dict[str, Any],
        state: dict[str, Any],
        is_podcast: bool = False,
    ) -> None:
        self._start_thread(
            self._shared_task_loop,
            (session_id, task_id, start_idx, chunks, config, state, is_podcast),
            "tts-playback",
        )
        self._record_event(
            "tts_thread_started",
            session_id=session_id,
            task_id=task_id,
            start_idx=start_idx,
            chunk_count=len(chunks),
            is_podcast=is_podcast,
        )

    def play_wav_file(self, filepath: str, filename: str) -> None:
        self.runtime_state.set_main(
            title="🎙️ " + filename.replace(".wav", "").replace("podcast_", ""),
            progress="",
            is_playing=True,
        )
        self.runtime_state.set_current_media(podcast=filename, md5=None)
        session_id, task_id = self.start_new_session()
        self.runtime_state.reset_podcast_generation()
        self._record_event(
            "wav_playback_started",
            session_id=session_id,
            task_id=task_id,
            filename=filename,
            filepath=filepath,
        )
        self._start_thread(
            self._play_wav_thread,
            (filepath, session_id, task_id),
            "wav-playback",
        )

    def _shared_task_loop(
        self,
        session_id: int,
        task_id: int,
        start_idx: int,
        chunks: list[Any],
        config: dict[str, Any],
        state: dict[str, Any],
        is_podcast: bool = False,
    ) -> None:
        profile = self.get_performance_profile(config.get("performance_profile"))
        buffer_high_sec = profile["buffer_high_sec"]
        buffer_low_sec = profile["buffer_low_sec"]
        try:
            if not is_podcast:
                self.player.start()
            self.shared_state.set_status("BUSY")
            for i in range(start_idx, len(chunks)):
                if self._shutdown_event.is_set() or not self.controller.can_feed_audio(
                    session_id, task_id
                ):
                    break
                self.runtime_state.set_main(progress=f"{i+1}/{len(chunks)}")
                chunk_text = chunks[i]
                if isinstance(chunk_text, dict):
                    chunk_config = config.copy()
                    chunk_config.update(chunk_text.get("config", {}))
                    actual_text = chunk_text["text"]
                    text_hash = self.get_text_hash(
                        actual_text + "_" + chunk_config.get("voice", "")
                    )
                else:
                    chunk_config = config
                    actual_text = chunk_text
                    text_hash = self.get_text_hash(actual_text)

                self.shared_state.text_q.put(
                    {
                        "task_id": task_id,
                        "text": actual_text,
                        "config": chunk_config,
                        "hash": text_hash,
                    }
                )
                if not is_podcast:
                    state["current_article"]["current_index"] = i
                    self.storage.save_state(state)

                if not is_podcast and self.player.get_queue_duration() > buffer_high_sec:
                    self.shared_state.set_status("COOLING")
                    while (
                        self.player.get_queue_duration() > buffer_low_sec
                        and not self._shutdown_event.is_set()
                        and self.controller.can_feed_audio(session_id, task_id)
                    ):
                        time.sleep(1.0)
                    self.shared_state.set_status("BUSY")
        finally:
            if self.controller.is_current(session_id, task_id):
                self.shared_state.text_q.put(self.sentinel)
                if not is_podcast:
                    self.player.wait_until_finished()
                    self.runtime_state.set_main(is_playing=False)
                self.shared_state.set_status("IDLE")
            self._record_event(
                "tts_thread_finished",
                session_id=session_id,
                task_id=task_id,
                is_current=self.controller.is_current(session_id, task_id),
            )

    def _play_wav_thread(self, path: str, session_id: int, task_id: int) -> None:
        try:
            import scipy.io.wavfile as wavfile

            sr, wav_data = wavfile.read(path)

            if len(wav_data.shape) == 1:
                wav_data = np.stack([wav_data, wav_data], axis=1)

            float_data = wav_data.astype(np.float32) / 32767.0
            chunk_size = sr * 2

            self.player.start()
            for i in range(0, len(float_data), chunk_size):
                if self._shutdown_event.is_set() or not self.controller.can_feed_audio(
                    session_id, task_id
                ):
                    break

                while (
                    self.player.audio_queue.qsize() > 5
                    and not self._shutdown_event.is_set()
                    and self.controller.can_feed_audio(session_id, task_id)
                ):
                    time.sleep(0.5)

                if not self.controller.can_feed_audio(session_id, task_id):
                    break

                self.player.play_chunk(float_data[i : i + chunk_size])

            if self.controller.is_current(session_id, task_id):
                self.player.signal_end_of_article()
        except Exception as e:
            print(f"[WavPlayer] Error: {e}")
            self._record_event(
                "wav_playback_failed",
                session_id=session_id,
                task_id=task_id,
                error=str(e),
            )
        finally:
            if self.controller.can_feed_audio(session_id, task_id):
                self.runtime_state.set_main(is_playing=False)
            self._record_event(
                "wav_playback_finished",
                session_id=session_id,
                task_id=task_id,
                is_current=self.controller.is_current(session_id, task_id),
            )

    def begin_shutdown(self) -> None:
        if self._shutdown_event.is_set():
            return
        self._shutdown_event.set()
        self.controller.stop_current_session()
        if self.player is not None:
            self.player.playback_finished_event.set()

    def shutdown(self, join_timeout: float = 2.0) -> None:
        self.begin_shutdown()

        with self._thread_lock:
            threads = list(self._threads)
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(join_timeout)

    def _record_event(self, event: str, **fields: Any) -> None:
        if self.event_log:
            self.event_log.record(event, **fields)
