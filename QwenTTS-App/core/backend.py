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
player = PCMPlayer(sample_rate=16000)
player.SENTINEL = GLOBAL_SENTINEL

processor = TextProcessor()
MAIN_IS_PLAYING = False
MAIN_TITLE = ""
MAIN_PROGRESS = "0/0"
save_file_lock = threading.Lock()
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
            while (not is_podcast and player.audio_queue.qsize() * (2048/16000) > 20.0) and not S.stop_event.is_set() and tid == S.current_task_id.value:
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
                f"Buffer: {player.audio_queue.qsize() * (2048/16000):.1f}s\n"
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
                        PODCAST_BUFFER = []
                else:
                    player.signal_end_of_article()
                continue
            
            if isinstance(item, tuple) and len(item) == 2:
                tid, samples = item
                if tid == S.current_task_id.value:
                    if PODCAST_FILE is not None:
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
    
    PODCAST_FILE = None
    PODCAST_BUFFER = []
    
    with S.current_task_id.get_lock():
        S.current_task_id.value += 1
    new_task_id = S.current_task_id.value

    S.stop_event.set()
    player.stop()
    S.stop_event.clear()
    
    state = storage.load_state()
    config = storage.load_config()
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
    global MAIN_IS_PLAYING, PODCAST_FILE, PODCAST_BUFFER
    
    PODCAST_FILE = None
    PODCAST_BUFFER = []
    
    with S.current_task_id.get_lock():
        S.current_task_id.value += 1
    S.stop_event.set()
    player.stop()
    MAIN_IS_PLAYING = False
    S.set_status("IDLE")
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

@app.post("/save_for_later")
async def save_for_later(data: dict = Body(...)):
    text = data.get("text", "").strip()
    if not text:
        return {"error": "Empty text"}
        
    save_file = os.path.join(BASE_DIR, "data", "saved_for_later.json")
    saved_items = []
    
    with save_file_lock:
        if os.path.exists(save_file):
            try:
                with open(save_file, "r", encoding="utf-8") as f:
                    saved_items = json.load(f)
            except:
                pass
                
        # Append new item
        saved_items.append({
            "timestamp": time.time(),
            "text": text,
            "title": text[:20].replace("\n", " ") + "..."
        })
        
        # Keep only the 3 most recent
        if len(saved_items) > 3:
            saved_items = saved_items[-3:]
            
        with open(save_file, "w", encoding="utf-8") as f:
            json.dump(saved_items, f, ensure_ascii=False, indent=2)
        
    return {"status": "saved", "count": len(saved_items)}

@app.post("/generate_podcast")
async def generate_podcast():
    global PODCAST_FILE, PODCAST_BUFFER
    save_file = os.path.join(BASE_DIR, "data", "saved_for_later.json")
    saved_items = []
    
    with save_file_lock:
        if os.path.exists(save_file):
            try:
                with open(save_file, "r", encoding="utf-8") as f:
                    saved_items = json.load(f)
            except: pass
            
        if not saved_items:
            return {"error": "No saved items"}
            
        # Clear the saved items after reading
        with open(save_file, "w", encoding="utf-8") as f:
            json.dump([], f)
            
    text = ""
    for item in saved_items:
        text += item.get("text", "") + "\n\n"
        
    os.makedirs(os.path.join(BASE_DIR, "data", "podcasts"), exist_ok=True)
    filename = os.path.join(BASE_DIR, "data", "podcasts", f"podcast_{int(time.time())}.wav")
    
    with S.current_task_id.get_lock():
        S.current_task_id.value += 1
    new_task_id = S.current_task_id.value

    S.stop_event.set()
    player.stop()
    S.stop_event.clear()
    
    PODCAST_FILE = filename
    PODCAST_BUFFER = []
    
    chunks = processor.smart_split(text)
    config = storage.load_config()
    
    threading.Thread(target=shared_task_loop, args=(new_task_id, 0, chunks, config, {}, True), daemon=True).start()
    return {"status": "generating", "file": filename}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="error")
