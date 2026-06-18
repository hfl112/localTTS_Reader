import os
import sys
import time
import json
import threading
import multiprocessing as mp
import mlx.core as mx
import numpy as np
import scipy.io.wavfile
import hashlib
from fastapi import FastAPI, Body
from contextlib import asynccontextmanager
import uvicorn
import traceback
import signal
import re

# 确保能找到 core 目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

# 统一的播客目录（指向根目录下的 podcasts/ 目录）
PODCASTS_DIR = os.path.abspath(os.path.join(os.path.dirname(BASE_DIR), "podcasts"))
os.makedirs(PODCASTS_DIR, exist_ok=True)

from core.tts_engine import TTSEngine
from core.player import PCMPlayer
from core.processor import TextProcessor
from core.storage import Storage
from core.state.runtime_state import RuntimeState

CACHE_DIR = os.path.join(BASE_DIR, "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)
PODCAST_CHUNK_DIR = os.path.join(BASE_DIR, "data", "podcast_chunks")
os.makedirs(PODCAST_CHUNK_DIR, exist_ok=True)

# 终极同步信号：必须是字符串，确保跨进程一致
GLOBAL_SENTINEL = "PIPELINE_END_STRICT_V1"

PERFORMANCE_PROFILES = {
    "fast": {
        "chunk_sleep": 0.02,
        "sentence_sleep": 0.5,
        "buffer_high_sec": 30.0,
        "buffer_low_sec": 12.0,
        "podcast_pause_poll_sec": 1.0,
        "model": None,
    },
    "balanced": {
        "chunk_sleep": 0.08,
        "sentence_sleep": 1.5,
        "buffer_high_sec": 20.0,
        "buffer_low_sec": 8.0,
        "podcast_pause_poll_sec": 2.0,
        "model": None,
    },
    "quiet": {
        "chunk_sleep": 0.25,
        "sentence_sleep": 3.0,
        "buffer_high_sec": 10.0,
        "buffer_low_sec": 4.0,
        "podcast_pause_poll_sec": 3.0,
        "model": "Qwen3-TTS-0.6B",
    },
}

def get_performance_profile(name: str | None) -> dict:
    profile_name = name if name in PERFORMANCE_PROFILES else "balanced"
    profile = PERFORMANCE_PROFILES[profile_name].copy()
    profile["name"] = profile_name
    return profile

def estimate_reading_minutes(text: str) -> float:
    zh_chars = len([ch for ch in text if '\u4e00' <= ch <= '\u9fff'])
    en_words = len([w for w in re.split(r"\s+", text) if w.strip()])
    return (zh_chars / 250.0) + (en_words / 150.0)

def get_text_hash(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def manage_cache_limit(max_items=10):
    try:
        files = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR) if f.endswith('.npy')]
        if len(files) <= max_items: return
        files.sort(key=os.path.getmtime)
        for f in files[:-max_items]:
            os.remove(f)
            md5_val = os.path.basename(f).replace('.npy', '')
            try:
                storage.delete_cache_by_md5(md5_val)
            except: pass
    except: pass

def clear_all_cache():
    try:
        for f in os.listdir(CACHE_DIR): os.remove(os.path.join(CACHE_DIR, f))
        import sqlite3
        try:
            conn = sqlite3.connect(storage.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM cache_metadata")
            conn.commit()
            conn.close()
        except: pass
    except: pass

# ==========================================
# 1. 跨进程共享状态
# ==========================================
class SharedState:
    def __init__(self):
        self.text_q = mp.Queue()
        self.audio_q = mp.Queue()
        self.stop_event = mp.Event()
        self.vram_mb = mp.Value('d', 0.0)
        self.status_code = mp.Value('i', 0) # 0:IDLE, 1:BUSY, 2:COOLING
        self.current_task_id = mp.Value('i', 0)

    def set_status(self, status):
        m = {"IDLE": 0, "BUSY": 1, "COOLING": 2}
        self.status_code.value = m.get(status, 0)

    def get_status(self):
        m = {0: "IDLE", 1: "BUSY", 2: "COOLING"}
        return m.get(self.status_code.value, "IDLE")

# ==========================================
# 2. 推理子进程
# ==========================================
def inference_worker(shared_state):
    print(f"[InferenceProcess] 启动成功, PID: {os.getpid()}")
    engine = None
    last_active_time = time.time()
    
    def handle_signal(sig, frame):
        sys.exit(0)
    signal.signal(signal.SIGTERM, handle_signal)

    while True:
        try:
            shared_state.vram_mb.value = mx.get_active_memory() / 1024 / 1024
            
            try:
                task = shared_state.text_q.get(timeout=2)
                last_active_time = time.time()
            except mp.queues.Empty:
                if engine and engine.model is not None and (time.time() - last_active_time > 600):
                    print("[InferenceProcess] 空闲自动卸载模型...")
                    engine.model = None
                    mx.clear_cache()
                    import gc
                    gc.collect()
                continue

            if task is None: break
            
            # 识别可靠的字符串哨兵
            if isinstance(task, str) and task == GLOBAL_SENTINEL:
                shared_state.audio_q.put(GLOBAL_SENTINEL)
                continue

            if not isinstance(task, dict): continue

            # 校验 Task ID
            task_id = task.get('task_id', -1)
            if task_id != shared_state.current_task_id.value:
                continue

            config = task['config']
            profile = get_performance_profile(config.get("performance_profile"))
            target_model_name = config.get("model", "Qwen3-TTS-1.7B-8bit")
            target_path = f"models/{target_model_name}"

            if engine is None:
                engine = TTSEngine(model_path=target_path, mlx_audio_path="../../mlx_audio")
            
            if engine.abs_model_path != os.path.abspath(os.path.join(engine.base_dir, target_path)):
                print(f"[InferenceProcess] 模型切换 -> {target_model_name}")
                engine = TTSEngine(model_path=target_path, mlx_audio_path="../../mlx_audio")
            
            if engine.model is None:
                engine.ensure_model_loaded()
            
            # 针对 0.6B Base 模型的音色加固
            if "0.6B" in target_model_name:
                config["instruct"] = f"Persona Anchor: {config.get('voice', 'Serena')}. " + config.get("instruct", "")

            text = task['text']
            text_hash = task.get('hash')
            
            # 1. 检查缓存
            cache_file = os.path.join(CACHE_DIR, f"{text_hash}.npy") if text_hash else None
            if cache_file and os.path.exists(cache_file):
                try:
                    cached_audio = np.load(cache_file)
                    SR = 16000
                    for s in range(0, len(cached_audio), SR):
                        if shared_state.stop_event.is_set() or task_id != shared_state.current_task_id.value: break
                        shared_state.audio_q.put((task_id, cached_audio[s:s+SR]))
                        time.sleep(0.005)
                    shared_state.audio_q.put("CHUNK_DONE")
                    continue
                except: pass

            # 2. 实时推理
            full_audio = []
            throttle_sleep = profile["chunk_sleep"]

            for samples in engine.generate_stream(text, config):
                if shared_state.stop_event.is_set() or task_id != shared_state.current_task_id.value: 
                    break
                shared_state.audio_q.put((tid if 'tid' in locals() else task_id, samples))
                full_audio.append(samples)
                time.sleep(throttle_sleep)
            
            if not shared_state.stop_event.is_set() and task_id == shared_state.current_task_id.value and cache_file and full_audio:
                try:
                    concat_audio = np.concatenate(full_audio)
                    np.save(cache_file, concat_audio)
                    duration = len(concat_audio) / 24000.0
                    try:
                        storage.add_cache_metadata(
                            md5=text_hash,
                            text=text,
                            model=target_model_name,
                            voice=config.get("voice", "Serena"),
                            duration=duration,
                            file_path=cache_file
                        )
                    except Exception as db_err:
                        print(f"[InferenceProcess] Failed to save cache metadata: {db_err}")
                    manage_cache_limit(10)
                except Exception as save_err:
                    print(f"[InferenceProcess] Save cache failed: {save_err}")
            
            shared_state.audio_q.put("CHUNK_DONE")
            # 注意：子进程不再随意 set_status("IDLE")，防止监控误报
            
        except Exception as e:
            print(f"[InferenceProcess] 异常: {e}")
            traceback.print_exc()

# ==========================================
# 3. 主进程逻辑
# ==========================================
S = SharedState()
storage = Storage(data_dir=os.path.join(BASE_DIR, "data"))
# 强制播放器的 SENTINEL 与全局一致，只在主进程中初始化播放器，子进程（InferenceProcess）不加载 CoreAudio 硬件驱动
import multiprocessing
if multiprocessing.parent_process() is None:
    player = PCMPlayer(sample_rate=24000)
    player.SENTINEL = GLOBAL_SENTINEL
else:
    player = None

processor = TextProcessor()
save_file_lock = threading.Lock()
runtime_state = RuntimeState()

class PlaybackController:
    """Single owner for playback session invalidation and audio queue cleanup."""

    def __init__(self, shared_state: SharedState, pcm_player: PCMPlayer | None):
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

    def snapshot(self) -> dict:
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
    def _safe_qsize(q) -> int:
        try:
            return q.qsize()
        except Exception:
            return -1

playback_controller = PlaybackController(S, player)

def do_save_for_later(text: str, source: str = "web", voice: str | None = None, title: str | None = None) -> int:
    text = text.strip()
    if not text: return 0
    save_file = os.path.join(BASE_DIR, "data", "saved_for_later.json")
    saved_items = []
    md5_val = hashlib.md5(text.encode("utf-8")).hexdigest()
    
    with save_file_lock:
        if os.path.exists(save_file):
            try:
                with open(save_file, "r", encoding="utf-8") as f: saved_items = json.load(f)
            except: pass
            
        if not any(item.get("md5") == md5_val for item in saved_items):
            display_title = title if title else (text[:20].replace("\n", " ") + "...")
            saved_items.append({
                "timestamp": time.time(),
                "text": text,
                "title": display_title,
                "source": source,
                "voice": voice,
                "is_exported": False,
                "md5": md5_val
            })
            if len(saved_items) > 5: saved_items = saved_items[-5:]
            with open(save_file, "w", encoding="utf-8") as f: json.dump(saved_items, f, ensure_ascii=False, indent=2)
            
    return len(saved_items)

def shared_task_loop(session_id, tid, start_idx, chunks, config, state, is_podcast=False):
    profile = get_performance_profile(config.get("performance_profile"))
    buffer_high_sec = profile["buffer_high_sec"]
    buffer_low_sec = profile["buffer_low_sec"]
    try:
        if not is_podcast:
            player.start()
        S.set_status("BUSY")
        for i in range(start_idx, len(chunks)):
            if not playback_controller.can_feed_audio(session_id, tid): break
            runtime_state.set_main(progress=f"{i+1}/{len(chunks)}")
            chunk_text = chunks[i]
            if isinstance(chunk_text, dict):
                chunk_config = config.copy()
                chunk_config.update(chunk_text.get('config', {}))
                actual_text = chunk_text['text']
                text_hash = get_text_hash(actual_text + "_" + chunk_config.get("voice", ""))
            else:
                chunk_config = config
                actual_text = chunk_text
                text_hash = get_text_hash(actual_text)
            
            S.text_q.put({'task_id': tid, 'text': actual_text, 'config': chunk_config, 'hash': text_hash})
            if not is_podcast:
                state["current_article"]["current_index"] = i
                storage.save_state(state)
            
            if not is_podcast and player.get_queue_duration() > buffer_high_sec:
                S.set_status("COOLING")
                while player.get_queue_duration() > buffer_low_sec and playback_controller.can_feed_audio(session_id, tid):
                    time.sleep(1.0)
                S.set_status("BUSY")
    finally:
        if playback_controller.is_current(session_id, tid):
            S.text_q.put(GLOBAL_SENTINEL)
            if not is_podcast:
                player.wait_until_finished()
                runtime_state.set_main(is_playing=False)
            S.set_status("IDLE")

def performance_monitor_thread():
    import psutil
    process = psutil.Process(os.getpid())
    print("[Monitor] 性能监控就绪")
    last_status = "IDLE"
    while True:
        try:
            st = S.get_status()
            if runtime_state.main_is_playing and st == "IDLE": st = "PLAYING"
            
            if st == "IDLE" and last_status != "IDLE":
                print(f"--- [DIAGNOSE] 任务已结束 (ID: {S.current_task_id.value}) ---\n")
            
            last_status = st
            if st == "IDLE":
                time.sleep(2)
                continue

            cpu = process.cpu_percent(interval=None) 
            log_msg = (
                f"--- [DIAGNOSE] ---\n"
                f"Task ID: {S.current_task_id.value} | Status: {st}\n"
                f"CPU: {cpu}% | VRAM: {S.vram_mb.value:.1f}MB\n"
                f"Buffer: {player.audio_queue.qsize() * (2048/24000):.1f}s\n"
                f"------------------\n"
            )
            print(log_msg)
            time.sleep(5)
        except: time.sleep(5)

def audio_feeder_thread():
    while True:
        try:
            item = S.audio_q.get()
            if item is None: break
            if isinstance(item, str) and item == GLOBAL_SENTINEL:
                snapshot = runtime_state.snapshot()
                if snapshot["podcast_file"]:
                    try:
                        podcast_file, podcast_buffer = runtime_state.consume_podcast_buffer()
                        if podcast_file and podcast_buffer:
                            wav_data = np.concatenate(podcast_buffer)
                            wav_data = (np.clip(wav_data, -1.0, 1.0) * 32767).astype(np.int16)
                            scipy.io.wavfile.write(podcast_file, 24000, wav_data)
                            print(f"[Podcast] Saved to {podcast_file}")
                            
                            save_file = os.path.join(BASE_DIR, "data", "saved_for_later.json")
                            with save_file_lock:
                                with open(save_file, "w", encoding="utf-8") as f:
                                    json.dump([], f)
                    except Exception as e:
                        print(f"[Podcast] Error saving: {e}")
                else:
                    player.signal_end_of_article()
                continue
            
            if isinstance(item, tuple) and len(item) == 2:
                tid, samples = item
                if tid == S.current_task_id.value:
                    if runtime_state.snapshot()["podcast_file"] is not None:
                        runtime_state.append_podcast_audio(samples)
                    else:
                        player.play_chunk(samples)
        except: pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    clear_all_cache()
    mp.set_start_method('spawn', force=True)
    p = mp.Process(target=inference_worker, args=(S,), daemon=True)
    p.start()
    threading.Thread(target=audio_feeder_thread, daemon=True).start()
    threading.Thread(target=performance_monitor_thread, daemon=True).start()
    yield
    print("[Backend] lifespan正在进行资源清理并终止播放器...")
    p.terminate()
    if player is not None:
        try:
            player.close()
        except Exception as e:
            print(f"[Backend] 关闭播放器异常: {e}")
    clear_all_cache()
    S.text_q.put(None)

app = FastAPI(lifespan=lifespan)

@app.post("/read")
async def read_text(data: dict = Body(...)):
    text = data.get('text', "")
    voice = data.get("voice", None)
    source = data.get("source", None)
    
    runtime_state.clear_current_media(keep_md5=data.get("from_saved", False))
    runtime_state.reset_podcast_generation()
    
    playback_session_id, new_task_id = playback_controller.start_new_session()
    
    state = storage.load_state()
    config = storage.load_config()
    config["performance_profile"] = data.get("performance_profile", config.get("performance_profile", "balanced"))
    if voice:
        config["voice"] = voice
        
    if text == "RESUME_MODE":
        current_art = state.get("current_article", {})
        chunks = current_art.get("chunks", [])
        curr_idx = current_art.get("current_index", 0)
    else:
        if source == "clipboard" and text:
            try:
                do_save_for_later(text, source="clipboard", voice=voice)
            except Exception as e:
                print(f"[Backend] Auto-saving clipboard text failed: {e}")
                
        chunks = processor.parse_dialogue_or_text(text, performance_profile=config["performance_profile"])
        state["current_article"] = {"title": text[:15].replace("\n", " ") + "...", "chunks": chunks, "current_index": 0}
        storage.save_state(state)
        curr_idx = 0
    
    runtime_state.set_main(title=state["current_article"]["title"], is_playing=True)
    
    threading.Thread(target=shared_task_loop, args=(playback_session_id, new_task_id, curr_idx, chunks, config, state), daemon=True).start()
    return {"status": "ok"}

@app.post("/stop")
async def stop_read():
    global ACTIVE_PODCAST_PROCS, ACTIVE_PODCAST_TASKS
    
    runtime_state.clear_current_media()
    runtime_state.reset_podcast_generation()
    
    playback_controller.stop_current_session()
    runtime_state.set_main(is_playing=False)
    S.set_status("IDLE")
    
    # Cancel all active podcast generation processes
    for p in ACTIVE_PODCAST_PROCS:
        if p.is_alive():
            try: p.terminate()
            except: pass
    ACTIVE_PODCAST_PROCS.clear()
    ACTIVE_PODCAST_TASKS.clear()
    
    # Cleanup pending podcast files
    if os.path.exists(PODCASTS_DIR):
        for f in os.listdir(PODCASTS_DIR):
            if ".pending_" in f:
                try: os.remove(os.path.join(PODCASTS_DIR, f))
                except: pass
                
    return {"status": "ok"}

@app.get("/status")
async def get_status():
    runtime_snapshot = runtime_state.snapshot()
    generating_title = ""
    if os.path.exists(PODCASTS_DIR):
        try:
            for f in os.listdir(PODCASTS_DIR):
                if f.startswith(".pending_单篇_"):
                    parts = f.split("_")
                    if len(parts) >= 4:
                        generating_title = parts[3]
                        break
                elif f.startswith(".pending_合集_"):
                    generating_title = "大合集播客"
                    break
        except:
            pass
            
    status_code = S.get_status()
    if generating_title and status_code == "IDLE":
        status_code = "BUSY"
        
    return {
        "is_playing": runtime_snapshot["main_is_playing"] and not player.is_paused,
        "is_paused": player.is_paused,
        "current_podcast_file": runtime_snapshot["current_podcast_file"],
        "current_playing_md5": runtime_snapshot["current_playing_md5"],
        "title": runtime_snapshot["main_title"],
        "progress": runtime_snapshot["main_progress"],
        "buffer_sec": player.get_queue_duration(),
        "status_code": status_code,
        "generating_title": generating_title
    }

@app.get("/debug/state")
async def debug_state():
    runtime_snapshot = runtime_state.snapshot()
    return {
        **playback_controller.snapshot(),
        "status_code": S.get_status(),
        **runtime_snapshot,
        "podcast_generation_paused": GLOBAL_PAUSE_EVENT.is_set(),
        "on_battery_power": is_on_battery_power(),
        "active_url_tasks": list(ACTIVE_URL_TASKS.keys()),
        "active_podcast_processes": sum(1 for p in ACTIVE_PODCAST_PROCS if p.is_alive()),
    }

@app.post("/pause")
async def pause_playback():
    player.pause()
    return {"status": "paused"}

@app.post("/resume")
async def resume_playback():
    player.resume()
    return {"status": "resumed"}

@app.post("/restart_audio")
async def restart_audio():
    if player is not None:
        try:
            player.restart_device()
            return {"status": "ok"}
        except Exception as e:
            return {"error": str(e)}
    return {"error": "Player not initialized"}

@app.post("/seek")
async def seek_playback(data: dict = Body(...)):
    direction = data.get("direction", 1) # 1 for next, -1 for prev
    
    runtime_state.reset_podcast_generation()
    
    state = storage.load_state()
    current_art = state.get("current_article", {})
    chunks = current_art.get("chunks", [])
    
    if not chunks:
        return {"error": "No active article"}
        
    curr = current_art.get("current_index", 0)
        
    new_idx = curr + direction
    if new_idx < 0: new_idx = 0
    if new_idx >= len(chunks): new_idx = len(chunks) - 1
    
    # Update state
    state["current_article"]["current_index"] = new_idx
    storage.save_state(state)
    
    # Restart playback at new index by triggering the read flow but simulating "RESUME_MODE"
    # We must stop current task first
    playback_session_id, new_task_id = playback_controller.start_new_session()
    
    runtime_state.set_main(is_playing=True)
    config = storage.load_config()
    config["performance_profile"] = config.get("performance_profile", "balanced")
    
    threading.Thread(target=shared_task_loop, args=(playback_session_id, new_task_id, new_idx, chunks, config, state), daemon=True).start()
    return {"status": "seeking", "new_index": new_idx}

import asyncio
import subprocess

ACTIVE_URL_TASKS: dict[str, dict] = {}
ACTIVE_PODCAST_PROCS = []
ACTIVE_PODCAST_TASKS = {}

GLOBAL_PAUSE_EVENT = mp.Event()
GLOBAL_PODCAST_GPU_LOCK = mp.Lock()

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

def podcast_manager_loop():
    while True:
        # Update last active time if anything is actively playing or URL is being fetched
        runtime_snapshot = runtime_state.snapshot()
        has_frontend_activity = runtime_snapshot["main_is_playing"] or len(ACTIVE_URL_TASKS) > 0
        runtime_state.update_activity_if_busy(has_frontend_activity)
        runtime_snapshot = runtime_state.snapshot()
            
        should_pause = (
            runtime_snapshot["main_is_playing"]
            or len(ACTIVE_URL_TASKS) > 0
            or (time.time() - runtime_snapshot["last_active_time"] < 120)
            or is_on_battery_power()
        )

        if should_pause:
            if not GLOBAL_PAUSE_EVENT.is_set():
                GLOBAL_PAUSE_EVENT.set()
        else:
            if GLOBAL_PAUSE_EVENT.is_set():
                GLOBAL_PAUSE_EVENT.clear()
        time.sleep(2)

threading.Thread(target=podcast_manager_loop, daemon=True).start()

def prepare_podcast_config(config: dict, text: str, force_small_model: bool = False) -> dict:
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
    engine: TTSEngine,
    text: str,
    config: dict,
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
            chunk_config.update(chunk.get('config', {}))
            actual_text = chunk['text']
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

def run_single_podcast_generation_thread(text: str, config: dict, md5: str, source: str, pause_event, gpu_lock, title: str = None) -> None:
    import traceback
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

    if title:
        safe_title = "".join(c for c in title if c.isalnum() or '\u4e00' <= c <= '\u9fff' or c in '[]_-')
    else:
        safe_title = "".join(c for c in text[:20] if c.isalnum() or '\u4e00' <= c <= '\u9fff' or c in '[]_-')
    if not safe_title: safe_title = "无标题"
    
    pending_file = os.path.join(PODCASTS_DIR, f".pending_单篇_{source}_{safe_title}_{md5[:8]}")
    os.makedirs(os.path.dirname(pending_file), exist_ok=True)
    with open(pending_file, "w") as f: f.write(text[:20])
    try:
        with gpu_lock:
            config = prepare_podcast_config(config, text)
            engine = TTSEngine(model_path=f"models/{config.get('model', 'Qwen3-TTS-1.7B-8bit')}", mlx_audio_path="../../mlx_audio")
            engine.ensure_model_loaded()
            chunk_dir = os.path.join(PODCAST_CHUNK_DIR, f"single_{md5[:12]}")
            chunk_files = generate_podcast_chunks(engine, text, config, chunk_dir, pause_event)
            out_name = f"podcast_单篇_{source}_{safe_title}_{md5[:8]}_{int(time.time())}.wav"
            write_podcast_wav_from_chunks(chunk_files, os.path.join(PODCASTS_DIR, out_name))
    except Exception as e:
        print(f"[PodcastProcess] Error: {e}")
        traceback.print_exc()
    finally:
        if os.path.exists(pending_file): os.remove(pending_file)

def run_podcast_generation_thread(filename: str, text: str, config: dict, pause_event, gpu_lock) -> None:
    import traceback
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

    pending_file = filename.replace(".wav", "") + ".pending_合集"
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(pending_file, "w") as f: f.write("pending")
    try:
        with gpu_lock:
            config = prepare_podcast_config(config, text, force_small_model=True)
            engine = TTSEngine(model_path=f"models/{config.get('model', 'Qwen3-TTS-1.7B-8bit')}", mlx_audio_path="../../mlx_audio")
            engine.ensure_model_loaded()
            batch_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
            chunk_dir = os.path.join(PODCAST_CHUNK_DIR, f"batch_{batch_hash[:12]}")
            chunk_files = generate_podcast_chunks(engine, text, config, chunk_dir, pause_event)
            write_podcast_wav_from_chunks(chunk_files, filename)
    except Exception as e:
        print(f"[PodcastProcess] Error: {e}")
    finally:
        if os.path.exists(pending_file): os.remove(pending_file)

@app.post("/read_url")
async def read_url(payload: dict = Body(...)) -> dict:
    global ACTIVE_URL_TASKS
    url = payload.get("url", "").strip()
    html = payload.get("html", "").strip()
    translate = payload.get("translate", False)
    mode = payload.get("mode", "original")
    
    # Fallback compatibility for older client payloads
    if mode == "original" and translate:
        mode = "translate"
        
    save = payload.get("save", False)
    podcast = payload.get("podcast", False)
    if not url: return {"error": "Empty URL"}
        
    current_time = time.time()
    ACTIVE_URL_TASKS = {u: t for u, t in ACTIVE_URL_TASKS.items() if current_time - t["timestamp"] < 60}
    if url in ACTIVE_URL_TASKS:
        return {"status": "error", "message": "该网页正处于后台解析抓取中，请不要重复点击，稍候可在下方收藏列表中查看！"}
        
    ACTIVE_URL_TASKS[url] = {"timestamp": current_time, "is_podcast": podcast}
    
    cli_path = os.path.join(os.path.dirname(BASE_DIR), "URL-Reader", "read_url_cli.py")
    cmd = [sys.executable, cli_path, url]
    
    # Save uploaded HTML to temporary file if available
    temp_html_path = None
    if html:
        try:
            import tempfile
            import uuid
            temp_dir = tempfile.gettempdir()
            temp_html_path = os.path.join(temp_dir, f"qwentts_upload_{uuid.uuid4().hex}.html")
            with open(temp_html_path, "w", encoding="utf-8") as f:
                f.write(html)
            cmd.extend(["--html-file", temp_html_path])
        except Exception as e:
            print(f"[Backend] Failed to save uploaded HTML: {e}")
            
    if mode == "translate":
        cmd.append("-t")
    elif mode == "podcast-trans":
        cmd.append("-pt")
    elif mode == "podcast-discuss":
        cmd.append("-pd")
        
    if save: cmd.append("--save")
    if podcast: cmd.append("--podcast")
    
    async def run_cli_task():
        try:
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.wait()
        finally:
            ACTIVE_URL_TASKS.pop(url, None)
            if temp_html_path and os.path.exists(temp_html_path):
                try: os.remove(temp_html_path)
                except: pass
            
    asyncio.create_task(run_cli_task())
    return {"status": "ok", "message": "Read URL task dispatched"}

@app.post("/delete_saved")
async def delete_saved(data: dict = Body(...)):
    md5 = data.get("md5")
    save_file = os.path.join(BASE_DIR, "data", "saved_for_later.json")
    
    with save_file_lock:
        if os.path.exists(save_file):
            try:
                with open(save_file, "r", encoding="utf-8") as f: items = json.load(f)
            except:
                items = []
                
            if md5:
                new_items = [item for item in items if item.get("md5") != md5]
                if len(new_items) < len(items):
                    with open(save_file, "w", encoding="utf-8") as f: json.dump(new_items, f, ensure_ascii=False, indent=2)
                    return {"status": "ok"}
            else:
                index = data.get("index")
                if index is not None and 0 <= index < len(items):
                    items.pop(index)
                    with open(save_file, "w", encoding="utf-8") as f: json.dump(items, f, ensure_ascii=False, indent=2)
                    return {"status": "ok"}
                    
    return {"error": "Item not found"}

@app.get("/podcasts/list")
async def list_podcasts():
    podcasts_dir = PODCASTS_DIR
    if not os.path.exists(podcasts_dir): return []
    files = []
    for f in os.listdir(podcasts_dir):
        path = os.path.join(podcasts_dir, f)
        is_pinned = "pinned_" in f
        
        clean_f = f.replace("pinned_", "")
        parts = clean_f.split("_")
        
        title = clean_f
        source = "web"
        is_pending = ".pending_" in f
        
        if len(parts) >= 5 and (parts[0] == "podcast" or parts[0] == ".pending"):
            # Format: podcast_单篇_source_title_hash_timestamp.wav
            if parts[1] == "单篇":
                source = parts[2]
                title = parts[3]
            elif parts[1] == "合集":
                source = "web"
                title = "大合集播客"
                
        if f.endswith(".wav"):
            try: size_mb = os.path.getsize(path) / (1024 * 1024)
            except: size_mb = 0
            files.append({"title": title, "filename": f, "timestamp": os.path.getmtime(path), "is_pending": False, "source": source, "is_pinned": is_pinned, "size_mb": size_mb})
        elif is_pending:
            files.append({"title": title + " (正在生成中...)", "filename": f, "timestamp": os.path.getmtime(path), "is_pending": True, "source": source, "is_pinned": False})
    
    current_time = time.time()
    for url, info in list(ACTIVE_URL_TASKS.items()):
        if info.get("is_podcast", False) and current_time - info["timestamp"] < 60:
            files.insert(0, {
                "title": "⏳ 正在抓取网页正文...",
                "filename": url,
                "timestamp": info["timestamp"],
                "is_pending": True,
                "source": "web",
                "is_pinned": False,
                "size_mb": 0
            })
            
    # Sort by pinned (True first), then timestamp descending
    files.sort(key=lambda x: (not x["is_pinned"], -x["timestamp"]))
    return files

@app.post("/podcasts/toggle_pin")
async def toggle_pin(data: dict = Body(...)):
    filename = data.get("filename", "")
    
    search_dirs = [
        PODCASTS_DIR,
        os.path.join(BASE_DIR, "data", "podcasts"),
        os.path.join(os.path.dirname(BASE_DIR), "podcasts"),
        os.path.join(BASE_DIR, "data", "exported")
    ]
    
    filepath = None
    for d in search_dirs:
        candidate = os.path.join(d, filename)
        if os.path.exists(candidate):
            filepath = candidate
            break
            
    if not filepath:
        return {"error": "File not found"}
        
    dir_name = os.path.dirname(filepath)
    is_pinned = "pinned_" in filename
    if is_pinned:
        new_name = filename.replace("pinned_", "")
    else:
        new_name = "pinned_" + filename
        
    new_path = os.path.join(dir_name, new_name)
    try:
        os.rename(filepath, new_path)
        return {"status": "ok", "new_name": new_name}
    except Exception as e:
        return {"error": str(e)}

@app.post("/podcasts/clear")
async def clear_podcasts():
    # 同时清理统一播客文件夹和其它备份文件夹下的未置顶播客
    deleted_count = 0
    for podcasts_dir in [PODCASTS_DIR, os.path.join(BASE_DIR, "data", "podcasts")]:
        if os.path.exists(podcasts_dir):
            for f in os.listdir(podcasts_dir):
                if f.endswith(".wav") and "pinned_" not in f:
                    try:
                        os.remove(os.path.join(podcasts_dir, f))
                        deleted_count += 1
                    except: pass
    return {"status": "ok", "deleted_count": deleted_count}

@app.post("/podcasts/delete")
async def delete_podcast(data: dict = Body(...)):
    filename = data.get("filename", "")
    if not filename: return {"error": "Empty filename"}
    
    safe_filename = os.path.basename(filename)
    search_dirs = [
        PODCASTS_DIR,
        os.path.join(BASE_DIR, "data", "podcasts"),
        os.path.join(os.path.exported_dir if hasattr(os, "exported_dir") else os.path.join(BASE_DIR, "data", "exported"), "exported") # keep fallback
    ]
    # Simple direct search list
    search_dirs = [
        PODCASTS_DIR,
        os.path.join(BASE_DIR, "data", "podcasts"),
        os.path.join(BASE_DIR, "data", "exported")
    ]
    
    filepath = None
    for d in search_dirs:
        candidate = os.path.join(d, safe_filename)
        if os.path.exists(candidate):
            filepath = candidate
            break
            
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
            return {"status": "ok"}
        except Exception as e:
            return {"error": f"Failed to delete file: {e}"}
    return {"error": "File not found"}

@app.post("/podcasts/play")
async def play_podcast(data: dict = Body(...)):
    filename = data.get("filename", "")
    
    search_dirs = [
        PODCASTS_DIR,
        os.path.join(BASE_DIR, "data", "podcasts"),
        os.path.join(BASE_DIR, "data", "exported")
    ]
    
    filepath = None
    for d in search_dirs:
        candidate = os.path.join(d, filename)
        if os.path.exists(candidate):
            filepath = candidate
            break
            
    if not filepath:
        return {"error": "File not found"}
    
    runtime_state.set_main(
        title="🎙️ " + filename.replace(".wav", "").replace("podcast_", ""),
        progress="",
        is_playing=True,
    )
    runtime_state.set_current_media(podcast=filename, md5=None)
    
    playback_session_id, playback_task_id = playback_controller.start_new_session()
    
    runtime_state.reset_podcast_generation()

    def play_wav_thread(path, session_id, task_id):
        try:
            import scipy.io.wavfile as wavfile
            import numpy as np
            sr, wav_data = wavfile.read(path)
            
            # handle mono to stereo
            if len(wav_data.shape) == 1:
                wav_data = np.stack([wav_data, wav_data], axis=1)
                
            float_data = wav_data.astype(np.float32) / 32767.0
            chunk_size = sr * 2 # 2 seconds chunks
            
            player.start()
            for i in range(0, len(float_data), chunk_size):
                if not playback_controller.can_feed_audio(session_id, task_id):
                    break
                
                # Keep audio queue small to avoid memory bloat and allow quick stop
                while player.audio_queue.qsize() > 5 and playback_controller.can_feed_audio(session_id, task_id):
                    time.sleep(0.5)
                    
                if not playback_controller.can_feed_audio(session_id, task_id):
                    break
                    
                chunk = float_data[i:i+chunk_size]
                player.play_chunk(chunk)
                
            if playback_controller.is_current(session_id, task_id):
                player.signal_end_of_article()
        except Exception as e:
            print(f"[WavPlayer] Error: {e}")
        finally:
            if playback_controller.can_feed_audio(session_id, task_id):
                runtime_state.set_main(is_playing=False)
            
    threading.Thread(target=play_wav_thread, args=(filepath, playback_session_id, playback_task_id), daemon=True).start()
    return {"status": "ok"}

@app.post("/save_for_later")
async def save_for_later(data: dict = Body(...)):
    runtime_state.touch_activity()
    text = data.get("text", "").strip()
    source = data.get("source", "web")
    voice = data.get("voice", None)
    title = data.get("title", None)
    if not text: return {"error": "Empty text"}
    
    count = do_save_for_later(text, source, voice, title)
    return {"status": "saved", "count": count}

@app.post("/generate_single_podcast")
async def generate_single_podcast(data: dict = Body(...)):
    global ACTIVE_PODCAST_PROCS, ACTIVE_PODCAST_TASKS
    runtime_state.touch_activity()
    
    text = data.get("text", "").strip()
    source = data.get("source", "web")
    voice = data.get("voice", None)
    title = data.get("title", None)
    if not text: return {"error": "Empty text"}
    
    md5_val = hashlib.md5(text.encode("utf-8")).hexdigest()
    
    # 过滤并清理已结束的任务进程
    for m, proc in list(ACTIVE_PODCAST_TASKS.items()):
        if not proc.is_alive():
            ACTIVE_PODCAST_TASKS.pop(m, None)
            
    # 如果检测到相同内容的任务已在生成中，直接返回成功状态（不需要重复排队）
    if md5_val in ACTIVE_PODCAST_TASKS:
        return {"status": "generating", "md5": md5_val, "message": "该内容已在后台生成中，无需重复提交！"}
        
    config = storage.load_config()
    config["performance_profile"] = data.get("performance_profile", "quiet")
    if voice:
        config["voice"] = voice
    p = mp.Process(target=run_single_podcast_generation_thread, args=(text, config, md5_val, source, GLOBAL_PAUSE_EVENT, GLOBAL_PODCAST_GPU_LOCK, title), daemon=True)
    p.start()
    
    ACTIVE_PODCAST_PROCS = [proc for proc in ACTIVE_PODCAST_PROCS if proc.is_alive()]
    ACTIVE_PODCAST_PROCS.append(p)
    ACTIVE_PODCAST_TASKS[md5_val] = p
    
    async def cleanup(proc, m):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, proc.join)
        ACTIVE_PODCAST_TASKS.pop(m, None)
    asyncio.create_task(cleanup(p, md5_val))
    
    return {"status": "generating", "md5": md5_val}

@app.post("/saved_items/clear")
async def clear_saved_items():
    save_file = os.path.join(BASE_DIR, "data", "saved_for_later.json")
    with save_file_lock:
        with open(save_file, "w", encoding="utf-8") as f:
            json.dump([], f)
    return {"status": "ok"}

@app.post("/generate_podcast")
async def generate_podcast_api():
    global ACTIVE_PODCAST_PROCS, ACTIVE_PODCAST_TASKS
    
    save_file = os.path.join(BASE_DIR, "data", "saved_for_later.json")
    saved_items = []
    with save_file_lock:
        if os.path.exists(save_file):
            try:
                with open(save_file, "r", encoding="utf-8") as f: saved_items = json.load(f)
            except: pass
    if not saved_items: return {"error": "No saved items"}
    
    text = "\n\n".join(item.get("text", "") for item in saved_items)
    md5_val = hashlib.md5(text.encode("utf-8")).hexdigest()
    
    # 过滤并清理已结束的任务进程
    for m, proc in list(ACTIVE_PODCAST_TASKS.items()):
        if not proc.is_alive():
            ACTIVE_PODCAST_TASKS.pop(m, None)
            
    # 如果检测到相同内容的任务已在生成中，直接返回成功并清空原列表
    if md5_val in ACTIVE_PODCAST_TASKS:
        with save_file_lock:
            with open(save_file, "w", encoding="utf-8") as f: json.dump([], f)
        return {"status": "generating", "message": "该合集内容已在后台生成中，无需重复提交！"}
        
    os.makedirs(PODCASTS_DIR, exist_ok=True)
    filename = os.path.join(PODCASTS_DIR, f"podcast_合集_web_大合集播客_{int(time.time())}.wav")
    
    config = storage.load_config()
    config["performance_profile"] = "quiet"
    first_voice = saved_items[0].get("voice") if saved_items else None
    if first_voice:
        config["voice"] = first_voice
        
    p = mp.Process(target=run_podcast_generation_thread, args=(filename, text, config, GLOBAL_PAUSE_EVENT, GLOBAL_PODCAST_GPU_LOCK), daemon=True)
    p.start()
    
    ACTIVE_PODCAST_PROCS = [proc for proc in ACTIVE_PODCAST_PROCS if proc.is_alive()]
    ACTIVE_PODCAST_PROCS.append(p)
    ACTIVE_PODCAST_TASKS[md5_val] = p
    
    async def cleanup(proc, m):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, proc.join)
        ACTIVE_PODCAST_TASKS.pop(m, None)
    asyncio.create_task(cleanup(p, md5_val))
    
    with save_file_lock:
        with open(save_file, "w", encoding="utf-8") as f: json.dump([], f)
    return {"status": "generating", "filename": filename}

@app.get("/saved_items")
async def get_saved_items():
    save_file = os.path.join(BASE_DIR, "data", "saved_for_later.json")
    saved_items = []
    with save_file_lock:
        if os.path.exists(save_file):
            try:
                with open(save_file, "r", encoding="utf-8") as f: saved_items = json.load(f)
            except: pass
    
    current_time = time.time()
    for url, info in list(ACTIVE_URL_TASKS.items()):
        if not info.get("is_podcast", False) and current_time - info["timestamp"] < 60:
            saved_items.insert(0, {
                "timestamp": info["timestamp"],
                "text": url,
                "title": "⏳ 正在抓取网页正文...",
                "source": "web",
                "is_exported": False,
                "is_pending": True
            })
    return saved_items

@app.post("/play_saved")
async def play_saved(data: dict = Body(...)):
    indices = data.get("indices", [])
    if not indices: return {"error": "No items selected"}
    save_file = os.path.join(BASE_DIR, "data", "saved_for_later.json")
    saved_items = []
    with save_file_lock:
        if os.path.exists(save_file):
            try:
                with open(save_file, "r", encoding="utf-8") as f: saved_items = json.load(f)
            except: pass
    if not saved_items: return {"error": "Queue empty"}
    text_to_play = "\n\n".join(saved_items[idx].get("text", "") for idx in indices if 0 <= idx < len(saved_items))
    if not text_to_play.strip(): return {"error": "Selected items are empty"}
    
    # Extract voice from the first selected item, if any
    first_idx = indices[0] if indices else 0
    voice = None
    if 0 <= first_idx < len(saved_items):
        voice = saved_items[first_idx].get("voice")
    
    if indices and 0 <= indices[0] < len(saved_items):
        runtime_state.set_current_media(podcast=None, md5=saved_items[indices[0]].get("md5"))
    else:
        runtime_state.set_current_media(podcast=None, md5=None)

    payload = {"text": text_to_play, "from_saved": True}
    if voice: payload["voice"] = voice
    
    return await read_text(payload)



@app.get("/cache/items")
async def get_cache_items():
    items = storage.get_all_cache()
    podcast_dir = PODCASTS_DIR
    for item in items:
        md5 = item.get("md5")
        is_exported = False
        if md5 and os.path.exists(podcast_dir):
            for f in os.listdir(podcast_dir):
                if f.startswith(f"podcast_") and f.endswith(".wav") and md5[:8] in f:
                    is_exported = True
                    break
        item["is_exported"] = is_exported
    return items

@app.post("/cache/play")
async def play_cache(data: dict = Body(...)):
    md5 = data.get("md5")
    item = storage.get_cache_by_md5(md5)
    if not item: return {"error": "Cache not found"}
    text = item.get("text", "")
    return await read_text({"text": text})

@app.post("/cache/export")
async def export_cache(data: dict = Body(...)):
    md5 = data.get("md5")
    item = storage.get_cache_by_md5(md5)
    if not item: return {"error": "Cache not found"}
    text = item.get("text", "")
    return await generate_single_podcast({"text": text, "source": "cache"})

@app.post("/cache/delete")
async def delete_cache(data: dict = Body(...)):
    md5 = data.get("md5")
    storage.delete_cache_by_md5(md5)
    return {"status": "ok"}

@app.post("/cache/clear")
async def clear_cache_endpoint():
    clear_all_cache()
    import sqlite3
    try:
        conn = sqlite3.connect(storage.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cache_metadata")
        conn.commit()
        conn.close()
    except: pass
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="error")
