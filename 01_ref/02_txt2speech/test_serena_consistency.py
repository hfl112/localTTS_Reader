import os
import torch
import soundfile as sf
import time
from qwen_tts import Qwen3TTSModel

# 配置
local_model_path_06b = "/Users/funanhe/Documents/0.MyCode/TTS/qwen3-tts/checkpoints/0.6b_custom"
local_model_path_17b = "/Users/funanhe/Documents/0.MyCode/TTS/qwen3-tts/checkpoints/1.7b"
output_dir = "consistency_test"
os.makedirs(output_dir, exist_ok=True)

# 读取测试文本
with open("novel_test_50.txt", "r", encoding="utf-8") as f:
    test_text = f.read()

def test_consistency(version):
    path = local_model_path_06b if version == "0.6b" else local_model_path_17b
    print(f"\n[测试] 正在加载 {version} 模型进行一致性测试...")
    
    # 强制 CPU + float32 排除硬件干扰
    model = Qwen3TTSModel.from_pretrained(path, device_map="cpu", dtype=torch.float32)
    
    print(f"  -> 正在生成 ({version} Serena)... 请稍候...")
    start_time = time.time()
    
    # 我们直接生成一整块（800字左右），不分段，看看 1.7B 能不能扛住
    # 这是测试一致性的最高强度方式
    try:
        wavs, sr = model.generate_custom_voice(
            text=test_text,
            language="chinese",
            speaker="Serena",
            instruct="Normal"
        )
        
        file_path = os.path.join(output_dir, f"Serena_{version}_FullText.wav")
        sf.write(file_path, wavs[0], sr)
        print(f"  [成功] 耗时: {time.time() - start_time:.2f}s, 保存至: {file_path}")
    except Exception as e:
        print(f"  [失败] {version} 崩溃了: {e}")

# 运行对比
test_consistency("1.7b")
test_consistency("0.6b")

print(f"\n测试完成！请对比 '{output_dir}' 中的两个文件。")
print("重点听：1.7B 在读到后面时是否比 0.6B 更像同一个人？")
