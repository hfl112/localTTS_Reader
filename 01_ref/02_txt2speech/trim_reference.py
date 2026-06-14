import soundfile as sf
import os

def trim_audio(file_path, target_duration_sec=7.58):
    if not os.path.exists(file_path):
        print(f"文件不存在: {file_path}")
        return

    # 读取音频
    data, sr = sf.read(file_path)
    current_duration = len(data) / sr
    print(f"当前音频时长: {current_duration:.3f} 秒")

    # 计算目标采样数
    target_samples = int(sr * target_duration_sec)
    
    if target_samples >= len(data):
        print("目标时长大于或等于当前时长，无需裁剪。")
        return

    # 执行裁剪
    trimmed_data = data[:target_samples]
    
    # 覆盖原文件
    sf.write(file_path, trimmed_data, sr)
    print(f"已裁剪至 {target_duration_sec} 秒并保存。新的爆音问题应该已解决。")

if __name__ == "__main__":
    audio_path = "/Users/funanhe/Documents/0.MyCode/TTS/02_txt2speech/serena_ref_8s.wav"
    trim_audio(audio_path, 7.58)
