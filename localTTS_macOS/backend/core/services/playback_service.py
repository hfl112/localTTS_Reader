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
        import os
        from core.processor import TextProcessor

        # 1. 尝试寻找并读取同名 .txt 文件以提取段落
        chunks = []
        txt_path = (filepath[:-4] if filepath.endswith(".wav") else filepath) + ".txt"
        if os.path.exists(txt_path):
            try:
                with open(txt_path, "r", encoding="utf-8") as f:
                    text = f.read()
                # 使用 TextProcessor 切分句子
                processor = TextProcessor()
                chunks = processor.parse_dialogue_or_text(text)
            except Exception as e:
                print(f"[PlaybackService] Failed to read/parse txt sidecar: {e}")

        # 2. 如果成功读取到了文稿 chunks，写入当前状态以在 Dashboard 显示
        title = filename.replace(".wav", "").replace("podcast_", "")
        try:
            state = self.storage.load_state()
            state["current_article"] = {
                "title": title,
                "chunks": chunks,
                "current_index": 0
            }
            self.storage.save_state(state)
        except Exception as e:
            print(f"[PlaybackService] Failed to save current_article to storage: {e}")

        self.runtime_state.set_main(
            title="🎙️ " + title,
            progress=f"1/{len(chunks)}" if chunks else "1/1",
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
            (filepath, session_id, task_id, chunks, title),
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
                if is_podcast:
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
                        "chunk_index": i,
                        "text": actual_text,
                        "config": chunk_config,
                        "hash": text_hash,
                    }
                )

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

    def _persist_current_index(self, title: str, idx: int) -> bool:
        """Persist the live karaoke index to state.json for cross-restart resume.

        Only the playback thread calls this (it is the single writer of the live
        index); read-only endpoints never write. Returns True on success.
        """
        try:
            state = self.storage.load_state()
            article = state.get("current_article")
            if article and article.get("title") == title:
                article["current_index"] = idx
                self.storage.save_state(state)
                return True
        except Exception:
            pass
        return False

    def _play_wav_thread(self, path: str, session_id: int, task_id: int, chunks: list[str], title: str) -> None:
        try:
            import scipy.io.wavfile as wavfile

            sr, wav_data = wavfile.read(path)

            if len(wav_data.shape) == 1:
                wav_data = np.stack([wav_data, wav_data], axis=1)

            float_data = wav_data.astype(np.float32) / 32767.0
            chunk_size = sr * 2
            total_len = len(float_data)

            # 预计算字数与停顿自适应权重占比以实现精准加权估算，解决滚动速度与朗读速度不匹配的问题
            def calculate_virtual_weight(chunk: Any) -> float:
                import re
                if isinstance(chunk, dict):
                    raw_text = chunk.get("text", "")
                else:
                    raw_text = str(chunk)
                
                # 剔除无声控制标签/角色前缀
                clean_text = re.sub(r"\[[a-zA-Z0-9_\u4e00-\u9fa5]+\]\s*:", "", raw_text)
                clean_text = re.sub(r"Persona\s*Anchor\s*:\s*[a-zA-Z0-9_]+\.?\s*", "", clean_text, flags=re.IGNORECASE)
                
                # 提取特征
                num_zh = len(re.findall(r"[\u4e00-\u9fa50-9]", clean_text))
                num_en_words = len(re.findall(r"\b[a-zA-Z']+\b", clean_text))
                num_major_punc = len(re.findall(r"[。！？\.\?!]", clean_text))
                num_minor_punc = len(re.findall(r"[，；、,;]", clean_text))
                
                # 语速停顿折合权重
                w_zh = 1.0
                w_en = 1.8
                w_major = 5.0
                w_minor = 2.0
                
                weight = (num_zh * w_zh) + (num_en_words * w_en) + (num_major_punc * w_major) + (num_minor_punc * w_minor)
                return max(weight, 0.5)

            cum_ratios = []
            if chunks:
                weights = [calculate_virtual_weight(c) for c in chunks]
                total_weight = sum(weights)
                if total_weight > 0:
                    curr_sum = 0
                    for w in weights:
                        curr_sum += w
                        cum_ratios.append(curr_sum / total_weight)
                else:
                    cum_ratios = [(idx + 1) / len(chunks) for idx in range(len(chunks))]

            self.player.start()
            # 节流持久化：RuntimeState 是实时索引的内存权威，state.json 仅用于
            # 跨重启恢复，因此只在索引变化且距上次写入 >=1.5s 时落盘，避免每块写盘。
            last_persist_idx = -1
            last_persist_t = 0.0
            final_idx = 0
            for i in range(0, total_len, chunk_size):
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

                # 估算当前的句子索引并写入 state（使用字数加权二分/线性区间定位）
                if chunks and cum_ratios:
                    # 消除由于播放器队列提前缓存（通常缓存 5 个块约 10 秒音频）导致的严重文本超前偏差
                    pending_samples = self.player.audio_queue.qsize() * chunk_size
                    actual_i = max(0, i - pending_samples)
                    ratio = actual_i / total_len
                    curr_idx = 0
                    for idx, r in enumerate(cum_ratios):
                        if ratio <= r:
                            curr_idx = idx
                            break
                    curr_idx = min(curr_idx, len(chunks) - 1)
                    # 内存权威：每块即时更新（廉价、锁保护）
                    self.runtime_state.set_main(index=curr_idx, total=len(chunks))
                    final_idx = curr_idx
                    # 落盘：节流，仅供跨重启恢复
                    now = time.time()
                    if curr_idx != last_persist_idx and (now - last_persist_t) >= 1.5:
                        if self._persist_current_index(title, curr_idx):
                            last_persist_idx = curr_idx
                            last_persist_t = now

                self.player.play_chunk(float_data[i : i + chunk_size])

            # 收尾持久化最终索引（供下次 RESUME 精确恢复），不受节流间隔限制
            if last_persist_idx != final_idx:
                self._persist_current_index(title, final_idx)

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
