import os
import sys
import numpy as np
import scipy.io.wavfile as wavfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "QwenTTS-App")))
from core.tts_engine import TTSEngine

def main():
    print("="*50)
    print("🎙️ 正在测试终极 ICL（参考音频）音色克隆...")
    print("="*50)
    
    # 初始化引擎
    engine = TTSEngine(mlx_audio_path="../../mlx_audio")
    
    # 简单的四句双人对话测试
    dialogue = [
        {"voice": "Serena", "text": "欢迎大家，我是女主持 Serena，我们终于解决了音色跳变的绝症。"},
        {"voice": "Ryan", "text": "大家好，我是男主持 Ryan。没错，刚才我还在变来变去，现在感觉自己稳如泰山。"},
        {"voice": "Serena", "text": "这是因为我们用了传说中的 ICL 技术，相当于给我们的声音拍了个快照。"},
        {"voice": "Ryan", "text": "太神奇了！那以后不管剧本多长，我们都不会再变成女声或者大叔音了。"}
    ]
    
    all_audio = []
    
    for i, line in enumerate(dialogue):
        voice = line["voice"]
        text = line["text"]
        print(f"[{voice} 正在录制 ({i+1}/{len(dialogue)})]: {text[:30]}...")
        
        chunk_config = {
            "voice": voice,
            "seed": 42,
            "instruct": "A professional male anchor." if voice == "Ryan" else "A professional female anchor.",
            "temperature": 0.2,
            "repetition_penalty": 1.1,
            "speed": 1.0,
            "lang_code": "zh"
        }
        
        # 强行注入 ICL 参数
        if voice == "Ryan":
            chunk_config["ref_audio"] = "ref_ryan.wav"
            chunk_config["ref_text"] = "大家好，我是这个播客的男主持人，今天很高兴能和大家一起分享。"
        else:
            chunk_config["ref_audio"] = "ref_serena.wav"
            chunk_config["ref_text"] = "各位听众朋友大家好，我是播客女主持，欢迎来到今天的节目。"
            
        current_audio = []
        for chunk in engine.generate_stream(text, chunk_config):
            current_audio.append(chunk)
            
        if current_audio:
            line_audio = np.concatenate(current_audio)
            all_audio.append(line_audio)
            
            pause_frames = int(24000 * 0.5)
            pause_array = np.zeros((pause_frames, 2), dtype=np.float32) if len(line_audio.shape) == 2 else np.zeros(pause_frames, dtype=np.float32)
            all_audio.append(pause_array)

    print("\n[✓] 所有对话合成完毕，正在保存...")
    final_audio = np.concatenate(all_audio)
    final_audio_int16 = np.int16(final_audio * 32767)
    
    output_filename = "test_icl_podcast.wav"
    wavfile.write(output_filename, 24000, final_audio_int16)
    print(f"[✓] 播客已保存至: {os.path.abspath(output_filename)}")

if __name__ == "__main__":
    main()
