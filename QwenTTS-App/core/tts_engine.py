import os
import sys
import threading
import numpy as np
import mlx.core as mx

class TTSEngine:
    def __init__(self, model_path="models/Qwen3-TTS-1.7B-8bit", mlx_audio_path="../../mlx_audio"):
        # 计算绝对路径
        self.base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), mlx_audio_path))
        self.abs_model_path = os.path.join(self.base_dir, model_path)
        
        # 将 mlx_audio 加入路径以确保能导入内部组件
        if self.base_dir not in sys.path:
            sys.path.insert(0, self.base_dir)
            
        self.model = None
        self._lock = threading.Lock()

    def ensure_model_loaded(self):
        with self._lock:
            if self.model is None:
                print(f"[TTSEngine] 正在从绝对路径加载模型: {self.abs_model_path}...")
                
                # 延迟导入，防止路径没设好就报错
                from mlx_audio.utils import load_model
                
                try:
                    # 直接传绝对路径给 load_model
                    self.model = load_model(self.abs_model_path)
                except Exception as e:
                    print(f"[TTSEngine] 加载失败: {e}")
                    raise e
                print("[TTSEngine] 模型加载完成")

    def generate_stream(self, text, config):
        self.ensure_model_loaded()
        
        if config.get("seed") is not None:
            mx.random.seed(config["seed"])

        # 补丁：给文本末尾加一个标点，防止模型吞词
        text_to_generate = text.strip() + "。"

        generate_kwargs = {
            "voice": config.get("voice", "Serena"),
            "instruct": config.get("instruct", "Professional female anchor, steady and clear."),
            "temperature": config.get("temperature", 0.2),
            "top_p": config.get("top_p", 0.5),
            "top_k": config.get("top_k", 10),
            "repetition_penalty": config.get("repetition_penalty", 1.0),
            "lang_code": config.get("lang_code", "zh"),
            "stream": True,
            "streaming_interval": 2.0,
            "response_format": "pcm"
        }

        for result in self.model.generate(text_to_generate, **generate_kwargs):
            audio_data = result.audio
            if audio_data.dtype != mx.int16:
                audio_data = (audio_data * 32767).astype(mx.int16)
            yield np.array(audio_data).tobytes()
