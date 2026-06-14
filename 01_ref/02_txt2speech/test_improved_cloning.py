import os
import torch
import soundfile as sf
import numpy as np
from qwen_tts_engine import QwenTTSEngine

# ================= 配置 =================
REF_AUDIO_FULL = "/Users/funanhe/Documents/0.MyCode/TTS/02_txt2speech/final_stable_production/Serena_LongText_Consistent.wav"
# 参考音频的前两句文本
REF_TEXT_SHORT = "很久很久以前，森林里住着一只爱唱歌的小兔子。今天，Serena 想为你分享这个温暖的故事。"
OUTPUT_DIR = "improved_cloning_results"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# 1. 准备精简的参考音频 (只取前 8 秒)
print("[Step 1] 正在截取精简参考音频 (前 8 秒)...")
data, sr = sf.read(REF_AUDIO_FULL)
short_ref_data = data[:int(sr * 8)] # 截取前 8 秒
REF_AUDIO_SHORT = "serena_ref_8s.wav"
sf.write(REF_AUDIO_SHORT, short_ref_data, sr)

# 2. 初始化升级后的引擎
engine = QwenTTSEngine()

# 3. 准备包含难念字的测试文本
test_text = """
楚玊（su4）静静地站在玉石前。她试探楚玊说：“你知道我的这个秘密。”
从前有座山，山里有座庙。在这个宁静的小村庄里，住着一位温柔的守护者。
我们要测试的是跨段落的淡入淡出效果，以及自动纠正生僻字读音的能力。
"""

print("\n[Step 2] 启动改进版克隆合成...")
try:
    # 调用引擎的 clone 模式
    # 引擎会自动：1. 修正 玊 的读音 2. 分段合成 3. 淡入淡出拼接
    wav, sr = engine.generate(
        text=test_text,
        mode="clone",
        ref_audio=REF_AUDIO_SHORT,
        ref_text=REF_TEXT_SHORT
    )
    
    save_path = os.path.join(OUTPUT_DIR, "Serena_Improved_Seamless_Cloning.wav")
    sf.write(save_path, wav, sr)
    
    print(f"\n✅ 测试成功！请检查: {save_path}")
    print("重点关注：1. '楚玊' 是否念对了 2. 段落衔接是否比之前顺滑。")

except Exception as e:
    print(f"\n❌ 出错: {e}")
