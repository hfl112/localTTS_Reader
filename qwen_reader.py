#!/Users/funanhe/miniconda3/envs/gemini/bin/python
import sys
import os
import subprocess
import time

# 1. 核心路径配置
BASE_DIR = "/Users/funanhe/00_MyCode/TTS/mlx_audio"
MODEL_PATH = "models/Qwen3-TTS-1.7B-8bit"
PYTHON_EXE = "/Users/funanhe/miniconda3/envs/gemini/bin/python"
FFPLAY_PATH = "/opt/homebrew/bin/ffplay"
LOG_FILE = "/Users/funanhe/00_MyCode/TTS/qwen_reader.log"

def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")

def main():
    try:
        # 清理旧的播放进程，防止音频设备冲突
        subprocess.run(["pkill", "-9", "ffplay"], stderr=subprocess.DEVNULL)
        
        # 获取选中的文本
        if len(sys.argv) < 2:
            log("错误: 没有接收到输入参数")
            return
        
        text = sys.argv[1].strip()
        if not text:
            log("错误: 输入文本为空")
            return

        # 给文本末尾加个句号作为缓冲，防止吞词
        padded_text = text + "。"

        log(f"开始朗读文本(长度:{len(text)}): {text[:20]}...")

        # 2. 构建命令
        # 我们使用全路径，并锁定语言和音色
        cmd = [
            PYTHON_EXE, "-m", "mlx_audio.tts.generate",
            "--model", MODEL_PATH,
            "--text", padded_text,
            "--voice", "Serena",
            "--lang_code", "zh",
            "--instruct", "保持稳重、客观且专业的成年女性播音员声音，语调平稳，不带多余情感。",
            "--temperature", "0.5",
            "--repetition_penalty", "1.1",
            "--stream",
            "--play"
        ]

        # 3. 设置环境变量
        env = os.environ.copy()
        env["PYTHONPATH"] = BASE_DIR
        # 确保 ffplay 在 PATH 中
        env["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:{env.get('PATH', '')}"
        
        # 4. 执行生成
        # 使用 subprocess.Popen 并在后台处理，如果需要可以改为 run
        process = subprocess.run(
            cmd, 
            cwd=BASE_DIR, 
            env=env, 
            capture_output=True, 
            text=True
        )
        
        if process.returncode != 0:
            log(f"执行失败(代码:{process.returncode}): {process.stderr}")
        else:
            log("朗读任务圆满完成")

    except Exception as e:
        log(f"系统级错误: {str(e)}")

if __name__ == "__main__":
    main()
