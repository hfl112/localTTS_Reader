import os

# 1. 彻底禁用 tqdm 监控
os.environ["TQDM_DISABLE"] = "1"
os.environ["TQDM_MONITOR_INTERVAL"] = "0"
try:
    import tqdm
    tqdm._monitor.TqdmMonitor = None
    tqdm.tqdm.monitor_interval = 0
except:
    pass

import sys
import uvicorn
from fastapi import FastAPI, Body
from contextlib import asynccontextmanager
import threading
import queue
import mlx.core as mx
import time
import traceback

# 确保能找到 core 目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from core.tts_engine import TTSEngine
from core.player import PCMPlayer
from core.processor import TextProcessor
from core.storage import Storage

# 全局组件
storage = Storage(data_dir=os.path.join(BASE_DIR, "data"))
player = PCMPlayer()
processor = TextProcessor()
engine = None # 将在专用线程中初始化

# 任务队列
task_queue = queue.Queue()

# 全局状态
class GlobalState:
    is_playing = False
    stop_event = threading.Event()
    current_title = ""
    current_progress = "0/0"

def tts_worker_thread():
    """
    【绝对核心】专用的 TTS 推理线程
    保证模型加载、推理、播放都在同一个线程内完成，解决 MLX Stream 绑定问题
    """
    global engine
    
    print("[WorkerThread] 正在初始化 MLX 环境...")
    try:
        mx.set_default_device(mx.gpu)
        engine = TTSEngine(
            model_path="models/Qwen3-TTS-1.7B-8bit", 
            mlx_audio_path="../../mlx_audio"
        )
        engine.ensure_model_loaded()
        print("[WorkerThread] 模型加载成功，准备就绪")
    except Exception as e:
        print(f"[WorkerThread] 环境初始化失败: {e}")
        return

    while True:
        try:
            # 等待新任务
            task = task_queue.get()
            if task is None: break # 退出信号
            
            # 使用 .get 提供默认值，防止 KeyError
            text = task.get('text', "")
            index = task.get('index', 0)
            is_resume = (text == "RESUME_MODE")
            
            # 加载最新配置
            config = storage.load_config()
            state = storage.load_state()
            speed = config.get("speed", 1.0)
            
            GlobalState.stop_event.clear()
            GlobalState.is_playing = True
            
            # 处理切片
            if is_resume:
                current_art = state.get("current_article", {})
                chunks = current_art.get("chunks", [])
                curr_idx = current_art.get("current_index", 0) if index == -1 else index
            else:
                chunks = processor.smart_split(text)
                state["current_article"] = {
                    "title": text[:15].replace("\n", " ") + "...",
                    "chunks": chunks,
                    "current_index": 0
                }
                storage.save_state(state)
                curr_idx = 0
            
            GlobalState.current_title = state["current_article"]["title"]
            
            if curr_idx < len(chunks):
                player.start(speed=speed)
                
                while curr_idx < len(chunks) and not GlobalState.stop_event.is_set():
                    chunk_text = chunks[curr_idx]
                    GlobalState.current_progress = f"{curr_idx+1}/{len(chunks)}"
                    print(f"[WorkerThread] 正在朗读: {GlobalState.current_progress}")
                    
                    for pcm_chunk in engine.generate_stream(chunk_text, config):
                        if GlobalState.stop_event.is_set():
                            break
                        player.play_chunk(pcm_chunk)
                    
                    if not GlobalState.stop_event.is_set():
                        curr_idx += 1
                        # 更新持久化进度
                        s = storage.load_state()
                        s["current_article"]["current_index"] = curr_idx
                        storage.save_state(s)
                
                player.stop(graceful=not GlobalState.stop_event.is_set())
            
            GlobalState.is_playing = False
            task_queue.task_done()
            print("[WorkerThread] 任务处理完成")
            
        except Exception as e:
            print(f"[WorkerThread] 运行报错: {e}")
            traceback.print_exc()
            GlobalState.is_playing = False

# 使用现代的 lifespan 管理 FastAPI 启动和关闭
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动工作线程
    worker = threading.Thread(target=tts_worker_thread, daemon=True)
    worker.start()
    yield
    # 关闭时发送退出信号
    task_queue.put(None)

app = FastAPI(lifespan=lifespan)

@app.post("/read")
async def read_text(data: dict = Body(...)):
    # 停止当前正在播放的任务
    GlobalState.stop_event.set()
    player.stop()
    
    # 将新任务放入队列
    task_queue.put(data)
    return {"status": "ok"}

@app.post("/stop")
async def stop_read():
    GlobalState.stop_event.set()
    player.stop()
    return {"status": "ok"}

@app.get("/status")
async def get_status():
    return {
        "is_playing": GlobalState.is_playing,
        "title": GlobalState.current_title,
        "current_index": GlobalState.current_progress.split('/')[0] if "/" in GlobalState.current_progress else 0,
        "total_chunks": GlobalState.current_progress.split('/')[1] if "/" in GlobalState.current_progress else 0
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="error")
