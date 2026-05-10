# MLX-Audio & Qwen3-TTS 量化版安装说明

本项目已成功配置 **MLX-Audio** 框架及 **Qwen3-TTS 1.7B 8-bit 量化模型**，专为 Mac (Apple Silicon) 优化，提供高性能的语音合成服务。

## 1. 安装路径
- **代码仓库**: `/Users/funanhe/00_MyCode/TTS/mlx_audio`
- **模型权重**: `/Users/funanhe/00_MyCode/TTS/mlx_audio/models/Qwen3-TTS-1.7B-8bit`
- **虚拟环境**: 使用当前 Python 环境，已安装 `mlx`, `mlx-audio`, `transformers 5.x`, `fastapi` 等核心依赖。

## 2. 核心功能
- **高性能语音合成 (TTS)**: 针对 Mac 优化，实测生成速度约为 **2.6x**（生成 10 秒音频仅需约 3.8 秒）。
- **低内存占用**: 使用 8-bit 量化模型，权重仅 **2.4GB**，相比原版 bf16 (3.6GB) 显著降低了显存压力。
- **多声音支持**: 支持 `Serena` (温柔女声)、`Vivian` (清亮女声)、`Ryan` (动感男声) 等多种预设音色。
- **可视化界面**: 提供内置的 Web UI (Studio)，支持直接在浏览器中进行文本转语音。

## 3. 使用方法

### 方法 A：启动可视化界面 (推荐)
在 `mlx_audio` 目录下运行以下命令：
```bash
cd /Users/funanhe/00_MyCode/TTS/mlx_audio
PYTHONPATH="." MLX_AUDIO_REALTIME_MODEL="models/Qwen3-TTS-1.7B-8bit" python -m mlx_audio.server --start-ui --host 127.0.0.1 --port 8000
```
启动后访问: [http://127.0.0.1:8000](http://127.0.0.1:8000)

### 方法 B：命令行快速生成
```bash
cd /Users/funanhe/00_MyCode/TTS/mlx_audio
PYTHONPATH="." python -m mlx_audio.tts.generate \
  --model models/Qwen3-TTS-1.7B-8bit \
  --text "你好，这是一段测试文字。" \
  --voice Serena \
  --output test.wav
```

### 方法 C：Python 代码调用
```python
import sys
sys.path.append("/Users/funanhe/00_MyCode/TTS/mlx_audio")
from mlx_audio.tts.utils import load_model

model = load_model("/Users/funanhe/00_MyCode/TTS/mlx_audio/models/Qwen3-TTS-1.7B-8bit")
for result in model.generate("文本内容", voice="Serena"):
    # result.audio 为生成的音频数据 (numpy array)
    pass
```

## 4. 维护说明
- **代码修正**: 为了兼容最新的 `transformers 5.x`，已手动修正了 `qwen_tts` 核心库中的装饰器兼容性问题。
- **依赖管理**: 已安装 `webrtcvad` 以支持流式服务器功能。

---
*创建日期: 2026-05-07*
