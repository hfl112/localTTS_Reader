import os
import sys
import argparse
import time
import psutil
import mlx.core as mx

# 确保能找到 core 目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from core.processor import TextProcessor
from core.tts_engine import TTSEngine
from core.player import PCMPlayer
from core.storage import Storage

def get_mem_usage():
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    # RSS: 常驻内存, MLX Peak: MLX峰值显存占用
    return mem_info.rss / 1024 / 1024, mx.get_peak_memory() / 1024 / 1024

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--speed", type=float, default=1.0)
    args = parser.parse_args()

    # 初始化
    storage = Storage(data_dir=os.path.join(BASE_DIR, "data"))
    config = storage.load_config()
    state = storage.load_state()
    processor = TextProcessor()
    engine = TTSEngine(
        model_path="models/Qwen3-TTS-1.7B-8bit", 
        mlx_audio_path="../../mlx_audio"
    )
    player = PCMPlayer()

    # 处理文本
    if args.text == "RESUME_MODE":
        print("[Worker] 进入恢复模式")
        if not state.get("current_article") or not state["current_article"].get("chunks"):
            print("[Worker] 错误：没有找到可恢复的文章内容")
            return
        chunks = state["current_article"]["chunks"]
        state["current_article"]["current_index"] = args.index
    else:
        print("[Worker] 进入新朗读模式")
        chunks = processor.smart_split(args.text)
        state["current_article"] = {
            "title": args.text[:15].replace("\n", " ") + "...",
            "chunks": chunks,
            "current_index": 0
        }
        storage.save_state(state)

    if state["current_article"]["current_index"] >= len(chunks):
        print("[Worker] 提示：该文章已朗读完毕")
        return

    # 执行朗读
    try:
        engine.ensure_model_loaded()
        rss, mlx_p = get_mem_usage()
        print(f"[Worker] 模型加载后内存: RSS={rss:.1f}MB, MLX-Peak={mlx_p:.1f}MB")
        
        player.start(speed=args.speed)
        
        while state["current_article"]["current_index"] < len(chunks):
            idx = state["current_article"]["current_index"]
            text = chunks[idx]
            
            print(f"[Worker] 正在朗读第 {idx+1}/{len(chunks)} 段...")
            for pcm_chunk in engine.generate_stream(text, config):
                player.play_chunk(pcm_chunk)
            
            state["current_article"]["current_index"] += 1
            storage.save_state(state)
            
            rss, mlx_p = get_mem_usage()
            print(f"[Worker] 进度: {state['current_article']['current_index']}/{len(chunks)}, 内存: RSS={rss:.1f}MB, MLX-Peak={mlx_p:.1f}MB")
            
        # 给 ffplay 留出最后一点时间把 buffer 播完
        print("[Worker] 等待音频淡出...")
        player.stop(graceful=True)
        time.sleep(1.0) 
        
    except Exception as e:
        print(f"[Worker] 发生错误: {e}")
    finally:
        rss, _ = get_mem_usage()
        print(f"[Worker] 任务结束，最终常驻内存: {rss:.1f}MB")

if __name__ == "__main__":
    main()
