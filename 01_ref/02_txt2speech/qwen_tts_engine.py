import os
import torch
import soundfile as sf
import numpy as np
import re
import time
from qwen_tts import Qwen3TTSModel

class QwenTTSEngine:
    """
    Qwen3-TTS 生产级语音合成引擎
    封装了 Vivian (1.7B 新闻) 和 Serena (0.6B 故事) 的最佳实践配置。
    """
    
    def __init__(self):
        # 路径配置
        self.path_06b = "/Users/funanhe/Documents/0.MyCode/TTS/qwen3-tts/checkpoints/0.6b_custom"
        self.path_17b = "/Users/funanhe/Documents/0.MyCode/TTS/qwen3-tts/checkpoints/1.7b"
        self.path_17b_base = "/Users/funanhe/Documents/0.MyCode/TTS/qwen3-tts/checkpoints/1.7b_base"
        self.device = "cpu"
        
        # 读音纠错字典 (Key: 原字, Value: 拼音或同音字)
        # 优先使用带声调的拼音 (如 sù)，模型对此支持最稳
        self.pronunciation_map = {
            "玊": "sù",
            # 可以在这里继续添加其他念错的字
        }
        
        # 模型缓存
        self._models = {}
        
    def _apply_corrections(self, text):
        """应用读音纠错"""
        for char, replacement in self.pronunciation_map.items():
            text = text.replace(char, replacement)
        return text

    def _crossfade_stitch(self, chunks, sr, fade_ms=100):
        """
        智能淡入淡出拼接音频片段，使衔接更自然。
        """
        if not chunks: return np.array([])
        fade_len = int(sr * fade_ms / 1000)
        output = chunks[0]
        
        for i in range(1, len(chunks)):
            next_chunk = chunks[i]
            if len(output) < fade_len or len(next_chunk) < fade_len:
                output = np.concatenate([output, next_chunk])
                continue
            
            # 提取前一段的结尾和后一段的开头进行淡入淡出
            fade_out = output[-fade_len:] * np.linspace(1.0, 0.0, fade_len)
            fade_in = next_chunk[:fade_len] * np.linspace(0.0, 1.0, fade_len)
            
            # 合并淡化部分
            overlap = fade_out + fade_in
            output = np.concatenate([output[:-fade_len], overlap, next_chunk[fade_len:]])
            
        return output

    def _get_model(self, version):
        """延迟加载模型"""
        if version not in self._models:
            if version == "1.7b_base":
                path = self.path_17b_base
            else:
                path = self.path_06b if version == "0.6b" else self.path_17b
                
            print(f"[Engine] 正在加载 {version} 模型到 CPU...")
            self._models[version] = Qwen3TTSModel.from_pretrained(
                path, 
                device_map=self.device, 
                dtype=torch.float32
            )
        return self._models[version]

    def _smart_split(self, text, max_chars=400):
        """
        利用 sentencex 进行高精度分句，并合并成逻辑区块。
        """
        from sentencex import segment
        
        # 1. 首先利用 sentencex 把文本切成最细粒度的句子
        # 它会自动保留所有标点符号
        raw_sentences = segment("zh", text)
        
        chunks = []
        current_chunk = ""
        
        for s in raw_sentences:
            # 如果单句就已经超过了 max_chars (极少见)，则强行按字数切
            if len(s) > max_chars:
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = ""
                # 强行切分超长单句
                for i in range(0, len(s), max_chars):
                    chunks.append(s[i:i+max_chars])
                continue
            
            # 尝试合并句子到当前区块
            if len(current_chunk) + len(s) <= max_chars:
                current_chunk += s
            else:
                # 区块满了，存入列表并开启新区块
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = s
                
        # 存入最后一个区块
        if current_chunk:
            chunks.append(current_chunk)
            
        return [c.strip() for c in chunks if c.strip()]

    def generate(self, text, mode="news", ref_audio=None, ref_text=None):
        """
        核心生成接口
        :param text: 输入文本
        :param mode: "news" (Vivian 1.7B), "story" (Serena 0.6B), "clone" (1.7B Base)
        :param ref_audio: 克隆模式下的参考音频路径
        :param ref_text: 参考音频对应的文本内容
        :return: (wav_data, sample_rate)
        """
        # 1. 应用读音纠错 (如 玊 -> 素)
        processed_text = self._apply_corrections(text)

        if mode == "news":
            speaker, version, instruct = "Vivian", "1.7b", "A professional, calm, and elegant female voice, speaking with a steady and sophisticated tone. Please speak slightly faster for a crisp and efficient delivery."
            model = self._get_model(version)
        elif mode == "story":
            speaker, version, instruct = "Serena", "0.6b", "Normal"
            model = self._get_model(version)
        elif mode == "clone":
            version = "1.7b_base"
            model = self._get_model(version)
            if not ref_audio or not ref_text:
                raise ValueError("克隆模式必须提供 ref_audio 和 ref_text")

        # 2. 智能切分文本
        chunks = self._smart_split(processed_text)
        
        audio_chunks = []
        sr = 24000
        print(f"[Engine] 正在以 {mode} 模式合成...")
        
        for i, chunk in enumerate(chunks):
            print(f"  -> 处理段落 {i+1}/{len(chunks)}...")
            if mode == "clone":
                wav_chunk, current_sr = model.generate_voice_clone(
                    text=chunk,
                    language="chinese",
                    ref_audio=ref_audio,
                    ref_text=ref_text
                )
            else:
                wav_chunk, current_sr = model.generate_custom_voice(
                    text=chunk,
                    language="chinese",
                    speaker=speaker,
                    instruct=instruct
                )
            sr = current_sr
            audio_chunks.append(wav_chunk[0])
            
        # 3. 使用 100ms 淡入淡出进行无缝拼接
        return self._crossfade_stitch(audio_chunks, sr, fade_ms=100), sr

# ================= 使用示例 (Example Use) =================
if __name__ == "__main__":
    engine = QwenTTSEngine()
    
    # 示例生成
    output_path = "engine_test_output.wav"
    text = "这是一段通过 QwenTTSEngine 封装类生成的测试文本。我们将确保它在 CPU 上运行极其稳定。"
    
    wav, sr = engine.generate(text, mode="news")
    sf.write(output_path, wav, sr)
    print(f"测试完成，文件已保存至: {output_path}")
