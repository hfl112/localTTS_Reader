import os
import torch
import soundfile as sf
import numpy as np
import re
import time
from qwen_tts import Qwen3TTSModel

# ================= 配置区 =================
MODEL_PATH_06B = "/Users/funanhe/Documents/0.MyCode/TTS/qwen3-tts/checkpoints/0.6b_custom"
MODEL_PATH_17B = "/Users/funanhe/Documents/0.MyCode/TTS/qwen3-tts/checkpoints/1.7b"
OUTPUT_DIR = "final_stable_production"

# 缓存模型 (强制 CPU 模式)
_models = {}

def get_model(version="0.6b"):
    if version not in _models:
        path = MODEL_PATH_06B if version == "0.6b" else MODEL_PATH_17B
        print(f"\n[加载] 正在加载 {version} 模型 (CPU 稳定版)...")
        # 强制 CPU + float32 保证 100% 还原质量
        _models[version] = Qwen3TTSModel.from_pretrained(path, device_map="cpu", dtype=torch.float32)
    return _models[version]

def generate_speech(text, mode="news"):
    """
    智能段落切分模式：
    1. 优先按段落 (\n) 切分，保持段内语流连贯。
    2. 如果段落过长 (> 300字)，则在段内找句号切分。
    """
    version = "1.7b" if mode == "news" else "0.6b"
    speaker = "Vivian" if mode == "news" else "Serena"
    
    if mode == "news":
        instruct = "A professional, calm, and elegant female voice, speaking with a steady and sophisticated tone. Please speak slightly faster for a crisp and efficient delivery."
    else:
        instruct = "Normal"
    
    model = get_model(version)

    # --- 智能切分逻辑 ---
    # 首先按换行符切成段落
    paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    final_chunks = []
    
    for p in paragraphs:
        # 如果段落长度在安全范围内 (例如 300 字)，直接作为一整块处理
        if len(p) <= 300:
            final_chunks.append(p)
        else:
            # 如果段落太长，按句号切分，防止模型崩溃
            sub_segments = re.split(r'([。！？；])', p)
            temp_chunk = ""
            for i in range(0, len(sub_segments)-1, 2):
                sentence = sub_segments[i] + sub_segments[i+1]
                if len(temp_chunk) + len(sentence) <= 300:
                    temp_chunk += sentence
                else:
                    if temp_chunk: final_chunks.append(temp_chunk)
                    temp_chunk = sentence
            if temp_chunk: final_chunks.append(temp_chunk)

    # --- 开始生成 ---
    combined_wav = []
    sr = 24000
    print(f"  -> [CPU] 正在使用 {speaker}({version}) 进行智能段落合成 (共 {len(final_chunks)} 个区块)...")
    
    start_time = time.time()
    for i, chunk in enumerate(final_chunks):
        print(f"    处理第 {i+1} 块: {chunk[:15]}...")
        wav_chunk, current_sr = model.generate_custom_voice(
            text=chunk,
            language="chinese",
            speaker=speaker,
            instruct=instruct
        )
        sr = current_sr
        combined_wav.append(wav_chunk[0])
        # 区块之间加入 0.6 秒的自然停顿 (换气感)
        combined_wav.append(np.zeros(int(sr * 0.6)))
    
    gen_time = time.time() - start_time
    return np.concatenate(combined_wav), sr, gen_time

# ================= 运行生产 =================
if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 测试长文本（多段落）
    news_content = """
今日科技快讯：人工智能领域取得重大突破。Qwen3-TTS 系列模型正式发布，正在重新定义人机交互的未来。
全球开发者正积极探索其在各行业的广泛应用，为用户带来更加智能的体验。我们期待这一技术能在医疗、教育等领域发挥更大的作用。
"""

    story_content = """
在一个遥远的星球上，住着一位爱听故事的小精灵。每天晚上，他都会坐在月亮边上，摇晃着双腿，等待着来自地球的声音。
“今晚会是什么样的冒险呢？” 他自言自语道。就在这时，一道流星划过天际，带来了一段从未听过的奇妙旋律。
"""

    # 1. 生成 Vivian (1.7B CPU - 智能段落版)
    wav_v, sr_v, cost_v = generate_speech(news_content, mode="news")
    sf.write(os.path.join(OUTPUT_DIR, "Vivian_Smart_Paragraph.wav"), wav_v, sr_v)
    print(f"    [成功] Vivian 已保存 (耗时: {cost_v:.2f}s)")

    # 2. 生成 Serena (0.6B CPU - 智能段落版)
    wav_s, sr_s, cost_s = generate_speech(story_content, mode="story")
    sf.write(os.path.join(OUTPUT_DIR, "Serena_Smart_Paragraph.wav"), wav_s, sr_s)
    print(f"    [成功] Serena 已保存 (耗时: {cost_s:.2f}s)")

    print(f"\n✅ 智能段落版生成完毕！请查看目录: {OUTPUT_DIR}")

