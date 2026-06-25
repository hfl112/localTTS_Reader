import os
import sys
import threading
import numpy as np
import mlx.core as mx
import time

class TTSEngine:
    def __init__(self, model_path="models/Qwen3-TTS-0.6B", mlx_audio_path=None):
        self.sample_rate = 24000 # 改回 24k 原生采样率
        
        # 1. 动态确定 mlx_audio 的绝对路径
        resolved_mlx_audio_path = mlx_audio_path
        
        # 优先从环境变量获取
        if not resolved_mlx_audio_path:
            resolved_mlx_audio_path = os.environ.get("MLX_AUDIO_PATH")
            
        if not resolved_mlx_audio_path:
            workspace_env = os.environ.get("TTS_WORKSPACE_PATH")
            if workspace_env:
                resolved_mlx_audio_path = os.path.join(workspace_env, "mlx_audio")
                
        # 默认回退到原有的相对路径
        if not resolved_mlx_audio_path:
            resolved_mlx_audio_path = "../../mlx_audio"
            
        # 如果是相对路径，以当前文件位置为基准计算绝对路径；如果是绝对路径，直接使用
        if os.path.isabs(resolved_mlx_audio_path):
            self.base_dir = os.path.abspath(resolved_mlx_audio_path)
        else:
            self.base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), resolved_mlx_audio_path))
            
        # 2. 动态确定 model_path 的绝对路径
        if os.path.isabs(model_path):
            self.abs_model_path = os.path.abspath(model_path)
        else:
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
                return True
            return False

    def generate_stream(self, text, config):
        if self.ensure_model_loaded():
            self.warmup()
        
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

        # Auto-detect language per chunk to prevent JIT collapse / "sisisi" garble
        has_chinese = any('\u4e00' <= char <= '\u9fff' for char in text_to_generate)
        current_lang_code = config.get("lang_code", "zh")
        if current_lang_code == "zh" and not has_chinese:
            print(f"[TTSEngine] Auto-detect: No Chinese chars, overriding lang_code to 'en'")
            current_lang_code = "en"
        elif current_lang_code == "en" and has_chinese:
            print(f"[TTSEngine] Auto-detect: Chinese chars detected, overriding lang_code to 'zh'")
            current_lang_code = "zh"

        generate_kwargs = {
            "voice": config.get("voice", "Serena"),
            "instruct": config.get("instruct", "Professional female anchor, steady and clear."),
            "temperature": config.get("temperature", 0.2),
            "top_p": config.get("top_p", 0.5),
            "top_k": config.get("top_k", 10),
            "repetition_penalty": config.get("repetition_penalty", 1.1),
            "lang_code": current_lang_code,
            "stream": True,
            "streaming_interval": 0.5,
            "response_format": "pcm",
            "max_tokens": dynamic_max_tokens
        }

        # Inject ICL parameters if provided
        if "ref_audio" in config:
            generate_kwargs["ref_audio"] = config["ref_audio"]
        if "ref_text" in config:
            generate_kwargs["ref_text"] = config["ref_text"]

        # Global ICL auto-injection mechanism to prevent voice drift in zero-shot mode
        if "ref_audio" not in generate_kwargs:
            project_root = os.path.dirname(self.base_dir)
            base_ref_path = os.path.join(project_root, "reference")
            
            serena_zh_audio = f"{base_ref_path}/ref_serena_zh.wav"
            serena_zh_text = "欢迎收听本期播客，我是女主持塞蕾娜。"
            
            serena_en_audio = f"{base_ref_path}/bbc_news.wav"
            serena_en_text = "This is the research headquarters for one of the oldest companies in tech, IBM."
            
            ryan_ref_audio = f"{base_ref_path}/ref_ryan.wav"
            ryan_ref_text = "各位听众大家好，欢迎收听本期的新闻快报，我是男主持瑞恩。"
            
            current_voice = generate_kwargs.get("voice", "Serena")
            current_lang = generate_kwargs.get("lang_code", "zh")
            
            if current_voice == "Serena":
                if current_lang == "zh" and os.path.exists(serena_zh_audio):
                    generate_kwargs["ref_audio"] = serena_zh_audio
                    generate_kwargs["ref_text"] = serena_zh_text
                    print(f"[TTSEngine] 全局 ICL 保护：自动为 Serena 注入中文锁音")
                elif current_lang == "en" and os.path.exists(serena_en_audio):
                    generate_kwargs["ref_audio"] = serena_en_audio
                    generate_kwargs["ref_text"] = serena_en_text
                    print(f"[TTSEngine] 全局 ICL 保护：自动为 Serena 注入英文锁音")
            elif current_voice == "Ryan":
                if current_lang == "zh" and os.path.exists(ryan_ref_audio):
                    generate_kwargs["ref_audio"] = ryan_ref_audio
                    generate_kwargs["ref_text"] = ryan_ref_text
                    print(f"[TTSEngine] 全局 ICL 保护：自动为 Ryan 注入中文锁音")

        # 跨语言 ICL 安全防护与自动语种重定向：
        # 防止自回归无限循环崩溃。如检测到提示词参考文本与当前要合成的语种不一致，
        # 则自动重定向至对应的同语种 ICL 提示词；若无同语种提示词，退避至内置零样本模式。
        ref_text = generate_kwargs.get("ref_text", "")
        current_lang = generate_kwargs.get("lang_code", "zh")
        current_voice = generate_kwargs.get("voice", "Serena")
        project_root = os.path.dirname(self.base_dir)
        base_ref_path = os.path.join(project_root, "reference")
        
        if ref_text and current_lang:
            has_chinese_ref = any('\u4e00' <= char <= '\u9fff' for char in ref_text)
            ref_lang = "zh" if has_chinese_ref else "en"
            
            if ref_lang != current_lang:
                print(f"[TTSEngine] 检测到跨语言 ICL 潜在冲突 ({ref_lang} 提示词 -> {current_lang} 生成)。尝试自动语种重定向...")
                redirected = False
                
                if current_voice == "Serena":
                    serena_zh_audio = f"{base_ref_path}/ref_serena_zh.wav"
                    serena_zh_text = "欢迎收听本期播客，我是女主持塞蕾娜。"
                    serena_en_audio = f"{base_ref_path}/bbc_news.wav"
                    serena_en_text = "This is the research headquarters for one of the oldest companies in tech, IBM."
                    
                    if current_lang == "zh" and os.path.exists(serena_zh_audio):
                        generate_kwargs["ref_audio"] = serena_zh_audio
                        generate_kwargs["ref_text"] = serena_zh_text
                        redirected = True
                    elif current_lang == "en" and os.path.exists(serena_en_audio):
                        generate_kwargs["ref_audio"] = serena_en_audio
                        generate_kwargs["ref_text"] = serena_en_text
                        redirected = True
                        
                elif current_voice == "Ryan":
                    ryan_ref_audio = f"{base_ref_path}/ref_ryan.wav"
                    ryan_ref_text = "各位听众大家好，欢迎收听本期的新闻快报，我是男主持瑞恩。"
                    if current_lang == "zh" and os.path.exists(ryan_ref_audio):
                        generate_kwargs["ref_audio"] = ryan_ref_audio
                        generate_kwargs["ref_text"] = ryan_ref_text
                        redirected = True
                
                if redirected:
                    print(f"[TTSEngine] 成功重定向至 {current_lang} 同语种锁音提示词，实现单语言稳定 ICL 生成。")
                else:
                    print(f"[TTSEngine] 无法重定向，自动退避为内置零样本模式以防止自回归崩溃。")
                    generate_kwargs.pop("ref_audio", None)
                    generate_kwargs.pop("ref_text", None)

        print(f"[TTSEngine] 开始生成: \"{text_to_generate[:20]}...\", MaxTokens: {dynamic_max_tokens}, Penalty: {generate_kwargs['repetition_penalty']}")

        start_time = time.time()
        # 动态超时：如果是后台预热编译阶段，给足 MLX JIT 算子编译所需时间 (180s)；
        # 日常朗读给每个汉字 1 秒的时间，最少 30 秒，最多 120 秒，绝不掐断
        if config.get("is_warmup", False):
            timeout = 180.0
        else:
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
                
                # 1. 转双声道 (broadcast 方式，MLX/C++ 层完成，不占 GIL)
                audio_data = audio_data[:, None] # [N, 1]
                audio_data = mx.concatenate([audio_data, audio_data], axis=1) # [N, 2]
                
                # 直接转换为 NumPy 数组进行稳健归一化
                samples = np.array(audio_data)
                
                # 2. 稳健增益归一化：采用 99.5% 分位数，过滤掉单点瞬态脉冲 (Clicks) 干扰，释放真实的最大人声音量
                abs_samples = np.abs(samples)
                if abs_samples.size > 0:
                    robust_peak = np.percentile(abs_samples, 99.5)
                    if robust_peak > 0.002:
                        gain = 0.85 / robust_peak
                        gain = min(gain, 6.0)  # 最大允许放大 6 倍，避免长音频听感疲劳和削波
                        samples = samples * gain
                
                # 3. 物理绝对峰值安全限制，杜绝数字溢出
                samples = np.clip(samples, -0.98, 0.98)
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
            "seed": 42,
            "is_warmup": True
        }
        # 使用极短文本预热
        for _ in self.generate_stream("预热。", warmup_config):
            pass
        print("[TTSEngine] 预热完成")
