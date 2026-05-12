import os
os.environ["TQDM_DISABLE"] = "1"

import rumps
import pyperclip
import sys
import subprocess
import threading
import time
import requests
import psutil

# App.py 负责极轻量的 UI
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from core.storage import Storage

class QwenTTSApp(rumps.App):
    def __init__(self):
        # 使用浏览器插件的图标
        icon_path = os.path.join(BASE_DIR, "../qwen-tts-extension/public/icon/32.png")
        super().__init__(name="", icon=icon_path, quit_button=None)
        
        self.storage = Storage(data_dir=os.path.join(BASE_DIR, "data"))
        self.config = self.storage.load_config()
        
        # 后端配置
        self.backend_url = "http://127.0.0.1:8001"
        self.backend_process = None
        self.python_exe = sys.executable

        # 启动持久化后端
        self.start_backend_service()

        # 构造 UI
        self.item_read = rumps.MenuItem("▶ 朗读新剪贴板", callback=self.on_read_clipboard)
        self.item_stop = rumps.MenuItem("⏹ 停止播放", callback=self.on_stop_click)
        self.item_resume = rumps.MenuItem("⏯ 继续上次朗读", callback=self.on_resume_click)
        self.item_next = rumps.MenuItem("⏭ 下一段", callback=self.on_next_click)
        
        self.menu_speed = rumps.MenuItem("语速")
        self.speed_items = {}
        for s in ["0.8x", "1.0x", "1.2x", "1.5x"]:
            val = float(s.replace('x', ''))
            item = rumps.MenuItem(s, callback=self.on_speed_change)
            if val == self.config.get("speed", 1.0): item.state = 1
            self.menu_speed.add(item)
            self.speed_items[val] = item

        self.menu_voice = rumps.MenuItem("音色")
        self.voice_items = {}
        for v in ["Serena", "Ryan", "Vivian"]:
            item = rumps.MenuItem(v, callback=self.on_voice_change)
            if v == self.config["voice"]: item.state = 1
            self.menu_voice.add(item)
            self.voice_items[v] = item

        self.menu_model = rumps.MenuItem("模型尺寸")
        self.model_items = {}
        models = [
            ("1.7B (高质量)", "Qwen3-TTS-1.7B-8bit"),
            ("0.6B (极冷速)", "Qwen3-TTS-0.6B")
        ]
        for label, val in models:
            item = rumps.MenuItem(label, callback=self.on_model_change)
            if val == self.config.get("model", "Qwen3-TTS-1.7B-8bit"): item.state = 1
            self.menu_model.add(item)
            self.model_items[val] = item

        self.menu = [
            self.item_read,
            self.item_stop,
            rumps.separator,
            self.item_resume,
            self.item_next,
            rumps.separator,
            self.menu_model,
            self.menu_speed,
            self.menu_voice,
            rumps.separator,
            rumps.MenuItem("当前设置", callback=self.on_show_info),
            rumps.MenuItem("退出", callback=self.on_quit)
        ]

    def start_backend_service(self):
        print("[App] 正在清理旧的后端进程...")
        try:
            # 暴力清理残留，确保端口不被占用
            subprocess.run(["pkill", "-f", "backend.py"], stderr=subprocess.DEVNULL)
            time.sleep(0.5)
        except: pass

        print("[App] 正在启动后台引擎进程...")
        backend_script = os.path.join(BASE_DIR, "core", "backend.py")
        self.backend_process = subprocess.Popen(
            [self.python_exe, backend_script],
            stdout=sys.stdout,
            stderr=sys.stderr
        )

    @rumps.timer(1)
    def monitor_backend(self, _):
        # 检查后端进程是否存活，如果挂了则尝试重启
        if self.backend_process and self.backend_process.poll() is not None:
            print("[App] 警告: 后端进程异常退出，正在尝试自动重启...")
            self.start_backend_service()
            return

        try:
            response = requests.get(f"{self.backend_url}/status", timeout=0.5)
            if response.status_code == 200:
                data = response.json()
                # 核心修复：更激进的按钮状态逻辑
                # 只要后台报正在播放，或者缓冲区里还有存货，就允许停止
                is_busy = data.get("is_playing", False) or float(data.get("buffer_sec", 0)) > 0.1
                
                self.item_stop.set_callback(self.on_stop_click if is_busy else None)
                self.item_resume.set_callback(None if is_busy else self.on_resume_click)
                
                if data["title"]:
                    total = data.get("total_chunks", 0)
                    curr = data.get("current_index", 0)
                    self.item_resume.title = f"⏯ 继续: {data['title']} ({curr}/{total})"
                else:
                    self.item_resume.title = "⏯ 继续上次朗读"
        except:
            pass

    def on_read_clipboard(self, _):
        """强制开始新的朗读"""
        try:
            raw_text = pyperclip.paste()
            text = raw_text.strip() if raw_text else ""
            if not text:
                rumps.notification("Qwen TTS", "警告", "剪贴板为空")
                return
            requests.post(f"{self.backend_url}/read", json={"text": text, "index": 0}, timeout=1)
        except Exception as e:
            print(f"[App] 通信错误: {e}")

    def on_stop_click(self, _):
        """停止播放"""
        try:
            requests.post(f"{self.backend_url}/stop")
        except:
            pass

    def on_resume_click(self, _):
        """恢复上次朗读"""
        try:
            requests.post(f"{self.backend_url}/read", json={"text": "RESUME_MODE", "index": -1}, timeout=1)
        except:
            pass

    def on_next_click(self, _):
        """下一段"""
        try:
            res = requests.get(f"{self.backend_url}/status").json()
            idx = int(res["current_index"])
            requests.post(f"{self.backend_url}/read", json={"text": "RESUME_MODE", "index": idx}, timeout=1)
        except:
            pass

    def on_speed_change(self, sender):
        val = float(sender.title.replace('x', ''))
        for item in self.speed_items.values(): item.state = 0
        sender.state = 1
        self.config["speed"] = val
        self.storage.save_config(self.config)
        # 如果正在播放，不打断，仅保存设置；
        # 如果需要即时生效，可以取消下面的注释
        # requests.post(f"{self.backend_url}/read", json={"text": "RESUME_MODE", "index": -1})

    def on_voice_change(self, sender):
        name = sender.title
        for item in self.voice_items.values(): item.state = 0
        sender.state = 1
        self.config["voice"] = name
        self.storage.save_config(self.config)
        # 音色改变通常需要重启当前段落
        requests.post(f"{self.backend_url}/read", json={"text": "RESUME_MODE", "index": -1})

    def on_model_change(self, sender):
        target_val = "Qwen3-TTS-1.7B-8bit"
        if "0.6B" in sender.title:
            target_val = "Qwen3-TTS-0.6B"
        
        for val, item in self.model_items.items():
            item.state = 1 if val == target_val else 0
            
        self.config["model"] = target_val
        self.storage.save_config(self.config)
        
        try:
            rumps.notification("Qwen TTS", "切换模型", f"正在切换至 {sender.title}，下次播放生效")
        except:
            pass
            
        requests.post(f"{self.backend_url}/stop")

    def on_show_info(self, _):
        process = psutil.Process(os.getpid())
        mem = process.memory_info().rss / 1024 / 1024
        model_name = self.config.get("model", "1.7B")
        msg = f"模型: {model_name}\n音色: {self.config['voice']}\n语速: {self.config['speed']}x\nUI内存: {mem:.1f} MB"
        rumps.alert("Qwen TTS 状态", msg)

    def on_quit(self, _):
        if self.backend_process:
            self.backend_process.terminate()
        rumps.quit_application()

if __name__ == "__main__":
    QwenTTSApp().run()
