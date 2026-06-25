import hashlib
import json
import multiprocessing as mp
import os
import subprocess
import threading
import time
import traceback
import uuid
from typing import Any, Callable

import numpy as np
import scipy.io.wavfile

from core.processor import TextProcessor
from core.services.podcast_jobs import PodcastJobStore
from core.services.performance import estimate_reading_minutes, get_performance_profile
from core.services.runtime_supervisor import stop_process
from core.services.runtime_log import RuntimeEventLog

BATTERY_PODCAST_POLICIES = {"pause", "quiet", "allow"}


def is_on_battery_power() -> bool:
    try:
        result = subprocess.run(
            ["pmset", "-g", "batt"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        return "Battery Power" in result.stdout
    except Exception:
        return False


def prepare_podcast_config(
    config: dict[str, Any],
    text: str,
    force_small_model: bool = False,
) -> dict[str, Any]:
    podcast_config = config.copy()
    podcast_config["performance_profile"] = podcast_config.get("performance_profile", "quiet")
    if podcast_config.get("force_battery_quiet"):
        podcast_config["performance_profile"] = "quiet"
        podcast_config["model"] = "Qwen3-TTS-0.6B"
    profile = get_performance_profile(podcast_config["performance_profile"])
    if force_small_model or estimate_reading_minutes(text) >= 20.0:
        podcast_config["model"] = profile.get("model") or "Qwen3-TTS-0.6B"
    return podcast_config


def wait_for_podcast_slot(pause_event, shutdown_event, poll_sec: float) -> None:
    while pause_event.is_set():
        if shutdown_event.wait(poll_sec):
            raise RuntimeError("podcast generation canceled")


def generate_podcast_chunks(
    engine: Any,
    text: str,
    config: dict[str, Any],
    chunk_dir: str,
    pause_event,
    shutdown_event,
) -> list[str]:
    profile = get_performance_profile(config.get("performance_profile"))
    os.makedirs(chunk_dir, exist_ok=True)
    chunks = TextProcessor().parse_dialogue_or_text(
        text,
        performance_profile=config.get("performance_profile", "quiet"),
    )
    chunk_files: list[str] = []
    progress_path = os.path.join(chunk_dir, "progress.json")

    for idx, chunk in enumerate(chunks):
        if shutdown_event.is_set():
            raise RuntimeError("podcast generation canceled")
        chunk_file = os.path.join(chunk_dir, f"chunk_{idx:05d}.npy")
        chunk_files.append(chunk_file)
        if os.path.exists(chunk_file):
            continue

        wait_for_podcast_slot(
            pause_event,
            shutdown_event,
            profile["podcast_pause_poll_sec"],
        )
        if isinstance(chunk, dict):
            chunk_config = config.copy()
            chunk_config.update(chunk.get("config", {}))
            actual_text = chunk["text"]
        else:
            chunk_config = config
            actual_text = chunk

        parts = []
        for samples in engine.generate_stream(actual_text, chunk_config):
            if shutdown_event.is_set():
                raise RuntimeError("podcast generation canceled")
            parts.append(samples)
            if shutdown_event.wait(profile["chunk_sleep"]):
                raise RuntimeError("podcast generation canceled")

        if parts:
            np.save(chunk_file, np.concatenate(parts).astype(np.float32))
            with open(progress_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"completed_chunks": idx + 1, "total_chunks": len(chunks)},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        if shutdown_event.wait(profile["sentence_sleep"]):
            raise RuntimeError("podcast generation canceled")

    return chunk_files


def write_podcast_wav_from_chunks(chunk_files: list[str], output_path: str) -> bool:
    existing_chunks = [path for path in chunk_files if os.path.exists(path)]
    if not existing_chunks:
        return False
    audio_parts = [np.load(path) for path in existing_chunks]
    full_wav = np.concatenate(audio_parts)
    wav_data = (np.clip(full_wav, -1.0, 1.0) * 32767).astype(np.int16)
    scipy.io.wavfile.write(output_path, 24000, wav_data)
    return True


def _configure_low_priority_process() -> None:
    try:
        os.nice(19)
        print("[PodcastProcess] Nice level set to 19 (lowest priority)")
    except Exception as e:
        print(f"[PodcastProcess] Failed to set nice level: {e}")

    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
    os.environ["NUMEXPR_NUM_THREADS"] = "1"


def run_single_podcast_generation_thread(
    text: str,
    config: dict[str, Any],
    md5: str,
    source: str,
    pause_event,
    shutdown_event,
    gpu_lock,
    podcasts_dir: str,
    podcast_chunk_dir: str,
    jobs_file: str,
    job_id: str,
    event_log_path: str | None,
    title: str | None = None,
) -> None:
    _configure_low_priority_process()
    job_store = PodcastJobStore(jobs_file)
    event_log = RuntimeEventLog(event_log_path) if event_log_path else None
    job_store.update(job_id, status="running", pid=os.getpid())
    if event_log:
        event_log.record("podcast_job_running", job_id=job_id, md5=md5, pid=os.getpid())

    if title:
        safe_title = "".join(
            c for c in title if c.isalnum() or "\u4e00" <= c <= "\u9fff" or c in "[]_-"
        )
    else:
        safe_title = "".join(
            c for c in text[:20] if c.isalnum() or "\u4e00" <= c <= "\u9fff" or c in "[]_-"
        )
    if not safe_title:
        safe_title = "无标题"

    pending_file = os.path.join(podcasts_dir, f".pending_单篇_{source}_{safe_title}_{md5[:8]}")
    os.makedirs(os.path.dirname(pending_file), exist_ok=True)
    with open(pending_file, "w") as f:
        f.write(text[:20])
    try:
        with gpu_lock:
            from core.tts_engine import TTSEngine

            config = prepare_podcast_config(config, text)
            engine = TTSEngine(
                model_path=f"models/{config.get('model', 'Qwen3-TTS-1.7B-8bit')}",
                mlx_audio_path="../../mlx_audio",
            )
            engine.ensure_model_loaded()
            chunk_dir = os.path.join(podcast_chunk_dir, f"single_{md5[:12]}")
            chunk_files = generate_podcast_chunks(
                engine,
                text,
                config,
                chunk_dir,
                pause_event,
                shutdown_event,
            )
            out_name = f"podcast_单篇_{source}_{safe_title}_{md5[:8]}_{int(time.time())}.wav"
            output_path = os.path.join(podcasts_dir, out_name)
            if not write_podcast_wav_from_chunks(chunk_files, output_path):
                raise RuntimeError("no generated podcast chunks")
            job_store.update(job_id, status="done", output_path=output_path, error=None)
            if event_log:
                event_log.record("podcast_job_done", job_id=job_id, md5=md5, output_path=output_path)
    except Exception as e:
        job_store.update(job_id, status="failed", error=str(e))
        if event_log:
            event_log.record("podcast_job_failed", job_id=job_id, md5=md5, error=str(e))
        print(f"[PodcastProcess] Error: {e}")
        traceback.print_exc()
    finally:
        if os.path.exists(pending_file):
            os.remove(pending_file)


def run_podcast_generation_thread(
    filename: str,
    text: str,
    config: dict[str, Any],
    pause_event,
    shutdown_event,
    gpu_lock,
    podcast_chunk_dir: str,
    jobs_file: str,
    job_id: str,
    event_log_path: str | None,
) -> None:
    _configure_low_priority_process()
    job_store = PodcastJobStore(jobs_file)
    event_log = RuntimeEventLog(event_log_path) if event_log_path else None
    job_store.update(job_id, status="running", pid=os.getpid())
    if event_log:
        event_log.record("podcast_job_running", job_id=job_id, pid=os.getpid())

    pending_file = filename.replace(".wav", "") + ".pending_合集"
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(pending_file, "w") as f:
        f.write("pending")
    try:
        with gpu_lock:
            from core.tts_engine import TTSEngine

            config = prepare_podcast_config(config, text, force_small_model=True)
            engine = TTSEngine(
                model_path=f"models/{config.get('model', 'Qwen3-TTS-1.7B-8bit')}",
                mlx_audio_path="../../mlx_audio",
            )
            engine.ensure_model_loaded()
            batch_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
            chunk_dir = os.path.join(podcast_chunk_dir, f"batch_{batch_hash[:12]}")
            chunk_files = generate_podcast_chunks(
                engine,
                text,
                config,
                chunk_dir,
                pause_event,
                shutdown_event,
            )
            if not write_podcast_wav_from_chunks(chunk_files, filename):
                raise RuntimeError("no generated podcast chunks")
            job_store.update(job_id, status="done", output_path=filename, error=None)
            if event_log:
                event_log.record("podcast_job_done", job_id=job_id, output_path=filename)
    except Exception as e:
        job_store.update(job_id, status="failed", error=str(e))
        if event_log:
            event_log.record("podcast_job_failed", job_id=job_id, error=str(e))
        print(f"[PodcastProcess] Error: {e}")
    finally:
        if os.path.exists(pending_file):
            os.remove(pending_file)


class PodcastService:
    def __init__(
        self,
        *,
        podcasts_dir: str,
        podcast_chunk_dir: str,
        runtime_state: Any,
        active_url_tasks: dict[str, dict],
        jobs_file: str | None = None,
        event_log: RuntimeEventLog | None = None,
        is_frontend_active: Callable[[], bool] | None = None,
        is_device_switching: Callable[[], bool] | None = None,
        get_battery_policy: Callable[[], str] | None = None,
    ) -> None:
        self.podcasts_dir = podcasts_dir
        self.podcast_chunk_dir = podcast_chunk_dir
        self.runtime_state = runtime_state
        self.active_url_tasks = active_url_tasks
        self.is_frontend_active = is_frontend_active
        self.is_device_switching = is_device_switching
        self.get_battery_policy = get_battery_policy
        self.job_store = PodcastJobStore(
            jobs_file
            or os.path.join(os.path.dirname(self.podcast_chunk_dir), "podcast_jobs.json")
        )
        self.job_store.mark_unfinished_failed("backend restarted before job completed")
        self.event_log = event_log
        self.pause_event = mp.Event()
        self.worker_shutdown_event = mp.Event()
        self.gpu_lock = mp.Lock()
        self.active_procs: list[mp.Process] = []
        self.active_tasks: dict[str, mp.Process] = {}
        self.active_job_ids: dict[str, str] = {}
        self.last_pause_reason = "recent_activity"
        self.last_battery_policy = "pause"
        self._shutdown_event = threading.Event()
        self._manager_thread = threading.Thread(
            target=self._manager_loop,
            name="podcast-manager",
            daemon=True,
        )
        self._manager_thread.start()

    def _manager_loop(self) -> None:
        while not self._shutdown_event.is_set():
            should_pause, reason = self._pause_state()

            if should_pause:
                self.last_pause_reason = reason
                if not self.pause_event.is_set():
                    self.pause_event.set()
                    self._record_event("podcast_generation_paused", reason=reason)
            else:
                self.last_pause_reason = "none"
                if self.pause_event.is_set():
                    self.pause_event.clear()
                    self._record_event("podcast_generation_resumed")
            self._shutdown_event.wait(2)

    def _frontend_active(self) -> bool:
        if self.is_frontend_active is not None:
            try:
                return bool(self.is_frontend_active())
            except Exception:
                return True
        return bool(self.runtime_state.snapshot()["main_is_playing"])

    def _pause_state(self) -> tuple[bool, str]:
        if self.is_device_switching is not None:
            try:
                if self.is_device_switching():
                    return False, "device_switching"
            except Exception:
                pass

        frontend_active = self._frontend_active()
        url_active = len(self.active_url_tasks) > 0
        battery_policy = self._battery_policy()
        self.last_battery_policy = battery_policy
        self.runtime_state.update_activity_if_busy(frontend_active or url_active)
        runtime_snapshot = self.runtime_state.snapshot()

        if frontend_active:
            return True, "frontend_active"
        if url_active:
            return True, "url_active"
        if time.time() - runtime_snapshot["last_active_time"] < 120:
            return True, "recent_activity"
        if is_on_battery_power() and battery_policy == "pause":
            return True, "battery"
        return False, "none"

    def _battery_policy(self) -> str:
        if self.get_battery_policy is None:
            return "pause"
        try:
            policy = self.get_battery_policy()
        except Exception:
            return "pause"
        return policy if policy in BATTERY_PODCAST_POLICIES else "pause"

    def _apply_battery_policy_to_config(self, config: dict[str, Any]) -> dict[str, Any]:
        config = config.copy()
        policy = self._battery_policy()
        if is_on_battery_power() and policy == "quiet":
            config["performance_profile"] = "quiet"
            config["model"] = "Qwen3-TTS-0.6B"
            config["force_battery_quiet"] = True
            self._record_event("battery_quiet_policy_applied")
        return config

    def cleanup_finished(self) -> None:
        for md5, proc in list(self.active_tasks.items()):
            if not proc.is_alive():
                try:
                    proc.join(0)
                except (AssertionError, OSError, ValueError):
                    pass
                job_id = self.active_job_ids.pop(md5, None)
                if proc.exitcode not in (0, None):
                    self.job_store.update(
                        job_id,
                        status="failed",
                        error=f"process exited with code {proc.exitcode}",
                    )
                    self._record_event(
                        "podcast_process_failed",
                        md5=md5,
                        job_id=job_id,
                        exitcode=proc.exitcode,
                    )
                self.active_tasks.pop(md5, None)
        live_processes = []
        for proc in self.active_procs:
            if proc.is_alive():
                live_processes.append(proc)
            else:
                try:
                    proc.join(0)
                except (AssertionError, OSError, ValueError):
                    pass
        self.active_procs = live_processes

    def is_generating(self, md5: str) -> bool:
        self.cleanup_finished()
        return md5 in self.active_tasks or self.job_store.active_for_md5(md5)

    def start_single(
        self,
        *,
        text: str,
        config: dict[str, Any],
        md5: str,
        source: str,
        title: str | None,
    ) -> None:
        if self._shutdown_event.is_set():
            raise RuntimeError("podcast service is shutting down")
        self.cleanup_finished()
        config = self._apply_battery_policy_to_config(config)
        safe_title = title if title else (text[:20].replace("\n", " ") + "...")
        job_id = f"single_{md5[:12]}_{uuid.uuid4().hex[:8]}"
        self.job_store.create(
            job_id=job_id,
            kind="single",
            md5=md5,
            title=safe_title,
            source=source,
        )
        self._record_event(
            "podcast_job_queued",
            job_id=job_id,
            kind="single",
            md5=md5,
            title=safe_title,
            source=source,
        )
        p = mp.Process(
            target=run_single_podcast_generation_thread,
            args=(
                text,
                config,
                md5,
                source,
                self.pause_event,
                self.worker_shutdown_event,
                self.gpu_lock,
                self.podcasts_dir,
                self.podcast_chunk_dir,
                self.job_store.path,
                job_id,
                self.event_log.path if self.event_log else None,
                title,
            ),
            daemon=True,
        )
        p.start()
        self.active_procs.append(p)
        self.active_tasks[md5] = p
        self.active_job_ids[md5] = job_id

    def start_batch(
        self,
        *,
        filename: str,
        text: str,
        config: dict[str, Any],
        md5: str,
    ) -> None:
        if self._shutdown_event.is_set():
            raise RuntimeError("podcast service is shutting down")
        self.cleanup_finished()
        config = self._apply_battery_policy_to_config(config)
        job_id = f"batch_{md5[:12]}_{uuid.uuid4().hex[:8]}"
        self.job_store.create(
            job_id=job_id,
            kind="batch",
            md5=md5,
            title="大合集播客",
            source="web",
            output_path=filename,
        )
        self._record_event(
            "podcast_job_queued",
            job_id=job_id,
            kind="batch",
            md5=md5,
            output_path=filename,
        )
        p = mp.Process(
            target=run_podcast_generation_thread,
            args=(
                filename,
                text,
                config,
                self.pause_event,
                self.worker_shutdown_event,
                self.gpu_lock,
                self.podcast_chunk_dir,
                self.job_store.path,
                job_id,
                self.event_log.path if self.event_log else None,
            ),
            daemon=True,
        )
        p.start()
        self.active_procs.append(p)
        self.active_tasks[md5] = p
        self.active_job_ids[md5] = job_id

    def cancel_all(
        self,
        *,
        graceful_timeout: float = 0.0,
        terminate_timeout: float = 2.0,
    ) -> None:
        for proc in list(self.active_procs):
            stop_process(
                proc,
                graceful_timeout=graceful_timeout,
                terminate_timeout=terminate_timeout,
            )
        self.active_procs.clear()
        self.active_tasks.clear()
        self.active_job_ids.clear()
        self.job_store.cancel_active()
        self._record_event("podcast_jobs_canceled")

    def shutdown(
        self,
        *,
        graceful_timeout: float = 0.0,
        terminate_timeout: float = 2.0,
    ) -> None:
        self._shutdown_event.set()
        self.worker_shutdown_event.set()
        self.pause_event.clear()
        self.cancel_all(
            graceful_timeout=graceful_timeout,
            terminate_timeout=terminate_timeout,
        )
        if self._manager_thread is not threading.current_thread():
            self._manager_thread.join(terminate_timeout)

    def cleanup_pending_files(self) -> None:
        if not os.path.exists(self.podcasts_dir):
            return
        for filename in os.listdir(self.podcasts_dir):
            if ".pending_" in filename:
                try:
                    os.remove(os.path.join(self.podcasts_dir, filename))
                except Exception:
                    pass

    def snapshot(self) -> dict[str, Any]:
        self.cleanup_finished()
        return {
            "podcast_generation_paused": self.pause_event.is_set(),
            "podcast_generation_pause_reason": self.last_pause_reason,
            "battery_podcast_policy": self.last_battery_policy,
            "on_battery_power": is_on_battery_power(),
            "active_podcast_processes": sum(1 for p in self.active_procs if p.is_alive()),
            "podcast_jobs": self.job_store.list()[:20],
        }

    def list_jobs(self) -> list[dict[str, Any]]:
        self.cleanup_finished()
        return self.job_store.list()

    def search_dirs(self) -> list[str]:
        base_dir = os.path.dirname(self.podcasts_dir)
        app_dir = os.path.join(base_dir, "QwenTTS-App")
        return [
            self.podcasts_dir,
            os.path.join(app_dir, "data", "podcasts"),
            os.path.join(app_dir, "data", "exported"),
        ]

    def find_file(self, filename: str) -> str | None:
        safe_filename = os.path.basename(filename)
        for directory in self.search_dirs():
            candidate = os.path.join(directory, safe_filename)
            if os.path.exists(candidate):
                return candidate
        return None

    def list_files(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.podcasts_dir):
            return []
        files: list[dict[str, Any]] = []
        for filename in os.listdir(self.podcasts_dir):
            path = os.path.join(self.podcasts_dir, filename)
            is_pinned = "pinned_" in filename
            clean_filename = filename.replace("pinned_", "")
            parts = clean_filename.split("_")

            title = clean_filename
            source = "web"
            is_pending = ".pending_" in filename

            if len(parts) >= 5 and (parts[0] == "podcast" or parts[0] == ".pending"):
                if parts[1] == "单篇":
                    source = parts[2]
                    title = parts[3]
                elif parts[1] == "合集":
                    source = "web"
                    title = "大合集播客"

            if filename.endswith(".wav"):
                try:
                    size_mb = os.path.getsize(path) / (1024 * 1024)
                except Exception:
                    size_mb = 0
                files.append(
                    {
                        "title": title,
                        "filename": filename,
                        "timestamp": os.path.getmtime(path),
                        "is_pending": False,
                        "source": source,
                        "is_pinned": is_pinned,
                        "size_mb": size_mb,
                    }
                )
            elif is_pending:
                files.append(
                    {
                        "title": title + " (正在生成中...)",
                        "filename": filename,
                        "timestamp": os.path.getmtime(path),
                        "is_pending": True,
                        "source": source,
                        "is_pinned": False,
                    }
                )

        current_time = time.time()
        for url, info in list(self.active_url_tasks.items()):
            if info.get("is_podcast", False) and current_time - info["timestamp"] < 60:
                files.insert(
                    0,
                    {
                        "title": "⏳ 正在抓取网页正文...",
                        "filename": url,
                        "timestamp": info["timestamp"],
                        "is_pending": True,
                        "source": "web",
                        "is_pinned": False,
                        "size_mb": 0,
                    },
                )

        files.sort(key=lambda x: (not x["is_pinned"], -x["timestamp"]))
        return files

    def toggle_pin(self, filename: str) -> dict[str, Any]:
        filepath = self.find_file(filename)
        if not filepath:
            return {"error": "File not found"}

        dir_name = os.path.dirname(filepath)
        safe_filename = os.path.basename(filename)
        if "pinned_" in safe_filename:
            new_name = safe_filename.replace("pinned_", "")
        else:
            new_name = "pinned_" + safe_filename

        try:
            os.rename(filepath, os.path.join(dir_name, new_name))
            return {"status": "ok", "new_name": new_name}
        except Exception as e:
            return {"error": str(e)}

    def clear_unpinned(self) -> int:
        deleted_count = 0
        for directory in [self.podcasts_dir, os.path.join(os.path.dirname(self.podcasts_dir), "QwenTTS-App", "data", "podcasts")]:
            if os.path.exists(directory):
                for filename in os.listdir(directory):
                    if filename.endswith(".wav") and "pinned_" not in filename:
                        try:
                            os.remove(os.path.join(directory, filename))
                            deleted_count += 1
                        except Exception:
                            pass
        return deleted_count

    def delete(self, filename: str) -> dict[str, Any]:
        if not filename:
            return {"error": "Empty filename"}
        filepath = self.find_file(filename)
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
                return {"status": "ok"}
            except Exception as e:
                return {"error": f"Failed to delete file: {e}"}
        return {"error": "File not found"}

    def _record_event(self, event: str, **fields: Any) -> None:
        if self.event_log:
            self.event_log.record(event, **fields)
