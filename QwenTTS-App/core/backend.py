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

# 确保能找到 core 目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from core.tts_engine import TTSEngine
from core.player import PCMPlayer
from core.processor import TextProcessor
from core.storage import Storage

CACHE_DIR = os.path.join(BASE_DIR, "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# 终极同步信号：必须是字符串，确保跨进程一致
GLOBAL_SENTINEL = "PIPELINE_END_STRICT_V1"

def get_text_hash(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def manage_cache_limit(max_items=10):
    try:
        files = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR) if f.endswith('.npy')]
        if len(files) <= max_items: return
        files.sort(key=os.path.getmtime)
        for f in files[:-max_items]: os.remove(f)
    except: pass

def clear_all_cache():
    try:
        for f in os.listdir(CACHE_DIR): os.remove(os.path.join(CACHE_DIR, f))
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
            # 针对不同模型决定“喘气”频率：小模型睡得更久，因为生成更快，发热更集中
            is_small_model = "0.6B" in target_model_name
            throttle_sleep = 0.04 if is_small_model else 0.01

            for samples in engine.generate_stream(text, config):
                if shared_state.stop_event.is_set() or task_id != shared_state.current_task_id.value: 
                    break
                shared_state.audio_q.put((tid if 'tid' in locals() else task_id, samples))
                full_audio.append(samples)
                # 巡航节流
                time.sleep(throttle_sleep)
            
            if not shared_state.stop_event.is_set() and task_id == shared_state.current_task_id.value and cache_file and full_audio:
                try:
                    np.save(cache_file, np.concatenate(full_audio))
                    manage_cache_limit(10)
                except: pass
            
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
# 强制播放器的 SENTINEL 与全局一致
player = PCMPlayer(sample_rate=24000)
player.SENTINEL = GLOBAL_SENTINEL

processor = TextProcessor()
MAIN_IS_PLAYING = False
MAIN_TITLE = ""
MAIN_PROGRESS = "0/0"
save_file_lock = threading.Lock()
podcast_buffer_lock = threading.Lock()
PODCAST_FILE = None
PODCAST_BUFFER = []

def shared_task_loop(tid, start_idx, chunks, config, state, is_podcast=False):
    global MAIN_IS_PLAYING, MAIN_PROGRESS
    try:
        if not is_podcast:
            player.start()
        S.set_status("BUSY")
        for i in range(start_idx, len(chunks)):
            if S.stop_event.is_set() or tid != S.current_task_id.value: break
            MAIN_PROGRESS = f"{i+1}/{len(chunks)}"
            chunk_text = chunks[i]
            S.text_q.put({'task_id': tid, 'text': chunk_text, 'config': config, 'hash': get_text_hash(chunk_text)})
            if not is_podcast:
                state["current_article"]["current_index"] = i
                storage.save_state(state)
            
            # Don't cool down based on player queue if it's podcast mode (since player isn't playing)
            # Use 24000 for calculation
            while (not is_podcast and player.audio_queue.qsize() * (2048/24000) > 20.0) and not S.stop_event.is_set() and tid == S.current_task_id.value:
                S.set_status("COOLING")
                time.sleep(1.0)
                S.set_status("BUSY")
    finally:
        if tid == S.current_task_id.value:
            S.text_q.put(GLOBAL_SENTINEL)
            if not is_podcast:
                player.wait_until_finished()
                MAIN_IS_PLAYING = False
            S.set_status("IDLE")

def performance_monitor_thread():
    import psutil
    process = psutil.Process(os.getpid())
    print("[Monitor] 性能监控就绪")
    last_status = "IDLE"
    while True:
        try:
            st = S.get_status()
            if MAIN_IS_PLAYING and st == "IDLE": st = "PLAYING"
            
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
    global PODCAST_FILE, PODCAST_BUFFER
    while True:
        try:
            item = S.audio_q.get()
            if item is None: break
            if isinstance(item, str) and item == GLOBAL_SENTINEL:
                if PODCAST_FILE:
                    try:
                        with podcast_buffer_lock:
                            if PODCAST_BUFFER:
                                wav_data = np.concatenate(PODCAST_BUFFER)
                                wav_data = (np.clip(wav_data, -1.0, 1.0) * 32767).astype(np.int16)
                                scipy.io.wavfile.write(PODCAST_FILE, 24000, wav_data)
                                print(f"[Podcast] Saved to {PODCAST_FILE}")
                                
                                save_file = os.path.join(BASE_DIR, "data", "saved_for_later.json")
                                with save_file_lock:
                                    with open(save_file, "w", encoding="utf-8") as f:
                                        json.dump([], f)
                    except Exception as e:
                        print(f"[Podcast] Error saving: {e}")
                    finally:
                        PODCAST_FILE = None
                        with podcast_buffer_lock:
                            PODCAST_BUFFER = []
                else:
                    player.signal_end_of_article()
                continue
            
            if isinstance(item, tuple) and len(item) == 2:
                tid, samples = item
                if tid == S.current_task_id.value:
                    if PODCAST_FILE is not None:
                        with podcast_buffer_lock:
                            PODCAST_BUFFER.append(samples)
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
    p.terminate()
    clear_all_cache()
    S.text_q.put(None)

app = FastAPI(lifespan=lifespan)

@app.post("/read")
async def read_text(data: dict = Body(...)):
    global MAIN_IS_PLAYING, MAIN_TITLE, MAIN_PROGRESS, PODCAST_FILE, PODCAST_BUFFER
    text = data.get('text', "")
    voice = data.get("voice", None)
    
    PODCAST_FILE = None
    with podcast_buffer_lock:
        PODCAST_BUFFER = []
    
    with S.current_task_id.get_lock():
        S.current_task_id.value += 1
    new_task_id = S.current_task_id.value

    S.stop_event.set()
    player.stop()
    S.stop_event.clear()
    
    state = storage.load_state()
    config = storage.load_config()
    if voice:
        config["voice"] = voice
        
    if text == "RESUME_MODE":
        current_art = state.get("current_article", {})
        chunks = current_art.get("chunks", [])
        curr_idx = current_art.get("current_index", 0)
    else:
        chunks = processor.smart_split(text)
        state["current_article"] = {"title": text[:15].replace("\n", " ") + "...", "chunks": chunks, "current_index": 0}
        storage.save_state(state)
        curr_idx = 0
    
    MAIN_TITLE = state["current_article"]["title"]
    MAIN_IS_PLAYING = True
    
    threading.Thread(target=shared_task_loop, args=(new_task_id, curr_idx, chunks, config, state), daemon=True).start()
    return {"status": "ok"}

@app.post("/stop")
async def stop_read():
    global MAIN_IS_PLAYING, PODCAST_FILE, PODCAST_BUFFER, ACTIVE_PODCAST_PROCS
    
    PODCAST_FILE = None
    with podcast_buffer_lock:
        PODCAST_BUFFER = []
    
    with S.current_task_id.get_lock():
        S.current_task_id.value += 1
    S.stop_event.set()
    player.stop()
    MAIN_IS_PLAYING = False
    S.set_status("IDLE")
    
    # Cancel all active podcast generation processes
    for p in ACTIVE_PODCAST_PROCS:
        if p.is_alive():
            try: p.terminate()
            except: pass
    ACTIVE_PODCAST_PROCS.clear()
    
    # Cleanup pending podcast files
    podcasts_dir = os.path.join(BASE_DIR, "data", "podcasts")
    if os.path.exists(podcasts_dir):
        for f in os.listdir(podcasts_dir):
            if ".pending_" in f:
                try: os.remove(os.path.join(podcasts_dir, f))
                except: pass
                
    return {"status": "ok"}

@app.get("/status")
async def get_status():
    global MAIN_IS_PLAYING, MAIN_TITLE, MAIN_PROGRESS
    # is_playing should be True if MAIN_IS_PLAYING and the player is not paused
    return {
        "is_playing": MAIN_IS_PLAYING and not player.is_paused,
        "title": MAIN_TITLE,
        "progress": MAIN_PROGRESS,
        "buffer_sec": player.get_queue_duration(),
        "status_code": S.get_status()
    }

@app.post("/pause")
async def pause_playback():
    player.pause()
    return {"status": "paused"}

@app.post("/resume")
async def resume_playback():
    player.resume()
    return {"status": "resumed"}

@app.post("/seek")
async def seek_playback(data: dict = Body(...)):
    global MAIN_IS_PLAYING, MAIN_TITLE, MAIN_PROGRESS, PODCAST_FILE, PODCAST_BUFFER
    direction = data.get("direction", 1) # 1 for next, -1 for prev
    
    PODCAST_FILE = None
    with podcast_buffer_lock:
        PODCAST_BUFFER = []
    
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
    with S.current_task_id.get_lock():
        S.current_task_id.value += 1
    new_task_id = S.current_task_id.value

    S.stop_event.set()
    player.stop()
    S.stop_event.clear()
    
    MAIN_IS_PLAYING = True
    config = storage.load_config()
    
    threading.Thread(target=shared_task_loop, args=(new_task_id, new_idx, chunks, config, state), daemon=True).start()
    return {"status": "seeking", "new_index": new_idx}

import asyncio
import subprocess

ACTIVE_URL_TASKS: dict[str, dict] = {}
ACTIVE_PODCAST_PROCS = []

GLOBAL_PAUSE_EVENT = mp.Event()
GLOBAL_PODCAST_GPU_LOCK = mp.Lock()
LAST_ACTIVE_TIME = time.time()

def podcast_manager_loop():
    global LAST_ACTIVE_TIME
    while True:
        # Update last active time if anything is actively playing or URL is being fetched
        if MAIN_IS_PLAYING or len(ACTIVE_URL_TASKS) > 0:
            LAST_ACTIVE_TIME = time.time()
            
        if MAIN_IS_PLAYING or len(ACTIVE_URL_TASKS) > 0 or (time.time() - LAST_ACTIVE_TIME < 120):
            if not GLOBAL_PAUSE_EVENT.is_set():
                GLOBAL_PAUSE_EVENT.set()
        else:
            if GLOBAL_PAUSE_EVENT.is_set():
                GLOBAL_PAUSE_EVENT.clear()
        time.sleep(2)

threading.Thread(target=podcast_manager_loop, daemon=True).start()

def run_single_podcast_generation_thread(text: str, config: dict, md5: str, source: str, pause_event, gpu_lock) -> None:
    import traceback
    safe_title = "".join(c for c in text[:20] if c.isalnum() or '\u4e00' <= c <= '\u9fff')
    if not safe_title: safe_title = "无标题"
    
    pending_file = os.path.join(BASE_DIR, "data", "podcasts", f".pending_单篇_{source}_{safe_title}_{md5[:8]}")
    os.makedirs(os.path.dirname(pending_file), exist_ok=True)
    with open(pending_file, "w") as f: f.write(text[:20])
    try:
        with gpu_lock:
            engine = TTSEngine(model_path=f"models/{config.get('model', 'Qwen3-TTS-1.7B-8bit')}", mlx_audio_path="../../mlx_audio")
            engine.ensure_model_loaded()
            chunks = TextProcessor().smart_split(text)
            audio_data = []
            for chunk in chunks:
                # Wait if paused by the manager
                while pause_event.is_set():
                    time.sleep(2)
                    
                for samples in engine.generate_stream(chunk, config):
                    audio_data.append(samples)
                # Let the GPU cool down between sentences
                time.sleep(1.5)
            if audio_data:
                full_wav = np.concatenate(audio_data)
                wav_data = (np.clip(full_wav, -1.0, 1.0) * 32767).astype(np.int16)
                out_name = f"podcast_单篇_{source}_{safe_title}_{md5[:8]}_{int(time.time())}.wav"
                scipy.io.wavfile.write(os.path.join(BASE_DIR, "data", "podcasts", out_name), 24000, wav_data)
    except Exception as e:
        print(f"[PodcastProcess] Error: {e}")
        traceback.print_exc()
    finally:
        if os.path.exists(pending_file): os.remove(pending_file)

def run_podcast_generation_thread(filename: str, text: str, config: dict, pause_event, gpu_lock) -> None:
    import traceback
    pending_file = filename.replace(".wav", "") + ".pending_合集"
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(pending_file, "w") as f: f.write("pending")
    try:
        with gpu_lock:
            engine = TTSEngine(model_path=f"models/{config.get('model', 'Qwen3-TTS-1.7B-8bit')}", mlx_audio_path="../../mlx_audio")
            engine.ensure_model_loaded()
            chunks = TextProcessor().smart_split(text)
            audio_data = []
            for chunk in chunks:
                while pause_event.is_set():
                    time.sleep(2)
                    
                for samples in engine.generate_stream(chunk, config):
                    audio_data.append(samples)
                # Let the GPU cool down between sentences
                time.sleep(1.5)
            if audio_data:
                full_wav = np.concatenate(audio_data)
                wav_data = (np.clip(full_wav, -1.0, 1.0) * 32767).astype(np.int16)
                scipy.io.wavfile.write(filename, 24000, wav_data)
    except Exception as e:
        print(f"[PodcastProcess] Error: {e}")
    finally:
        if os.path.exists(pending_file): os.remove(pending_file)

@app.post("/read_url")
async def read_url(payload: dict = Body(...)) -> dict:
    global ACTIVE_URL_TASKS
    url = payload.get("url", "").strip()
    translate = payload.get("translate", False)
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
    if translate: cmd.append("-t")
    if save: cmd.append("--save")
    if podcast: cmd.append("--podcast")
    
    async def run_cli_task():
        try:
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.wait()
        finally:
            ACTIVE_URL_TASKS.pop(url, None)
            
    asyncio.create_task(run_cli_task())
    return {"status": "ok", "message": "Read URL task dispatched"}

@app.post("/delete_saved")
async def delete_saved(data: dict = Body(...)):
    index = data.get("index")
    save_file = os.path.join(BASE_DIR, "data", "saved_for_later.json")
    with save_file_lock:
        if os.path.exists(save_file):
            with open(save_file, "r", encoding="utf-8") as f: items = json.load(f)
            if 0 <= index < len(items):
                items.pop(index)
                with open(save_file, "w", encoding="utf-8") as f: json.dump(items, f, ensure_ascii=False, indent=2)
                return {"status": "ok"}
    return {"error": "Item not found"}

@app.get("/podcasts/list")
async def list_podcasts():
    podcasts_dir = os.path.join(BASE_DIR, "data", "podcasts")
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
    filepath = os.path.join(BASE_DIR, "data", "podcasts", filename)
    if not os.path.exists(filepath): return {"error": "File not found"}
    
    if "pinned_" in filename:
        new_name = filename.replace("pinned_", "")
    else:
        new_name = "pinned_" + filename
        
    new_path = os.path.join(BASE_DIR, "data", "podcasts", new_name)
    os.rename(filepath, new_path)
    return {"status": "ok"}

@app.post("/podcasts/clear")
async def clear_podcasts():
    podcasts_dir = os.path.join(BASE_DIR, "data", "podcasts")
    if not os.path.exists(podcasts_dir): return {"status": "ok"}
    
    deleted_count = 0
    for f in os.listdir(podcasts_dir):
        if f.endswith(".wav") and "pinned_" not in f:
            try:
                os.remove(os.path.join(podcasts_dir, f))
                deleted_count += 1
            except: pass
    return {"status": "ok", "deleted_count": deleted_count}

@app.post("/podcasts/play")
async def play_podcast(data: dict = Body(...)):
    filename = data.get("filename", "")
    filepath = os.path.join(BASE_DIR, "data", "podcasts", filename)
    if not os.path.exists(filepath): return {"error": "File not found"}
    
    import subprocess
    try:
        subprocess.Popen(["open", filepath])
    except Exception as e:
        return {"error": str(e)}
    return {"status": "ok"}

@app.post("/save_for_later")
async def save_for_later(data: dict = Body(...)):
    global LAST_ACTIVE_TIME
    LAST_ACTIVE_TIME = time.time()
    text = data.get("text", "").strip()
    source = data.get("source", "web")
    voice = data.get("voice", None)
    if not text: return {"error": "Empty text"}
    
    save_file = os.path.join(BASE_DIR, "data", "saved_for_later.json")
    saved_items = []
    md5_val = hashlib.md5(text.encode("utf-8")).hexdigest()
    
    with save_file_lock:
        if os.path.exists(save_file):
            try:
                with open(save_file, "r", encoding="utf-8") as f: saved_items = json.load(f)
            except: pass
            
        saved_items.append({
            "timestamp": time.time(),
            "text": text,
            "title": text[:20].replace("\n", " ") + "...",
            "source": source,
            "voice": voice,
            "is_exported": False,
            "md5": md5_val
        })
        if len(saved_items) > 5: saved_items = saved_items[-5:]
        with open(save_file, "w", encoding="utf-8") as f: json.dump(saved_items, f, ensure_ascii=False, indent=2)
            
    return {"status": "saved", "count": len(saved_items)}

@app.post("/generate_single_podcast")
async def generate_single_podcast(data: dict = Body(...)):
    global ACTIVE_PODCAST_PROCS, LAST_ACTIVE_TIME
    LAST_ACTIVE_TIME = time.time()
    text = data.get("text", "").strip()
    source = data.get("source", "web")
    voice = data.get("voice", None)
    if not text: return {"error": "Empty text"}
    
    md5_val = hashlib.md5(text.encode("utf-8")).hexdigest()
    config = storage.load_config()
    if voice:
        config["voice"] = voice
    p = mp.Process(target=run_single_podcast_generation_thread, args=(text, config, md5_val, source, GLOBAL_PAUSE_EVENT, GLOBAL_PODCAST_GPU_LOCK), daemon=True)
    p.start()
    
    ACTIVE_PODCAST_PROCS = [proc for proc in ACTIVE_PODCAST_PROCS if proc.is_alive()]
    ACTIVE_PODCAST_PROCS.append(p)
    
    async def cleanup(proc):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, proc.join)
    asyncio.create_task(cleanup(p))
    
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
    save_file = os.path.join(BASE_DIR, "data", "saved_for_later.json")
    saved_items = []
    with save_file_lock:
        if os.path.exists(save_file):
            try:
                with open(save_file, "r", encoding="utf-8") as f: saved_items = json.load(f)
            except: pass
    if not saved_items: return {"error": "No saved items"}
    
    text = "\n\n".join(item.get("text", "") for item in saved_items)
    os.makedirs(os.path.join(BASE_DIR, "data", "podcasts"), exist_ok=True)
    filename = os.path.join(BASE_DIR, "data", "podcasts", f"podcast_合集_web_大合集播客_{int(time.time())}.wav")
    
    config = storage.load_config()
    first_voice = saved_items[0].get("voice") if saved_items else None
    if first_voice:
        config["voice"] = first_voice
        
    p = mp.Process(target=run_podcast_generation_thread, args=(filename, text, config, GLOBAL_PAUSE_EVENT, GLOBAL_PODCAST_GPU_LOCK), daemon=True)
    p.start()
    
    global ACTIVE_PODCAST_PROCS
    ACTIVE_PODCAST_PROCS = [proc for proc in ACTIVE_PODCAST_PROCS if proc.is_alive()]
    ACTIVE_PODCAST_PROCS.append(p)
    
    async def cleanup(proc):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, proc.join)
    asyncio.create_task(cleanup(p))
    
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
    
    payload = {"text": text_to_play}
    if voice: payload["voice"] = voice
    
    return await read_text(payload)



@app.get("/cache/items")
async def get_cache_items():
    items = storage.get_all_cache()
    podcast_dir = os.path.join(BASE_DIR, "data", "podcasts")
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
