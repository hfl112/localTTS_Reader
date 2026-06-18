import os
import sys
import time
import threading
import multiprocessing as mp
import mlx.core as mx
import numpy as np
import scipy.io.wavfile
import hashlib
from fastapi import FastAPI
from contextlib import asynccontextmanager
import uvicorn
import traceback
import signal

# 确保能找到 core 目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

# 统一的播客目录（指向根目录下的 podcasts/ 目录）
PODCASTS_DIR = os.path.abspath(os.path.join(os.path.dirname(BASE_DIR), "podcasts"))
os.makedirs(PODCASTS_DIR, exist_ok=True)

from core.tts_engine import TTSEngine
from core.api_models import (
    DeleteSavedRequest,
    FilenameRequest,
    GenerateSinglePodcastRequest,
    Md5Request,
    PlaySavedRequest,
    ReadRequest,
    ReadUrlRequest,
    SaveForLaterRequest,
    SeekRequest,
)
from core.player import PCMPlayer
from core.processor import TextProcessor
from core.storage import Storage
from core.state.runtime_state import RuntimeState
from core.services.playback_service import PlaybackService
from core.services.podcast_service import PodcastService
from core.services.performance import get_performance_profile
from core.services.saved_items_service import SavedItemsService
from core.services.cache_service import CacheService
from core.services.runtime_log import RuntimeEventLog

CACHE_DIR = os.path.join(BASE_DIR, "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)
PODCAST_CHUNK_DIR = os.path.join(BASE_DIR, "data", "podcast_chunks")
os.makedirs(PODCAST_CHUNK_DIR, exist_ok=True)
RUNTIME_EVENTS_FILE = os.path.join(BASE_DIR, "data", "runtime_events.jsonl")
PODCAST_JOBS_FILE = os.path.join(BASE_DIR, "data", "podcast_jobs.json")

# 终极同步信号：必须是字符串，确保跨进程一致
GLOBAL_SENTINEL = "PIPELINE_END_STRICT_V1"

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
    cache_service.clear()

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
runtime_state = RuntimeState()
saved_items_service = SavedItemsService(BASE_DIR)
cache_service = CacheService(storage, CACHE_DIR, PODCASTS_DIR)
event_log = RuntimeEventLog(RUNTIME_EVENTS_FILE)

playback_service = PlaybackService(
    shared_state=S,
    player=player,
    storage=storage,
    runtime_state=runtime_state,
    sentinel=GLOBAL_SENTINEL,
    get_text_hash=get_text_hash,
    get_performance_profile=get_performance_profile,
    event_log=event_log,
)

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
                            event_log.record("podcast_buffer_saved", output_path=podcast_file)
                            
                            saved_items_service.clear()
                    except Exception as e:
                        print(f"[Podcast] Error saving: {e}")
                        event_log.record("podcast_buffer_save_failed", error=str(e))
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
async def read_text(data: ReadRequest):
    text = data.text
    voice = data.voice
    source = data.source

    runtime_state.clear_current_media(keep_md5=data.from_saved)
    runtime_state.reset_podcast_generation()
    
    playback_session_id, new_task_id = playback_service.start_new_session()
    event_log.record(
        "read_requested",
        source=source,
        voice=voice,
        text_chars=len(text),
        session_id=playback_session_id,
        task_id=new_task_id,
    )
    
    state = storage.load_state()
    config = storage.load_config()
    config["performance_profile"] = data.performance_profile or config.get(
        "performance_profile", "balanced"
    )
    if voice:
        config["voice"] = voice
        
    if text == "RESUME_MODE":
        current_art = state.get("current_article", {})
        chunks = current_art.get("chunks", [])
        curr_idx = current_art.get("current_index", 0)
    else:
        if source == "clipboard" and text:
            try:
                saved_items_service.save(text, source="clipboard", voice=voice)
            except Exception as e:
                print(f"[Backend] Auto-saving clipboard text failed: {e}")
                
        chunks = processor.parse_dialogue_or_text(text, performance_profile=config["performance_profile"])
        state["current_article"] = {"title": text[:15].replace("\n", " ") + "...", "chunks": chunks, "current_index": 0}
        storage.save_state(state)
        curr_idx = 0
    
    runtime_state.set_main(title=state["current_article"]["title"], is_playing=True)
    
    playback_service.start_tts_thread(
        session_id=playback_session_id,
        task_id=new_task_id,
        start_idx=curr_idx,
        chunks=chunks,
        config=config,
        state=state,
    )
    return {"status": "ok"}

@app.post("/stop")
async def stop_read():
    event_log.record("stop_requested")
    runtime_state.clear_current_media()
    runtime_state.reset_podcast_generation()
    
    playback_service.stop_current_session()
    runtime_state.set_main(is_playing=False)
    S.set_status("IDLE")
    
    podcast_service.cancel_all()
    podcast_service.cleanup_pending_files()
                
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
        **playback_service.snapshot(),
        "status_code": S.get_status(),
        **runtime_snapshot,
        **podcast_service.snapshot(),
        "active_url_tasks": list(ACTIVE_URL_TASKS.keys()),
    }

@app.get("/debug/events")
async def debug_events(limit: int = 50):
    return event_log.recent(limit=limit)

@app.post("/pause")
async def pause_playback():
    playback_service.pause()
    return {"status": "paused"}

@app.post("/resume")
async def resume_playback():
    playback_service.resume()
    return {"status": "resumed"}

@app.post("/restart_audio")
async def restart_audio():
    if player is not None:
        try:
            playback_service.restart_device()
            return {"status": "ok"}
        except Exception as e:
            return {"error": str(e)}
    return {"error": "Player not initialized"}

@app.post("/seek")
async def seek_playback(data: SeekRequest):
    direction = data.direction  # 1 for next, -1 for prev
    event_log.record("seek_requested", direction=direction)
    
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
    playback_session_id, new_task_id = playback_service.start_new_session()
    
    runtime_state.set_main(is_playing=True)
    config = storage.load_config()
    config["performance_profile"] = config.get("performance_profile", "balanced")
    
    playback_service.start_tts_thread(
        session_id=playback_session_id,
        task_id=new_task_id,
        start_idx=new_idx,
        chunks=chunks,
        config=config,
        state=state,
    )
    return {"status": "seeking", "new_index": new_idx}

import asyncio

ACTIVE_URL_TASKS: dict[str, dict] = {}
podcast_service = PodcastService(
    podcasts_dir=PODCASTS_DIR,
    podcast_chunk_dir=PODCAST_CHUNK_DIR,
    runtime_state=runtime_state,
    active_url_tasks=ACTIVE_URL_TASKS,
    jobs_file=PODCAST_JOBS_FILE,
    event_log=event_log,
)

@app.post("/read_url")
async def read_url(payload: ReadUrlRequest) -> dict:
    global ACTIVE_URL_TASKS
    url = payload.url.strip()
    html = payload.html.strip()
    mode = payload.effective_mode()
    save = payload.save
    podcast = payload.podcast
    if not url: return {"error": "Empty URL"}
        
    current_time = time.time()
    for task_url, task_info in list(ACTIVE_URL_TASKS.items()):
        if current_time - task_info["timestamp"] >= 60:
            ACTIVE_URL_TASKS.pop(task_url, None)
    if url in ACTIVE_URL_TASKS:
        return {"status": "error", "message": "该网页正处于后台解析抓取中，请不要重复点击，稍候可在下方收藏列表中查看！"}
        
    ACTIVE_URL_TASKS[url] = {"timestamp": current_time, "is_podcast": podcast}
    event_log.record(
        "read_url_dispatched",
        url=url,
        mode=mode,
        save=save,
        podcast=podcast,
        has_uploaded_html=bool(html),
    )
    
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
            event_log.record("read_url_finished", url=url)
            if temp_html_path and os.path.exists(temp_html_path):
                try: os.remove(temp_html_path)
                except: pass
            
    asyncio.create_task(run_cli_task())
    return {"status": "ok", "message": "Read URL task dispatched"}

@app.post("/delete_saved")
async def delete_saved(data: DeleteSavedRequest):
    md5 = data.md5
    index = data.index
    if saved_items_service.delete(md5=md5, index=index):
        return {"status": "ok"}
    return {"error": "Item not found"}

@app.get("/podcasts/list")
async def list_podcasts():
    return podcast_service.list_files()

@app.get("/podcasts/jobs")
async def list_podcast_jobs():
    return podcast_service.list_jobs()

@app.post("/podcasts/toggle_pin")
async def toggle_pin(data: FilenameRequest):
    return podcast_service.toggle_pin(data.filename)

@app.post("/podcasts/clear")
async def clear_podcasts():
    deleted_count = podcast_service.clear_unpinned()
    return {"status": "ok", "deleted_count": deleted_count}

@app.post("/podcasts/delete")
async def delete_podcast(data: FilenameRequest):
    return podcast_service.delete(data.filename)

@app.post("/podcasts/play")
async def play_podcast(data: FilenameRequest):
    filename = data.filename
    filepath = podcast_service.find_file(filename)
    if not filepath:
        return {"error": "File not found"}
    
    event_log.record("podcast_play_requested", filename=filename, filepath=filepath)
    playback_service.play_wav_file(filepath, filename)
    return {"status": "ok"}

@app.post("/save_for_later")
async def save_for_later(data: SaveForLaterRequest):
    runtime_state.touch_activity()
    text = data.text.strip()
    source = data.source
    voice = data.voice
    title = data.title
    if not text: return {"error": "Empty text"}
    
    count = saved_items_service.save(text, source, voice, title)
    event_log.record("saved_item_added", source=source, voice=voice, title=title, text_chars=len(text))
    return {"status": "saved", "count": count}

@app.post("/generate_single_podcast")
async def generate_single_podcast(data: GenerateSinglePodcastRequest):
    runtime_state.touch_activity()

    text = data.text.strip()
    source = data.source
    voice = data.voice
    title = data.title
    if not text: return {"error": "Empty text"}
    
    md5_val = hashlib.md5(text.encode("utf-8")).hexdigest()
    
    # 如果检测到相同内容的任务已在生成中，直接返回成功状态（不需要重复排队）
    if podcast_service.is_generating(md5_val):
        return {"status": "generating", "md5": md5_val, "message": "该内容已在后台生成中，无需重复提交！"}
        
    config = storage.load_config()
    config["performance_profile"] = data.performance_profile
    if voice:
        config["voice"] = voice
    podcast_service.start_single(
        text=text,
        config=config,
        md5=md5_val,
        source=source,
        title=title,
    )
    event_log.record(
        "single_podcast_requested",
        md5=md5_val,
        source=source,
        voice=voice,
        title=title,
        text_chars=len(text),
    )
    
    return {"status": "generating", "md5": md5_val}

@app.post("/saved_items/clear")
async def clear_saved_items():
    saved_items_service.clear()
    return {"status": "ok"}

@app.post("/generate_podcast")
async def generate_podcast_api():
    saved_items = saved_items_service.load()
    if not saved_items: return {"error": "No saved items"}
    
    text = "\n\n".join(item.get("text", "") for item in saved_items)
    md5_val = hashlib.md5(text.encode("utf-8")).hexdigest()
    
    # 如果检测到相同内容的任务已在生成中，直接返回成功并清空原列表
    if podcast_service.is_generating(md5_val):
        saved_items_service.clear()
        return {"status": "generating", "message": "该合集内容已在后台生成中，无需重复提交！"}
        
    os.makedirs(PODCASTS_DIR, exist_ok=True)
    filename = os.path.join(PODCASTS_DIR, f"podcast_合集_web_大合集播客_{int(time.time())}.wav")
    
    config = storage.load_config()
    config["performance_profile"] = "quiet"
    first_voice = saved_items[0].get("voice") if saved_items else None
    if first_voice:
        config["voice"] = first_voice
        
    podcast_service.start_batch(
        filename=filename,
        text=text,
        config=config,
        md5=md5_val,
    )
    event_log.record(
        "batch_podcast_requested",
        md5=md5_val,
        filename=filename,
        item_count=len(saved_items),
        text_chars=len(text),
    )
    
    saved_items_service.clear()
    return {"status": "generating", "filename": filename}

@app.get("/saved_items")
async def get_saved_items():
    saved_items = saved_items_service.load()
    
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
async def play_saved(data: PlaySavedRequest):
    indices = data.indices
    if not indices: return {"error": "No items selected"}
    saved_items = saved_items_service.load()
    if not saved_items: return {"error": "Queue empty"}
    text_to_play, voice, selected_md5 = saved_items_service.selected_text(indices)
    if not text_to_play.strip(): return {"error": "Selected items are empty"}
    runtime_state.set_current_media(podcast=None, md5=selected_md5)

    payload = ReadRequest(text=text_to_play, from_saved=True, voice=voice)

    return await read_text(payload)



@app.get("/cache/items")
async def get_cache_items():
    return cache_service.list_items()

@app.post("/cache/play")
async def play_cache(data: Md5Request):
    md5 = data.md5
    text = cache_service.get_text(md5)
    if text is None: return {"error": "Cache not found"}
    return await read_text(ReadRequest(text=text))

@app.post("/cache/export")
async def export_cache(data: Md5Request):
    md5 = data.md5
    text = cache_service.get_text(md5)
    if text is None: return {"error": "Cache not found"}
    return await generate_single_podcast(
        GenerateSinglePodcastRequest(text=text, source="cache")
    )

@app.post("/cache/delete")
async def delete_cache(data: Md5Request):
    cache_service.delete(data.md5)
    return {"status": "ok"}

@app.post("/cache/clear")
async def clear_cache_endpoint():
    cache_service.clear()
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="error")
