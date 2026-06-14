import os
import torch
import soundfile as sf
import time
import numpy as np
import re
from qwen_tts import Qwen3TTSModel

# ================= 配置区 =================
MODEL_PATH_BASE = "/Users/funanhe/Documents/0.MyCode/TTS/qwen3-tts/checkpoints/1.7b_base"
REF_AUDIO_PATH = "/Users/funanhe/Documents/0.MyCode/TTS/02_txt2speech/final_stable_production/Serena_LongText_Consistent.wav"
# 参考音频对应的文本 (模型需要知道参考音频里在说什么，才能提取纯净的音色)
REF_TEXT = "很久很久以前，森林里住着一只爱唱歌的小兔子。今天，Serena 想为你分享这个温暖的故事。"
OUTPUT_DIR = "cloning_consistency_test"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# 1. 加载 1.7B Base 模型
print(f"正在加载 1.7B Base 模型 (用于声音克隆): {MODEL_PATH_BASE}")
model = Qwen3TTSModel.from_pretrained(
    MODEL_PATH_BASE, 
    device_map="cpu", 
    dtype=torch.float32
)

# 2. 读取要朗读的小说文本 (前 50 行)
with open("novel_test_50.txt", "r", encoding="utf-8") as f:
    novel_text = f.read()

def generate_with_cloning(text):
    """
    使用固定参考音频进行克隆合成
    """
    # 依然使用智能分段，但在每一段都强制传入同一个参考音频
    paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    combined_wav = []
    sr = 24000
    
    print(f"--- 开始克隆合成 (使用固定 Serena 参考) ---")
    
    for i, p in enumerate(paragraphs):
        print(f"  正在处理第 {i+1} 段...")
        # 调用 generate_voice_clone 接口
        wav_chunk, current_sr = model.generate_voice_clone(
            text=p,
            language="chinese",
            ref_audio=REF_AUDIO_PATH,
            ref_text=REF_TEXT
        )
        sr = current_sr
        combined_wav.append(wav_chunk[0])
        combined_wav.append(np.zeros(int(sr * 0.5))) # 加入 0.5s 停顿
        
    return np.concatenate(combined_wav), sr

# 运行合成
try:
    start_time = time.time()
    final_wav, sr = generate_with_cloning(novel_text)
    
    save_path = os.path.join(OUTPUT_DIR, "Serena_Cloned_Consistency_Test.wav")
    sf.write(save_path, final_wav, sr)
    
    print(f"\n✅ 合成成功！耗时: {time.time() - start_time:.2f}s")
    print(f"请检查文件: {save_path}")
    print("理论上，由于每一段都参考了同一个音频，音色的一致性会显著提升。")

except Exception as e:
    print(f"\n❌ 出错: {e}")
