import os
import sys
import numpy as np
import scipy.io.wavfile as wavfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "QwenTTS-App")))
from core.tts_engine import TTSEngine

def main():
    engine = TTSEngine(mlx_audio_path="../../mlx_audio")
    
    text = "各位听众大家好，欢迎收听本期的新闻快报，我是男主持瑞恩。"
    instruct = "A professional male news anchor, clear, deep, and steady."
    
    print("="*50)
    print("🎲 开始为你随机抽卡 Ryan 的播音腔男声...")
    print("="*50)
    
    # 尝试 5 个不同的随机种子，看看哪个能在 1.7B 里面爆出极品男声
    seeds_to_try = [42, 123, 888, 9999, 2026]
    
    for idx, seed in enumerate(seeds_to_try):
        print(f"\n[抽卡 {idx+1}/5] 正在使用 Seed={seed} 生成...")
        chunk_config = {
            "voice": "Ryan",
            "seed": seed,
            "instruct": instruct,
            "temperature": 0.3, # 稍微给点温度增加抽卡多样性
            "repetition_penalty": 1.1,
            "speed": 1.0,
            "lang_code": "zh"
        }
        
        audio_chunks = []
        for chunk in engine.generate_stream(text, chunk_config):
            audio_chunks.append(chunk)
            
        if audio_chunks:
            audio_data = np.concatenate(audio_chunks)
            audio_data_int16 = np.int16(audio_data * 32767)
            filename = f"ryan_gacha_seed_{seed}.wav"
            wavfile.write(filename, 24000, audio_data_int16)
            print(f"[✓] 生成完毕，保存为: {filename}")

if __name__ == "__main__":
    main()
