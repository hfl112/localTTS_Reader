import hashlib
import json
import multiprocessing as mp
import os
import subprocess
import threading
import time
import traceback
from typing import Any

import numpy as np
import scipy.io.wavfile

from core.processor import TextProcessor
from core.services.performance import estimate_reading_minutes, get_performance_profile


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
    profile = get_performance_profile(podcast_config["performance_profile"])
    if force_small_model or estimate_reading_minutes(text) >= 20.0:
        podcast_config["model"] = profile.get("model") or "Qwen3-TTS-0.6B"
    return podcast_config


def wait_for_podcast_slot(pause_event, poll_sec: float) -> None:
    while pause_event.is_set():
        time.sleep(poll_sec)


def generate_podcast_chunks(
    engine: Any,
    text: str,
    config: dict[str, Any],
    chunk_dir: str,
    pause_event,
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
        chunk_file = os.path.join(chunk_dir, f"chunk_{idx:05d}.npy")
        chunk_files.append(chunk_file)
        if os.path.exists(chunk_file):
            continue

        wait_for_podcast_slot(pause_event, profile["podcast_pause_poll_sec"])
        if isinstance(chunk, dict):
            chunk_config = config.copy()
            chunk_config.update(chunk.get("config", {}))
            actual_text = chunk["text"]
        else:
            chunk_config = config
            actual_text = chunk

        parts = []
        for samples in engine.generate_stream(actual_text, chunk_config):
            parts.append(samples)
            time.sleep(profile["chunk_sleep"])

        if parts:
            np.save(chunk_file, np.concatenate(parts).astype(np.float32))
            with open(progress_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"completed_chunks": idx + 1, "total_chunks": len(chunks)},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        time.sleep(profile["sentence_sleep"])

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
    gpu_lock,
    podcasts_dir: str,
    podcast_chunk_dir: str,
    title: str | None = None,
) -> None:
    _configure_low_priority_process()

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
            chunk_files = generate_podcast_chunks(engine, text, config, chunk_dir, pause_event)
            out_name = f"podcast_单篇_{source}_{safe_title}_{md5[:8]}_{int(time.time())}.wav"
            write_podcast_wav_from_chunks(chunk_files, os.path.join(podcasts_dir, out_name))
    except Exception as e:
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
    gpu_lock,
    podcast_chunk_dir: str,
) -> None:
    _configure_low_priority_process()

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
            chunk_files = generate_podcast_chunks(engine, text, config, chunk_dir, pause_event)
            write_podcast_wav_from_chunks(chunk_files, filename)
    except Exception as e:
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
    ) -> None:
        self.podcasts_dir = podcasts_dir
        self.podcast_chunk_dir = podcast_chunk_dir
        self.runtime_state = runtime_state
        self.active_url_tasks = active_url_tasks
        self.pause_event = mp.Event()
        self.gpu_lock = mp.Lock()
        self.active_procs: list[mp.Process] = []
        self.active_tasks: dict[str, mp.Process] = {}
        threading.Thread(target=self._manager_loop, daemon=True).start()

    def _manager_loop(self) -> None:
        while True:
            runtime_snapshot = self.runtime_state.snapshot()
            has_frontend_activity = (
                runtime_snapshot["main_is_playing"] or len(self.active_url_tasks) > 0
            )
            self.runtime_state.update_activity_if_busy(has_frontend_activity)
            runtime_snapshot = self.runtime_state.snapshot()

            should_pause = (
                runtime_snapshot["main_is_playing"]
                or len(self.active_url_tasks) > 0
                or (time.time() - runtime_snapshot["last_active_time"] < 120)
                or is_on_battery_power()
            )

            if should_pause:
                if not self.pause_event.is_set():
                    self.pause_event.set()
            else:
                if self.pause_event.is_set():
                    self.pause_event.clear()
            time.sleep(2)

    def cleanup_finished(self) -> None:
        for md5, proc in list(self.active_tasks.items()):
            if not proc.is_alive():
                self.active_tasks.pop(md5, None)
        self.active_procs = [proc for proc in self.active_procs if proc.is_alive()]

    def is_generating(self, md5: str) -> bool:
        self.cleanup_finished()
        return md5 in self.active_tasks

    def start_single(
        self,
        *,
        text: str,
        config: dict[str, Any],
        md5: str,
        source: str,
        title: str | None,
    ) -> None:
        self.cleanup_finished()
        p = mp.Process(
            target=run_single_podcast_generation_thread,
            args=(
                text,
                config,
                md5,
                source,
                self.pause_event,
                self.gpu_lock,
                self.podcasts_dir,
                self.podcast_chunk_dir,
                title,
            ),
            daemon=True,
        )
        p.start()
        self.active_procs.append(p)
        self.active_tasks[md5] = p

    def start_batch(
        self,
        *,
        filename: str,
        text: str,
        config: dict[str, Any],
        md5: str,
    ) -> None:
        self.cleanup_finished()
        p = mp.Process(
            target=run_podcast_generation_thread,
            args=(filename, text, config, self.pause_event, self.gpu_lock, self.podcast_chunk_dir),
            daemon=True,
        )
        p.start()
        self.active_procs.append(p)
        self.active_tasks[md5] = p

    def cancel_all(self) -> None:
        for proc in self.active_procs:
            if proc.is_alive():
                try:
                    proc.terminate()
                except Exception:
                    pass
        self.active_procs.clear()
        self.active_tasks.clear()

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
            "on_battery_power": is_on_battery_power(),
            "active_podcast_processes": sum(1 for p in self.active_procs if p.is_alive()),
        }

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
