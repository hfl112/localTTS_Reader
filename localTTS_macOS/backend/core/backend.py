import os
import sys
import json
import time
from typing import List, Dict, Any
import threading
import multiprocessing as mp
import mlx.core as mx
import numpy as np
import scipy.io.wavfile
import hashlib
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
import uvicorn
import traceback
import signal
import uuid

# 确保能找到 core 目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from core.paths import runtime_paths

PODCASTS_DIR = runtime_paths.podcasts_path
CACHE_DIR = runtime_paths.cache_path
PODCAST_CHUNK_DIR = os.path.join(runtime_paths.app_support_path, "PodcastChunks")
os.makedirs(PODCAST_CHUNK_DIR, exist_ok=True)
RUNTIME_EVENTS_FILE = os.path.join(runtime_paths.data_path, "runtime_events.jsonl")
PODCAST_JOBS_FILE = os.path.join(runtime_paths.data_path, "podcast_jobs.json")
URL_JOBS_FILE = os.path.join(runtime_paths.data_path, "url_jobs.json")

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
    SettingsUpdateRequest,
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
from core.services.runtime_supervisor import RuntimeSupervisor
from core.services.url_jobs import UrlJobStore


# 终极同步信号：必须是字符串，确保跨进程一致
GLOBAL_SENTINEL = "PIPELINE_END_STRICT_V1"
INSTANCE_ID = str(uuid.uuid4())

# When the app launches us with TTS_BACKEND_PORT=0 it wants an OS-assigned
# ephemeral port (eliminating the app-side pick-then-bind TOCTOU). We bind the
# socket ourselves in __main__, record the real port here, and publish it in
# runtime.json so the app/extension can discover it. None means a fixed port.
BOUND_PORT: int | None = None

# Discovery descriptor: published on startup so the native app and the browser
# extension can find the dynamically-chosen port without relying on a fixed
# 8001. Lives at the App Support root for easy discovery; removed on shutdown.
RUNTIME_DESCRIPTOR_FILE = os.path.join(runtime_paths.app_support_path, "runtime.json")


def write_runtime_descriptor() -> None:
    try:
        descriptor = {
            "port": BOUND_PORT if BOUND_PORT is not None else int(os.environ.get("TTS_BACKEND_PORT", 8001)),
            "host": os.environ.get("TTS_BACKEND_HOST", "127.0.0.1"),
            "pid": os.getpid(),
            "instance_id": INSTANCE_ID,
            "managed": os.environ.get("TTS_WATCHDOG_FD") is not None,
        }
        tmp_path = RUNTIME_DESCRIPTOR_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(descriptor, f)
        os.replace(tmp_path, RUNTIME_DESCRIPTOR_FILE)
        print(f"[Backend] Runtime descriptor published: {RUNTIME_DESCRIPTOR_FILE} (port={descriptor['port']})")
    except Exception as e:
        print(f"[Backend] Failed to write runtime descriptor: {e}")


def remove_runtime_descriptor() -> None:
    try:
        if os.path.exists(RUNTIME_DESCRIPTOR_FILE):
            os.remove(RUNTIME_DESCRIPTOR_FILE)
    except Exception:
        pass


def get_text_hash(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

# Number of most-recent cache entries to keep (single source; was hardcoded
# as 10 in two places). The DB `created_at` order is authoritative for eviction.
CACHE_MAX_ITEMS = 10

def manage_cache_limit(max_items=None, storage_obj=None):
    """Evict oldest cache entries beyond the limit, driven by the DB's
    created_at order (authoritative) rather than filesystem mtime. Deletes each
    evicted row's file via its stored file_path, then the row — keeping DB and
    disk consistent (the old mtime+filename approach drifted from the DB)."""
    if storage_obj is None:
        return
    if max_items is None:
        max_items = CACHE_MAX_ITEMS
    try:
        rows = storage_obj.get_all_cache()  # newest-first (ORDER BY created_at DESC)
        for row in rows[max_items:]:
            file_path = row.get("file_path")
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError as e:
                    print(f"[Cache] Failed to remove {file_path}: {e}")
            md5_val = row.get("md5")
            if md5_val:
                try:
                    storage_obj.delete_cache_by_md5(md5_val)
                except Exception as e:
                    print(f"[Cache] Failed to delete row {md5_val}: {e}")
    except Exception as e:
        print(f"[Cache] manage_cache_limit error: {e}")

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
    # The application bundle is read-only after installation.  Worker metadata
    # must live in Application Support just like the main process metadata.
    worker_storage = Storage(data_dir=runtime_paths.data_path)
    last_active_time = time.time()
    metal_warning_reported = False
    
    def handle_signal(sig, frame):
        sys.exit(0)
    signal.signal(signal.SIGTERM, handle_signal)

    while True:
        try:
            try:
                shared_state.vram_mb.value = mx.get_active_memory() / 1024 / 1024
                metal_warning_reported = False
            except RuntimeError as error:
                # Querying Metal while idle is diagnostic-only.  A missing or
                # temporarily unavailable device must not create a hot error
                # loop that consumes a CPU core and floods the logs.
                shared_state.vram_mb.value = 0.0
                if not metal_warning_reported:
                    print(f"[InferenceProcess] Metal memory query unavailable: {error}")
                    metal_warning_reported = True
            
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
            chunk_index = task.get('chunk_index', -1)
            if task_id != shared_state.current_task_id.value:
                continue

            config = task['config']
            profile = get_performance_profile(config.get("performance_profile"))
            target_model_name = config.get("model", "Qwen3-TTS-1.7B-8bit")
            target_path = os.path.join(runtime_paths.models_path, target_model_name)

            if engine is None:
                engine = TTSEngine(
                    model_path=target_path,
                    mlx_audio_path=runtime_paths.mlx_audio_path,
                )
            
            if engine.abs_model_path != os.path.abspath(os.path.join(engine.base_dir, target_path)):
                print(f"[InferenceProcess] 模型切换 -> {target_model_name}")
                engine = TTSEngine(
                    model_path=target_path,
                    mlx_audio_path=runtime_paths.mlx_audio_path,
                )
            
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
                        shared_state.audio_q.put((task_id, chunk_index, cached_audio[s:s+SR]))
                        time.sleep(0.005)
                    shared_state.audio_q.put("CHUNK_DONE")
                    continue
                except Exception as e:
                    # 缓存重放失败：回退到正常合成（不要吞 KeyboardInterrupt/SystemExit）
                    print(f"[InferenceProcess] Cache replay failed, will re-synthesize: {e}")

            # 2. 实时推理
            full_audio = []
            throttle_sleep = profile["chunk_sleep"]

            for samples in engine.generate_stream(text, config):
                if shared_state.stop_event.is_set() or task_id != shared_state.current_task_id.value: 
                    break
                shared_state.audio_q.put((task_id, chunk_index, samples))
                full_audio.append(samples)
                time.sleep(throttle_sleep)
            
            if not shared_state.stop_event.is_set() and task_id == shared_state.current_task_id.value and cache_file and full_audio:
                try:
                    concat_audio = np.concatenate(full_audio)
                    np.save(cache_file, concat_audio)
                    duration = len(concat_audio) / 24000.0
                    try:
                        worker_storage.add_cache_metadata(
                            md5=text_hash,
                            text=text,
                            model=target_model_name,
                            voice=config.get("voice", "Serena"),
                            duration=duration,
                            file_path=cache_file
                        )
                    except Exception as db_err:
                        print(f"[InferenceProcess] Failed to save cache metadata: {db_err}")
                    manage_cache_limit(CACHE_MAX_ITEMS, worker_storage)
                except Exception as save_err:
                    print(f"[InferenceProcess] Save cache failed: {save_err}")
            
            shared_state.audio_q.put("CHUNK_DONE")
            # 注意：子进程不再随意 set_status("IDLE")，防止监控误报
            
        except Exception as e:
            print(f"[InferenceProcess] 异常: {e}")
            traceback.print_exc()
            time.sleep(1.0)

# ==========================================
# 3. 主进程逻辑
# ==========================================
S: SharedState | None = None
storage: Storage | None = None
player: PCMPlayer | None = None
processor: TextProcessor | None = None
runtime_state: RuntimeState | None = None
saved_items_service: SavedItemsService | None = None
cache_service: CacheService | None = None
event_log: RuntimeEventLog | None = None
url_job_store: UrlJobStore | None = None
playback_service: PlaybackService | None = None
podcast_service: PodcastService | None = None
runtime_supervisor: RuntimeSupervisor | None = None
ACTIVE_URL_TASKS: dict[str, dict] = {}


def init_runtime_services() -> None:
    global S
    global storage
    global player
    global processor
    global runtime_state
    global saved_items_service
    global cache_service
    global event_log
    global url_job_store
    global playback_service
    global podcast_service
    global runtime_supervisor

    if S is not None:
        return

    # Run explicit startup-only path setup (legacy data migration) before any
    # service touches the runtime dirs. Path resolution itself happens at import.
    runtime_paths.init()

    S = SharedState()
    storage = Storage()
    player = PCMPlayer(sample_rate=24000)
    player.SENTINEL = GLOBAL_SENTINEL
    processor = TextProcessor()
    runtime_state = RuntimeState()
    saved_items_service = SavedItemsService()

    cache_service = CacheService(storage, CACHE_DIR, PODCASTS_DIR)
    event_log = RuntimeEventLog(RUNTIME_EVENTS_FILE)
    url_job_store = UrlJobStore(URL_JOBS_FILE)
    url_job_store.mark_unfinished_failed("backend restarted before URL job completed")

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

    podcast_service = PodcastService(
        podcasts_dir=PODCASTS_DIR,
        podcast_chunk_dir=PODCAST_CHUNK_DIR,
        runtime_state=runtime_state,
        active_url_tasks=ACTIVE_URL_TASKS,
        jobs_file=PODCAST_JOBS_FILE,
        event_log=event_log,
        is_frontend_active=lambda: (
            runtime_state.snapshot()["main_is_playing"]
            and player is not None
            and not player.is_paused
        ),
        is_device_switching=lambda: bool(player is not None and player.is_device_switching()),
        get_battery_policy=lambda: storage.load_config().get("battery_podcast_policy", "pause"),
    )

    runtime_supervisor = RuntimeSupervisor(
        shared_state=S,
        player=player,
        playback_service=playback_service,
        podcast_service=podcast_service,
        url_job_store=url_job_store,
        active_url_tasks=ACTIVE_URL_TASKS,
        event_log=event_log,
    )

def performance_monitor_thread(shutdown_event: threading.Event):
    if S is None or player is None or runtime_state is None:
        return
    import psutil
    process = psutil.Process(os.getpid())
    print("[Monitor] 性能监控就绪")
    last_status = "IDLE"
    while not shutdown_event.is_set():
        try:
            st = S.get_status()
            if runtime_state.main_is_playing and st == "IDLE": st = "PLAYING"
            
            if st == "IDLE" and last_status != "IDLE":
                print(f"--- [DIAGNOSE] 任务已结束 (ID: {S.current_task_id.value}) ---\n")
            
            last_status = st
            if st == "IDLE":
                shutdown_event.wait(2)
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
            shutdown_event.wait(5)
        except Exception:
            shutdown_event.wait(5)

def audio_feeder_thread(shutdown_event: threading.Event):
    if S is None or player is None or runtime_state is None:
        return
    while not shutdown_event.is_set():
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
            
            if isinstance(item, tuple):
                if len(item) == 3:
                    tid, chunk_idx, samples = item
                elif len(item) == 2:
                    tid, samples = item
                    chunk_idx = -1
                else:
                    continue

                if tid == S.current_task_id.value:
                    if runtime_state.snapshot()["podcast_file"] is not None:
                        runtime_state.append_podcast_audio(samples)
                    else:
                        if chunk_idx is not None and chunk_idx >= 0:
                            player.play_chunk((samples, chunk_idx))
                        else:
                            player.play_chunk(samples)
        except Exception:
            if shutdown_event.is_set():
                break

@asynccontextmanager
async def lifespan(app: FastAPI):
    import shutil
    configured_ffmpeg = runtime_paths.ffmpeg_path
    if not (configured_ffmpeg and os.path.isfile(configured_ffmpeg)) and shutil.which("ffmpeg") is None:
        print("\n" + "="*80)
        print("[Warning] 系统中未检测到 ffmpeg 命令行工具！播客音频合成功能可能无法正常运作。")
        print("请确保已通过 brew install ffmpeg 安装，并将其添加至系统的 PATH 中。")
        print("="*80 + "\n")

    mp.set_start_method('spawn', force=True)
    try:
        init_runtime_services()
        if S is None or runtime_supervisor is None:
            raise RuntimeError("runtime shared state failed to initialize")
        runtime_supervisor.start_watchdog(asyncio.get_running_loop())
        runtime_supervisor.start_inference(inference_worker, (S,))
        runtime_supervisor.start_thread(audio_feeder_thread, name="audio-feeder")
        runtime_supervisor.start_thread(
            performance_monitor_thread,
            name="performance-monitor",
        )
        write_runtime_descriptor()
        yield
    finally:
        print("[Backend] lifespan 正在执行统一资源清理...")
        remove_runtime_descriptor()
        if runtime_supervisor is not None:
            await runtime_supervisor.shutdown()
        else:
            if podcast_service is not None:
                podcast_service.shutdown()
            if player is not None:
                player.close()

app = FastAPI(lifespan=lifespan)

from fastapi import Request
from fastapi.responses import JSONResponse

@app.middleware("http")
async def management_token_middleware(request: Request, call_next) -> Any:
    if request.method == "OPTIONS":
        return await call_next(request)

    token: str | None = os.environ.get("TTS_MANAGEMENT_TOKEN")
    path: str = request.url.path

    # Compatibility is opt-in and restricted to the loopback interface.  It is
    # used only when app.py owns the backend so the existing extension can keep
    # working while the authenticated native client is developed separately.
    # SECURITY: 该开关一旦开启会对所有匹配主机的请求完全绕过鉴权（含 /control、
    # /settings），仅应在 app.py 自管后端时启用。"testclient" 是 FastAPI TestClient
    # 的伪主机，仅在 pytest 运行时放行，避免生产中被伪造该 host 绕过鉴权。
    loopback_hosts = {"127.0.0.1", "::1", "localhost"}
    if "pytest" in sys.modules:
        loopback_hosts.add("testclient")
    legacy_loopback_clients = (
        os.environ.get("TTS_LEGACY_LOOPBACK_CLIENTS") == "1"
        and request.client is not None
        and request.client.host in loopback_hosts
    )
    if legacy_loopback_clients:
        return await call_next(request)
    
    x_token: str | None = request.headers.get("x-management-token")
    x_ext_token: str | None = request.headers.get("x-extension-token")
    has_mgmt: bool = bool(token) and x_token == token
    method: str = request.method

    def deny(detail: str) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": detail})

    # --- 0. 公开只读端点（无需任何令牌）：仅被轮询的非敏感读接口 ---
    PUBLIC_GET = {"/health", "/snapshot", "/status"}
    if method == "GET" and path in PUBLIC_GET:
        return await call_next(request)

    # --- 1. 管理端独占接口 (AppKit 专用；含控制/可暴露密钥的配置) ---
    #     控制类、/stop、/settings(读写)、/engines*(含密钥) 一律需管理令牌。
    #     未设管理令牌时（开发态）放行，保持本地开发可用。
    is_mgmt_only: bool = (
        path.startswith("/control/")
        or path == "/stop"
        or path == "/settings"
        or path.startswith("/engines")
    )
    if is_mgmt_only:
        if token and not has_mgmt:
            return deny("Unauthorized: invalid management token")
        return await call_next(request)

    # --- 2. 其余“改变状态”的请求一律默认拒绝：需管理令牌或扩展配对令牌 ---
    #     默认拒绝（而非默认放行）——此前 /seek /pause /resume /restart_audio
    #     未列入任何名单而被无鉴权放行，可被本地/局域网客户端劫持播放。
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        if has_mgmt:
            return await call_next(request)
        config: Dict[str, Any] = storage.load_config() if storage else {}
        pairing_token: str | None = config.get("extension_pairing_token")
        if pairing_token and x_ext_token == pairing_token:
            return await call_next(request)
        return deny("Unauthorized: invalid extension token or pairing required")

    # --- 3. 其余只读 GET（saved_items / cache / podcasts 列表等内容读取）放行 ---
    return await call_next(request)



@app.post("/read")
async def read_text(data: ReadRequest):
    text = data.text
    voice = data.voice
    source = data.source

    # 非原文模式：先经翻译/LLM 引擎处理文本，再走正常朗读流程
    mode = (data.mode or "original").strip()
    if text and text != "RESUME_MODE" and mode not in ("", "original"):
        from fastapi.concurrency import run_in_threadpool
        reader_dir = os.path.join(BASE_DIR, "URL-Reader")
        if reader_dir not in sys.path:
            sys.path.insert(0, reader_dir)

        def _process():
            from reader_service import process_with_llm
            return process_with_llm(text, mode)

        try:
            processed = await run_in_threadpool(_process)
            if processed and processed.strip():
                text = processed
        except Exception as e:
            event_log.record("read_mode_process_failed", mode=mode, error=str(e))
            raise HTTPException(status_code=500, detail=f"{mode} 处理失败: {e}")

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
def stop_read():
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
def get_status():
    if runtime_state is None or player is None or S is None:
        return {
            "is_playing": False,
            "is_paused": False,
            "current_podcast_file": None,
            "current_playing_md5": None,
            "title": "",
            "progress": "",
            "buffer_sec": 0,
            "status_code": "STARTING",
            "generating_title": "",
        }
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
        except Exception as e:
            print(f"[Status] Failed to scan pending podcast files: {e}")
            
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
def debug_state():
    runtime_snapshot = runtime_state.snapshot()
    return {
        **playback_service.snapshot(),
        "status_code": S.get_status(),
        **runtime_snapshot,
        **podcast_service.snapshot(),
        "active_url_tasks": list(ACTIVE_URL_TASKS.keys()),
    }

@app.get("/debug/events")
def debug_events(limit: int = 50):
    return event_log.recent(limit=limit)

@app.post("/pause")
def pause_playback():
    playback_service.pause()
    return {"status": "paused"}

@app.post("/resume")
def resume_playback():
    playback_service.resume()
    return {"status": "resumed"}

@app.post("/restart_audio")
def restart_audio():
    if player is not None:
        try:
            playback_service.restart_device()
            return {"status": "ok"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    raise HTTPException(status_code=503, detail="Player not initialized")

@app.post("/seek")
def seek_playback(data: SeekRequest):
    direction = data.direction  # 1 for next, -1 for prev
    event_log.record("seek_requested", direction=direction)
    
    runtime_state.reset_podcast_generation()
    
    state = storage.load_state()
    current_art = state.get("current_article", {})
    chunks = current_art.get("chunks", [])
    
    if not chunks:
        raise HTTPException(status_code=400, detail="No active article")
        
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

def validate_fetch_url(raw: str) -> str | None:
    """SSRF 防护：校验将要抓取的 URL。不安全时返回中文错误信息，安全返回 None。

    - scheme 仅允许 http/https（拒绝 file://、gopher:// 等）。
    - 解析主机的所有地址，若任一落在内网/环回/链路本地/保留/多播段则拒绝
      （拦截 127.0.0.1、localhost、169.254.169.254 云元数据、192.168/10/172.16 等）。
    注：解析在抓取前完成，存在 DNS rebinding 的残留 TOCTOU（抓取时再次解析可能变化）；
    彻底消除需在抓取层固定已校验 IP，留作后续增强。
    """
    import ipaddress
    import socket as _socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(raw)
    except Exception:
        return "URL 解析失败"
    if parsed.scheme not in ("http", "https"):
        return f"仅支持 http/https，已拒绝 scheme: {parsed.scheme or '(空)'}"
    host = parsed.hostname
    if not host:
        return "URL 缺少主机名"
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = _socket.getaddrinfo(host, port, proto=_socket.IPPROTO_TCP)
    except Exception as e:
        return f"无法解析主机 {host}: {e}"
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return f"非法解析地址: {ip_str}"
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            return f"拒绝访问内网/保留地址: {host} -> {ip_str}"
    return None


@app.post("/read_url")
async def read_url(payload: ReadUrlRequest) -> dict:
    global ACTIVE_URL_TASKS
    url = payload.url.strip()
    html = payload.html.strip()
    mode = payload.effective_mode()
    save = payload.save
    podcast = payload.podcast
    action = payload.action()
    if not url: raise HTTPException(status_code=400, detail="Empty URL")

    # SSRF 防护：拒绝非 http/https 及指向内网/保留地址的 URL（DNS 解析阻塞，放线程池）
    from fastapi.concurrency import run_in_threadpool
    url_err = await run_in_threadpool(validate_fetch_url, url)
    if url_err:
        event_log.record("read_url_rejected", url=url, reason=url_err)
        raise HTTPException(status_code=400, detail=url_err)

    current_time = time.time()
    for task_url, task_info in list(ACTIVE_URL_TASKS.items()):
        if current_time - task_info["timestamp"] >= 60:
            ACTIVE_URL_TASKS.pop(task_url, None)
    if url in ACTIVE_URL_TASKS:
        return {"status": "error", "message": "该网页正处于后台解析抓取中，请不要重复点击，稍候可在下方收藏列表中查看！"}
        
    job_id = f"url_{uuid.uuid4().hex[:12]}"
    ACTIVE_URL_TASKS[url] = {"timestamp": current_time, "is_podcast": podcast, "job_id": job_id}
    url_job_store.create(
        job_id=job_id,
        url=url,
        mode=mode,
        action=action,
        has_html=bool(html),
    )
    event_log.record(
        "read_url_dispatched",
        job_id=job_id,
        url=url,
        mode=mode,
        action=action,
        has_uploaded_html=bool(html),
    )

    async def run_cli_task():
        try:
            url_job_store.update(job_id, status="running", stage="starting")

            def update_stage(stage: str, fields: dict) -> None:
                url_job_store.update(job_id, status="running", stage=stage, **fields)
                event_log.record("url_job_stage", job_id=job_id, url=url, stage=stage, **fields)

            reader_dir = os.path.join(BASE_DIR, "URL-Reader")
            if reader_dir not in sys.path:
                sys.path.append(reader_dir)
            from reader_service import process_url_job

            result = await asyncio.to_thread(
                process_url_job,
                url=url,
                html=html,
                mode=mode,
                base_dir=reader_dir,
                cache_dir=os.path.join(reader_dir, "cache"),
                stage_callback=update_stage,
            )
            url_job_store.update(
                job_id,
                status="dispatching",
                stage="dispatching",
                title=result.title,
                source=result.source,
                text_chars=len(result.text),
                from_cache=result.from_cache,
                error=None,
            )

            if podcast:
                await generate_single_podcast(
                    GenerateSinglePodcastRequest(
                        text=result.text,
                        source=result.source,
                        voice=result.voice,
                        title=result.title,
                    )
                )
            elif save:
                await save_for_later(
                    SaveForLaterRequest(
                        text=result.text,
                        source=result.source,
                        voice=result.voice,
                        title=result.title,
                    )
                )
            else:
                await read_text(
                    ReadRequest(text=result.text, source=result.source, voice=result.voice)
                )

            url_job_store.update(job_id, status="done", stage="done", error=None)
            event_log.record("read_url_finished", job_id=job_id, url=url, action=action)
        except Exception as e:
            url_job_store.update(job_id, status="failed", stage="failed", error=str(e))
            event_log.record("read_url_failed", job_id=job_id, url=url, error=str(e))
        finally:
            ACTIVE_URL_TASKS.pop(url, None)
            
    if runtime_supervisor is None:
        ACTIVE_URL_TASKS.pop(url, None)
        url_job_store.update(
            job_id,
            status="failed",
            stage="failed",
            error="runtime supervisor is not ready",
        )
        raise HTTPException(status_code=503, detail="Backend is not ready")
    runtime_supervisor.create_task(run_cli_task(), job_id=job_id)
    return {"status": "ok", "job_id": job_id, "message": "Read URL task dispatched"}

@app.get("/url_jobs")
def list_url_jobs():
    return url_job_store.list()

@app.post("/delete_saved")
def delete_saved(data: DeleteSavedRequest):
    md5 = data.md5
    index = data.index
    if saved_items_service.delete(md5=md5, index=index):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Item not found")

@app.get("/podcasts/list")
def list_podcasts():
    return podcast_service.list_files()

@app.get("/podcasts/jobs")
def list_podcast_jobs():
    return podcast_service.list_jobs()

@app.post("/podcasts/toggle_pin")
def toggle_pin(data: FilenameRequest):
    return podcast_service.toggle_pin(data.filename)

@app.post("/podcasts/clear")
def clear_podcasts():
    deleted_count = podcast_service.clear_unpinned()
    return {"status": "ok", "deleted_count": deleted_count}

@app.post("/podcasts/delete")
def delete_podcast(data: FilenameRequest):
    return podcast_service.delete(data.filename)

@app.post("/podcasts/play")
def play_podcast(data: FilenameRequest):
    filename = data.filename
    filepath = podcast_service.find_file(filename)
    if not filepath:
        raise HTTPException(status_code=404, detail="File not found")
    
    event_log.record("podcast_play_requested", filename=filename, filepath=filepath)
    playback_service.play_wav_file(filepath, filename)
    return {"status": "ok"}

@app.get("/podcasts/transcript")
def get_podcast_transcript(filename: str):
    """返回播客同名 .txt 文稿（生成时写入的 sidecar）。"""
    filepath = podcast_service.find_file(filename) if podcast_service else None
    if not filepath:
        return {"text": ""}
    txt_path = (filepath[:-4] if filepath.endswith(".wav") else filepath) + ".txt"
    try:
        if os.path.exists(txt_path):
            with open(txt_path, "r", encoding="utf-8") as f:
                return {"text": f.read()}
    except Exception:
        pass
    return {"text": ""}

@app.post("/save_for_later")
async def save_for_later(data: SaveForLaterRequest):
    runtime_state.touch_activity()
    text = data.text.strip()
    source = data.source
    voice = data.voice
    title = data.title
    if not text: raise HTTPException(status_code=400, detail="Empty text")

    from fastapi.concurrency import run_in_threadpool
    count = await run_in_threadpool(saved_items_service.save, text, source, voice, title)
    event_log.record("saved_item_added", source=source, voice=voice, title=title, text_chars=len(text))
    return {"status": "saved", "count": count}

@app.post("/generate_single_podcast")
async def generate_single_podcast(data: GenerateSinglePodcastRequest):
    runtime_state.touch_activity()

    text = data.text.strip()
    source = data.source
    voice = data.voice
    title = data.title
    if not text: raise HTTPException(status_code=400, detail="Empty text")
    
    md5_val = hashlib.md5(text.encode("utf-8")).hexdigest()
    
    # 如果检测到相同内容的任务已在生成中，直接返回成功状态（不需要重复排队）
    if podcast_service.is_generating(md5_val):
        return {"status": "generating", "md5": md5_val, "message": "该内容已在后台生成中，无需重复提交！"}

    from fastapi.concurrency import run_in_threadpool
    config = await run_in_threadpool(storage.load_config)
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
def clear_saved_items():
    saved_items_service.clear()
    return {"status": "ok"}

@app.post("/generate_podcast")
def generate_podcast_api():
    saved_items = saved_items_service.load()
    if not saved_items: raise HTTPException(status_code=400, detail="No saved items")
    
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
def get_saved_items():
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
    if not indices: raise HTTPException(status_code=400, detail="No items selected")
    saved_items = saved_items_service.load()
    if not saved_items: raise HTTPException(status_code=400, detail="Queue empty")
    text_to_play, voice, selected_md5 = saved_items_service.selected_text(indices)
    if not text_to_play.strip(): raise HTTPException(status_code=400, detail="Selected items are empty")
    runtime_state.set_current_media(podcast=None, md5=selected_md5)

    payload = ReadRequest(text=text_to_play, from_saved=True, voice=voice)

    return await read_text(payload)



@app.get("/cache/items")
def get_cache_items():
    return cache_service.list_items()

@app.post("/cache/play")
async def play_cache(data: Md5Request):
    md5 = data.md5
    text = cache_service.get_text(md5)
    if text is None: raise HTTPException(status_code=404, detail="Cache not found")
    return await read_text(ReadRequest(text=text))

@app.post("/cache/export")
async def export_cache(data: Md5Request):
    md5 = data.md5
    text = cache_service.get_text(md5)
    if text is None: raise HTTPException(status_code=404, detail="Cache not found")
    return await generate_single_podcast(
        GenerateSinglePodcastRequest(text=text, source="cache")
    )

@app.post("/cache/delete")
def delete_cache(data: Md5Request):
    cache_service.delete(data.md5)
    return {"status": "ok"}

@app.post("/cache/clear")
def clear_cache_endpoint():
    cache_service.clear()
    return {"status": "ok"}


@app.get("/health")
def get_health():
    return {
        "status": "ready",
        "instance_id": INSTANCE_ID,
        "pid": os.getpid(),
        "managed": os.environ.get("TTS_WATCHDOG_FD") is not None,
        "accepting_requests": runtime_supervisor.accepting_requests if runtime_supervisor else True
    }


@app.get("/snapshot")
def get_snapshot():
    runtime_snapshot = runtime_state.snapshot() if runtime_state else {}
    playback_snap = playback_service.snapshot() if playback_service else {}
    podcast_snap = podcast_service.snapshot() if podcast_service else {}
    
    state = storage.load_state() if storage else {}
    current_art = state.get("current_article", {})
    chunks = current_art.get("chunks", [])
    chunks_clean = [c["text"] if isinstance(c, dict) else c for c in chunks]
    # 持久化的 current_index 作为非播放态/恢复时的回退值。
    current_index = current_art.get("current_index", 0)

    # 实时播放时，以播放器的实时句子索引为准——player.currently_playing_index 由
    # 音频回调更新，覆盖流式 /read 与保存-WAV 两条播放路径（统一权威）。
    # /snapshot 是只读端点：**只读不写** state.json，避免与播放线程、/seek 的写入
    # 相互竞争导致索引回退/丢更新（P1A 修复的核心仍然保留）。
    if player is not None and player.currently_playing_index is not None and player.currently_playing_index >= 0:
        curr_idx = player.currently_playing_index
        if curr_idx < len(chunks):
            current_index = curr_idx
            # 实时覆盖进度串，避免显示上一次播放残留的 main_progress
            runtime_snapshot["main_progress"] = f"{curr_idx + 1}/{len(chunks)}"

    return {
        **playback_snap,
        "status_code": S.get_status() if S else "IDLE",
        **runtime_snapshot,
        **podcast_snap,
        "active_url_tasks": list(ACTIVE_URL_TASKS.keys()),
        "instance_id": INSTANCE_ID,
        "current_article_chunks": chunks_clean,
        "current_article_index": current_index,
    }


@app.get("/settings")
def get_settings():
    if storage is None:
        raise HTTPException(status_code=503, detail="Storage not initialized")
    return storage.load_config()


@app.patch("/settings")
def patch_settings(update_data: SettingsUpdateRequest):
    if storage is None:
        raise HTTPException(status_code=503, detail="Storage not initialized")
    config = storage.load_config()
    # 过滤掉 None 值，仅更新传入的字段
    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}
    config.update(update_dict)
    storage.save_config(config)
    return {"status": "ok", "config": config}


def _deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge_dict(base[k], v)
        else:
            base[k] = v
    return base


def _default_engines() -> Dict[str, Any]:
    """Locked default engines schema — single source of truth in
    URL-Reader/engine_config.py (此前 backend.py 另存一份会与之漂移)。"""
    import copy
    reader_dir = os.path.join(BASE_DIR, "URL-Reader")
    if reader_dir not in sys.path:
        sys.path.append(reader_dir)
    from engine_config import DEFAULT_ENGINES
    return copy.deepcopy(DEFAULT_ENGINES)


@app.get("/engines")
def get_engines():
    if storage is None:
        raise HTTPException(status_code=503, detail="Storage not initialized")
    config = storage.load_config()
    stored = config.get("engines")
    # 返回 默认 + 存储 的合并结果，确保新 schema 字段（如 models）始终出现，
    # 与引擎实际读取的 load_engines() 合并逻辑一致。
    merged = _default_engines()
    if isinstance(stored, dict):
        _deep_merge_dict(merged, stored)
    return merged


@app.patch("/engines")
def patch_engines(update: Dict[str, Any]):
    if storage is None:
        raise HTTPException(status_code=503, detail="Storage not initialized")
    config = storage.load_config()
    engines = config.get("engines")
    if not isinstance(engines, dict):
        engines = _default_engines()
    _deep_merge_dict(engines, update or {})
    config["engines"] = engines
    storage.save_config(config)
    return {"status": "ok"}


@app.post("/engines/check")
async def check_engine(payload: Dict[str, Any]):
    """检测某个 provider 是否连通。可在 body 带 key/region 先持久化再探测。
    body: {family: 'llm'|'translate', provider: str, key?: str, region?: str}"""
    from fastapi.concurrency import run_in_threadpool

    family = (payload.get("family") or "").strip()
    provider = (payload.get("provider") or "").strip()
    key = payload.get("key")
    region = payload.get("region")
    if not family or not provider:
        return {"ok": False, "message": "缺少 family 或 provider"}

    # 若带了凭据，先写入 config（不改 selected），让引擎 provider 读到
    if storage is not None and key:
        config = storage.load_config()
        engines = config.get("engines")
        if not isinstance(engines, dict):
            engines = _default_engines()
        if family == "llm":
            llm = engines.setdefault("llm", {})
            if provider == "local":
                llm["local_model_path"] = key
            else:
                llm.setdefault("keys", {})[provider] = key
        elif family == "translate":
            tr = engines.setdefault("translate", {})
            if provider == "microsoft":
                tr["microsoft_key"] = key
                if region:
                    tr["microsoft_region"] = region
            elif provider == "deepl":
                tr["deepl_key"] = key
        config["engines"] = engines
        storage.save_config(config)

    reader_dir = os.path.join(BASE_DIR, "URL-Reader")
    if reader_dir not in sys.path:
        sys.path.insert(0, reader_dir)

    def _probe():
        try:
            if family == "llm":
                from llm_engine import probe_provider
                return probe_provider(provider)
            from translation_engine import probe_provider
            return probe_provider(provider)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    ok, message = await run_in_threadpool(_probe)
    return {"ok": bool(ok), "message": message}


@app.post("/control/heartbeat")
def post_heartbeat():
    if runtime_state:
        runtime_state.touch_activity()
    return {"status": "ok"}


@app.post("/control/shutdown")
def post_shutdown():
    import signal
    
    def trigger_sigterm():
        time.sleep(0.1)
        if "pytest" not in sys.modules:
            os.kill(os.getpid(), signal.SIGTERM)
        else:
            print("[Backend] Pytest environment detected via sys.modules. Skipping self-kill.")
        
    threading.Thread(target=trigger_sigterm, daemon=True).start()
    return {"status": "shutting_down"}




if __name__ == "__main__":
    port = int(os.environ.get("TTS_BACKEND_PORT", 8001))
    host = os.environ.get("TTS_BACKEND_HOST", "127.0.0.1")
    if port == 0:
        # Ephemeral port: bind ourselves, capture the OS-assigned port, then let
        # uvicorn serve the pre-bound socket. runtime.json (written in lifespan
        # startup) publishes BOUND_PORT so the app/extension can discover it.
        import socket as _socket
        _sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        _sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        _sock.bind((host, 0))
        BOUND_PORT = _sock.getsockname()[1]
        print(f"[Backend] OS-assigned ephemeral port: {BOUND_PORT}")
        uvicorn.Server(uvicorn.Config(app, log_level="error")).run(sockets=[_sock])
    else:
        uvicorn.run(app, host=host, port=port, log_level="error")
