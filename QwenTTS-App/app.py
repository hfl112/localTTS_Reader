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
        # 使用浏览器插件的绿色 Logo 作为菜单栏图标
        icon_path = os.path.join(BASE_DIR, "../qwen-tts-extension/public/icon/32.png")
        super().__init__(name="", icon=icon_path, quit_button=None)
        
        self.storage = Storage(data_dir=os.path.join(BASE_DIR, "data"))
        self.config = self.storage.load_config()
        self.state = self.storage.load_state()
        
        # 后端配置
        self.backend_url = "http://127.0.0.1:8001"
        self.backend_process = None
        self.python_exe = sys.executable

        # 启动持久化后端
        self.start_backend_service()

        # 构造 UI
        self.item_play = rumps.MenuItem("▶ 朗读剪贴板", callback=self.on_play_click)
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

        self.menu = [
            self.item_play,
            self.item_resume,
            self.item_next,
            rumps.separator,
            self.menu_speed,
            self.menu_voice,
            rumps.separator,
            rumps.MenuItem("当前设置", callback=self.on_show_info),
            rumps.MenuItem("退出", callback=self.on_quit)
        ]

    def start_backend_service(self):
        print("[App] 正在启动后台引擎进程...")
        backend_script = os.path.join(BASE_DIR, "core", "backend.py")
        self.backend_process = subprocess.Popen(
            [self.python_exe, backend_script],
            stdout=sys.stdout,
            stderr=sys.stderr
        )

    @rumps.timer(1)
    def monitor_backend(self, _):
        try:
            response = requests.get(f"{self.backend_url}/status", timeout=0.5)
            if response.status_code == 200:
                data = response.json()
                is_playing = data["is_playing"]
                self.item_play.title = "⏹ 停止播放" if is_playing else "▶ 朗读剪贴板"
                
                if data["title"]:
                    total = data.get("total_chunks", 0)
                    curr = data.get("current_index", 0)
                    self.item_resume.title = f"⏯ 继续: {data['title']} ({curr}/{total})"
        except:
            pass

    def on_play_click(self, _):
        try:
            res = requests.get(f"{self.backend_url}/status", timeout=0.5).json()
            if res["is_playing"]:
                requests.post(f"{self.backend_url}/stop")
            else:
                raw_text = pyperclip.paste()
                text = raw_text.strip() if raw_text else ""
                if not text:
                    print("[App] 警告: 剪贴板为空")
                    return
                requests.post(f"{self.backend_url}/read", json={"text": text, "index": 0}, timeout=1)
        except Exception as e:
            print(f"[App] 通信错误: {e}")

    def on_resume_click(self, _):
        try:
            requests.post(f"{self.backend_url}/read", json={"text": "RESUME_MODE", "index": -1}, timeout=1)
        except:
            pass

    def on_next_click(self, _):
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
        requests.post(f"{self.backend_url}/read", json={"text": "RESUME_MODE", "index": -1})

    def on_voice_change(self, sender):
        name = sender.title
        for item in self.voice_items.values(): item.state = 0
        sender.state = 1
        self.config["voice"] = name
        self.storage.save_config(self.config)
        requests.post(f"{self.backend_url}/read", json={"text": "RESUME_MODE", "index": -1})

    def on_show_info(self, _):
        process = psutil.Process(os.getpid())
        mem = process.memory_info().rss / 1024 / 1024
        msg = f"音色: {self.config['voice']}\n语速: {self.config['speed']}x\nSeed: {self.config['seed']}\nUI内存: {mem:.1f} MB"
        rumps.alert("Qwen TTS 状态", msg)

    def on_quit(self, _):
        if self.backend_process:
            self.backend_process.terminate()
            subprocess.run(["pkill", "-9", "ffplay"], stderr=subprocess.DEVNULL)
        rumps.quit_application()

if __name__ == "__main__":
    QwenTTSApp().run()
