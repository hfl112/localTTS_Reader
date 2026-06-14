import os
import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel

# 配置
local_model_path = "/Users/funanhe/Documents/0.MyCode/TTS/qwen3-tts/checkpoints/0.6b_custom"
output_dir = "pronunciation_test"
os.makedirs(output_dir, exist_ok=True)

# 加载模型 (用你最满意的 Serena 0.6B)
print("正在加载模型...")
model = Qwen3TTSModel.from_pretrained(local_model_path, device_map="cpu", dtype=torch.float32)

# 测试方案
tests = [
    {"name": "Original", "text": "她的名字叫楚玊。"},
    {"name": "Pinyin_Format_1", "text": "她的名字叫楚玊(su4)。"},
    {"name": "Pinyin_Format_2", "text": "她的名字叫楚玊[su4]。"},
    {"name": "Substitution", "text": "她的名字叫楚素。"}, # 替换为同音字“素”
    {"name": "Padding", "text": "她的名字叫楚 玊 。"}
]

print("\n--- 开始读音纠错测试 ---")

for t in tests:
    print(f"正在测试方案: {t['name']} -> {t['text']}")
    wavs, sr = model.generate_custom_voice(
        text=t['text'],
        language="chinese",
        speaker="Serena",
        instruct="Normal"
    )
    file_path = os.path.join(output_dir, f"{t['name']}.wav")
    sf.write(file_path, wavs[0], sr)

print(f"\n测试完成！请检查 '{output_dir}' 目录下的音频，看看哪个念对了。")
