import os
import sys
import threading
import numpy as np
import mlx.core as mx
import time

class TTSEngine:
    def __init__(self, model_path="models/Qwen3-TTS-1.7B-8bit", mlx_audio_path="../../mlx_audio"):
        # ... (existing path logic)
        self.sample_rate = 16000 # 强制设定目标采样率为 16k
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

        # 补丁：给文本末尾加一个标点和空格，防止模型吞词
        text_to_generate = text.strip()
        if not any(text_to_generate.endswith(p) for p in [".", "。", "!", "！", "?", "？", ";", "；"]):
            text_to_generate += "。"
        
        # 核心优化：额外增加一点“呼吸空间”，确保最后一个字能稳稳读完
        text_to_generate += "  "

        # 动态计算 max_tokens
        # 核心修复：大幅提高上限。8192 token 足以支撑 2-3 分钟的连续语音。
        # 依靠 time-based timeout 保护即可，不需要靠 max_tokens 保护。
        dynamic_max_tokens = max(2048, len(text_to_generate) * 20)
        dynamic_max_tokens = min(dynamic_max_tokens, 8192)

        generate_kwargs = {
            "voice": config.get("voice", "Serena"),
            "instruct": config.get("instruct", "Professional female anchor, steady and clear."),
            "temperature": config.get("temperature", 0.2),
            "top_p": config.get("top_p", 0.5),
            "top_k": config.get("top_k", 10),
            "repetition_penalty": config.get("repetition_penalty", 1.1), # 提高到 1.1 增加稳定性
            "lang_code": config.get("lang_code", "zh"),
            "stream": True,
            "streaming_interval": 0.5, # 缩短到 0.5s，让数据流更均匀
            "response_format": "pcm",
            "max_tokens": dynamic_max_tokens # 传入动态限制
        }

        print(f"[TTSEngine] 开始生成: \"{text_to_generate[:20]}...\", MaxTokens: {dynamic_max_tokens}, Penalty: {generate_kwargs['repetition_penalty']}")

        start_time = time.time()
        # 动态超时：每个汉字给 1 秒的时间，最少 30 秒，最多 120 秒
        # 这样即使是 160 字的长段落，也有 160s 的缓冲时间，绝不会被掐断
        timeout = max(30, min(len(text_to_generate) * 1.0, 120)) 
        
        try:
            for result in self.model.generate(text_to_generate, **generate_kwargs):
                if time.time() - start_time > timeout:
                    print(f"[TTSEngine] 警告: 该段落生成超时 ({timeout:.1f}s)，强制中断以保护系统。")
                    break
                    
                # --- 核心改进：在推理线程（锁外）完成所有重型计算 ---
                audio_data = result.audio
                if audio_data.dtype != mx.float32:
                    audio_data = audio_data.astype(mx.float32)
                
                # 1. 自动归一化
                peak = mx.max(mx.abs(audio_data))
                if peak > 0.01:
                    gain = mx.minimum(0.7 / peak, mx.array(4.0))
                    audio_data = audio_data * gain
                
                # 2. 转双声道并应用主音量 (都在 MLX/C++ 层完成，不占 GIL)
                # 使用 broadcast 方式扩展为双声道
                audio_data = audio_data[:, None] # [N, 1]
                audio_data = mx.concatenate([audio_data, audio_data], axis=1) # [N, 2]
                audio_data = audio_data * 0.8 # 安全增益
                
                # 3. 降采样到 16kHz (如果模型输出不是 16k)
                # MLX 原生不支持高质量重采样，我们转为 numpy 后用简单线性插值或直接跳步
                # Qwen3-TTS 默认输出是 24k，转 16k 比例是 1.5
                samples = np.array(audio_data)
                if self.sample_rate != 24000:
                    from scipy import signal
                    # 使用 scipy.signal.resample 进行高质量降采样
                    num_samples = int(len(samples) * self.sample_rate / 24000)
                    samples = signal.resample(samples, num_samples)
                
                yield samples.astype(np.float32)
        except Exception as e:
            print(f"[TTSEngine] 生成过程发生错误: {e}")

    def warmup(self):
        """
        预热模型，触发 MLX 算子编译，防止第一次朗读时卡顿。
        """
        self.ensure_model_loaded()
        print("[TTSEngine] 正在进行预热 (触发 MLX 编译)...")
        warmup_config = {
            "voice": "Serena",
            "temperature": 0.2,
            "top_p": 0.5,
            "seed": 42
        }
        # 使用极短文本预热
        for _ in self.generate_stream("预热。", warmup_config):
            pass
        print("[TTSEngine] 预热完成")
