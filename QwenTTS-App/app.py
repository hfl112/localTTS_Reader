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
import sqlite3

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
        self.session = requests.Session()  # 复用 TCP 连接以减少本地 CPU 开销

        # 启动持久化后端
        self.start_backend_service()

        # 构造 UI
        self.item_read_clip = rumps.MenuItem(
            "📋 朗读剪贴板", callback=self.on_read_clipboard
        )
        self.item_play_pause = rumps.MenuItem(
            "⏸ 播放 / 暂停", callback=self.on_play_pause
        )
        self.item_prev = rumps.MenuItem("⏮ 上一段", callback=self.on_prev_click)
        self.item_next = rumps.MenuItem("⏭ 下一段", callback=self.on_next_click)
        self.item_stop = rumps.MenuItem("⏹ 停止播放", callback=self.on_stop_click)
        self.item_save = rumps.MenuItem(
            "💾 保存当前朗读", callback=self.on_save_current
        )
        self.item_podcast = rumps.MenuItem(
            "🎙️ 生成收藏播客", callback=self.on_generate_podcast
        )
        self.item_restart_audio = rumps.MenuItem("🎧 重启音频设备", callback=self.on_restart_audio)

        self.menu_speed = rumps.MenuItem("语速")
        self.speed_items = {}
        for s in ["0.8x", "1.0x", "1.2x", "1.5x"]:
            val = float(s.replace("x", ""))
            item = rumps.MenuItem(s, callback=self.on_speed_change)
            if val == self.config.get("speed", 1.0):
                item.state = 1
            self.menu_speed.add(item)
            self.speed_items[val] = item

        self.menu_voice = rumps.MenuItem("音色")
        self.voice_items = {}
        for v in ["Serena", "Ryan", "Vivian"]:
            item = rumps.MenuItem(v, callback=self.on_voice_change)
            if v == self.config["voice"]:
                item.state = 1
            self.menu_voice.add(item)
            self.voice_items[v] = item

        self.menu_model = rumps.MenuItem("模型尺寸")
        self.model_items = {}
        models = [
            ("1.7B (高质量)", "Qwen3-TTS-1.7B-8bit"),
            ("0.6B (极冷速)", "Qwen3-TTS-0.6B"),
        ]
        for label, val in models:
            item = rumps.MenuItem(label, callback=self.on_model_change)
            if val == self.config.get("model", "Qwen3-TTS-1.7B-8bit"):
                item.state = 1
            self.menu_model.add(item)
            self.model_items[val] = item

        self.menu_podcasts = rumps.MenuItem("🎙️ 最近播客")

        self.menu = [
            self.item_read_clip,
            rumps.separator,
            self.item_play_pause,
            self.item_prev,
            self.item_next,
            self.item_stop,
            rumps.separator,
            self.item_save,
            self.item_podcast,
            self.menu_podcasts,
            rumps.separator,
            self.menu_model,
            self.menu_speed,
            self.menu_voice,
            rumps.separator,
            self.item_restart_audio,
            rumps.separator,
            rumps.MenuItem("退出", callback=self.on_quit),
        ]

        # 启动时执行一次清空临时资产
        self.clean_assets()

    def start_backend_service(self):
        print("[App] 正在清理旧的后端进程...")
        
        # 1. 强杀所有包含 backend.py 命名的残留进程
        try:
            subprocess.run(["pkill", "-9", "-f", "backend.py"], stderr=subprocess.DEVNULL)
        except:
            pass
            
        # 2. 物理释放 8001 端口占用，斩草除根
        try:
            output = subprocess.check_output(["lsof", "-t", "-i:8001"], stderr=subprocess.DEVNULL)
            pids = output.decode("utf-8").strip().split("\n")
            for pid in pids:
                pid = pid.strip()
                if pid:
                    print(f"[App] 检测到 8001 端口残留僵尸进程 PID: {pid}，正在执行强杀...")
                    subprocess.run(["kill", "-9", pid], stderr=subprocess.DEVNULL)
        except:
            pass
            
        time.sleep(0.5)

        print("[App] 正在启动后台引擎进程...")
        backend_script = os.path.join(BASE_DIR, "core", "backend.py")
        self.backend_process = subprocess.Popen(
            [self.python_exe, backend_script], stdout=sys.stdout, stderr=sys.stderr
        )

    def _safe_post_async(self, endpoint: str, json_data: dict = None) -> None:
        def run():
            try:
                self.session.post(f"{self.backend_url}{endpoint}", json=json_data, timeout=5)
            except Exception as e:
                print(f"[App] 异步 POST 通信错误 {endpoint}: {e}")
        threading.Thread(target=run, daemon=True).start()

    @rumps.timer(1)
    def monitor_backend(self, _):
        if self.backend_process and self.backend_process.poll() is not None:
            print("[App] 警告: 后端进程异常退出，正在尝试自动重启...")
            self.start_backend_service()
            return

        # 在主线程中扫描播客文件变化并更新子菜单 (纯本地 IO，极轻量)
        self.scan_and_update_podcasts_menu()

        # 为防止 1Hz 同步 HTTP 阻碍 UI 主线程，使用后台线程和 Session 并缩减超时
        def check():
            try:
                response = self.session.get(f"{self.backend_url}/status", timeout=0.4)
                if response.status_code == 200:
                    data = response.json()
                    is_playing = data.get("is_playing", False)
                    self.item_play_pause.title = "⏸ 暂停" if is_playing else "▶ 继续"
            except:
                pass
        threading.Thread(target=check, daemon=True).start()

    def scan_and_update_podcasts_menu(self) -> None:
        """扫描本地导出的播客音频并在发生变化时动态更新菜单栏"""
        podcasts_dir = os.path.join(BASE_DIR, "..", "podcasts")
        exported_dir = os.path.join(BASE_DIR, "data", "exported")
        
        wav_files = []
        for dir_path in [podcasts_dir, exported_dir]:
            if os.path.exists(dir_path):
                for f in os.listdir(dir_path):
                    if f.endswith(".wav"):
                        full_path = os.path.join(dir_path, f)
                        try:
                            mtime = os.path.getmtime(full_path)
                            wav_files.append((f, mtime))
                        except:
                            pass
        
        # 根据最后修改时间降序排序，最新的排前面
        wav_files.sort(key=lambda x: x[1], reverse=True)
        recent_files = wav_files[:10]
        
        # 计算特征 Hash 避免重复渲染导致闪烁
        current_hash = "|".join([f"{x[0]}_{x[1]}" for x in recent_files])
        if getattr(self, "last_podcasts_hash", None) == current_hash:
            return
            
        self.last_podcasts_hash = current_hash
        
        # 动态更新菜单项
        if hasattr(self.menu_podcasts, "_menu") and self.menu_podcasts._menu is not None:
            try:
                self.menu_podcasts.clear()
            except Exception:
                pass
        if not recent_files:
            self.menu_podcasts.add(rumps.MenuItem("暂无生成的播客"))
            return
            
        for f, mtime in recent_files:
            local_time = time.localtime(mtime)
            time_str = time.strftime("%m-%d %H:%M", local_time)
            
            if f.startswith("export_"):
                text_preview = ""
                try:
                    parts = f.replace(".wav", "").split("_")
                    if len(parts) >= 2:
                        md5_val = parts[1]
                        cache_item = self.storage.get_cache_by_md5(md5_val)
                        if cache_item and cache_item.get("text"):
                            raw_text = cache_item["text"].strip().replace("\n", " ")
                            if len(raw_text) > 12:
                                text_preview = ": " + raw_text[:10] + "..."
                            else:
                                text_preview = ": " + raw_text
                except Exception as e:
                    print(f"[App] 获取播客文本前缀失败: {e}")
                display_title = f"🎙️ 导出 {time_str}{text_preview}"
            else:
                display_title = f"📻 播客 {time_str}"
                
            def make_callback(filename: str):
                def play_cb(_):
                    try:
                        self.session.post(
                            f"{self.backend_url}/podcasts/play", 
                            json={"filename": filename}, 
                            timeout=3
                        )
                    except Exception as play_err:
                        print(f"[App] 播客菜单播放错误: {play_err}")
                return play_cb
                
            item = rumps.MenuItem(display_title, callback=make_callback(f))
            self.menu_podcasts.add(item)

    def on_read_clipboard(self, _):
        """朗读剪贴板"""
        try:
            raw_text = pyperclip.paste()
            text = raw_text.strip() if raw_text else ""
            if not text:
                rumps.notification("Qwen TTS", "警告", "剪贴板为空")
                return
            self._safe_post_async("/read", {"text": text, "index": 0})
        except Exception as e:
            print(f"[App] 读取剪贴板错误: {e}")

    def on_play_pause(self, _):
        def run():
            try:
                res = self.session.get(f"{self.backend_url}/status", timeout=2).json()
                is_playing = res.get("is_playing", False)
                endpoint = "/pause" if is_playing else "/resume"
                self.session.post(f"{self.backend_url}{endpoint}", timeout=2)
            except Exception as e:
                print(f"[App] 播放暂停切换错误: {e}")
        threading.Thread(target=run, daemon=True).start()

    def on_stop_click(self, _):
        """停止播放"""
        self._safe_post_async("/stop")

    def on_prev_click(self, _):
        self._safe_post_async("/seek", {"direction": -1})

    def on_next_click(self, _):
        self._safe_post_async("/seek", {"direction": 1})

    def on_save_current(self, _):
        def run():
            try:
                res = self.session.post(f"{self.backend_url}/save_current", timeout=3).json()
                if res.get("error"):
                    rumps.notification("Qwen TTS", "保存失败", res["error"])
                else:
                    rumps.notification("Qwen TTS", "保存成功", "已保存当前朗读文章")
            except Exception as e:
                print(f"[App] 保存当前朗读错误: {e}")
        threading.Thread(target=run, daemon=True).start()

    def on_generate_podcast(self, _):
        def run():
            try:
                res = self.session.post(f"{self.backend_url}/generate_podcast", timeout=3).json()
                if res.get("error"):
                    rumps.notification("Qwen TTS", "生成失败", res["error"])
                else:
                    rumps.notification(
                        "Qwen TTS", "生成中", "播客开始生成，请查看根目录的 podcasts/ 目录"
                    )
            except Exception as e:
                print(f"[App] 生成播客错误: {e}")
        threading.Thread(target=run, daemon=True).start()

    def on_speed_change(self, sender):
        val = float(sender.title.replace("x", ""))
        for item in self.speed_items.values():
            item.state = 0
        sender.state = 1
        self.config["speed"] = val
        self.storage.save_config(self.config)

    def on_voice_change(self, sender):
        name = sender.title
        for item in self.voice_items.values():
            item.state = 0
        sender.state = 1
        self.config["voice"] = name
        self.storage.save_config(self.config)
        # 音色改变通常需要重启当前段落
        self._safe_post_async("/read", {"text": "RESUME_MODE", "index": -1})

    def on_model_change(self, sender):
        target_val = "Qwen3-TTS-1.7B-8bit"
        if "0.6B" in sender.title:
            target_val = "Qwen3-TTS-0.6B"

        for val, item in self.model_items.items():
            item.state = 1 if val == target_val else 0

        self.config["model"] = target_val
        self.storage.save_config(self.config)

        try:
            rumps.notification(
                "Qwen TTS", "切换模型", f"正在切换至 {sender.title}，下次播放生效"
            )
        except:
            pass

        self._safe_post_async("/stop")


    def clean_assets(self) -> None:
        """清理临时缓存和已导出的播客音频"""
        print("[App] 正在清理临时缓存和已导出的音频文件...")
        # 1. 清理 cache 目录中的 npy 文件
        cache_dir = os.path.join(BASE_DIR, "data", "cache")
        if os.path.exists(cache_dir):
            for f in os.listdir(cache_dir):
                if f.endswith(".npy"):
                    try:
                        os.remove(os.path.join(cache_dir, f))
                    except:
                        pass
        # 2. 清空 SQLite 中的 cache_metadata 数据库表
        try:
            db_path = os.path.join(BASE_DIR, "data", "cache.db")
            if os.path.exists(db_path):
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM cache_metadata")
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"[App] 清空 cache_metadata 失败: {e}")

        # 3. 清理 exported 目录中的 wav 文件
        export_dir = os.path.join(BASE_DIR, "data", "exported")
        if os.path.exists(export_dir):
            for f in os.listdir(export_dir):
                if f.endswith(".wav"):
                    try:
                        os.remove(os.path.join(export_dir, f))
                    except:
                        pass

        # 4. 限制 podcasts 目录下的 wav 文件仅保留最新的 3 个，其他的删掉
        try:
            podcasts_dir = os.path.join(BASE_DIR, "..", "podcasts")
            if os.path.exists(podcasts_dir):
                files = [
                    os.path.join(podcasts_dir, f)
                    for f in os.listdir(podcasts_dir)
                    if f.endswith(".wav")
                ]
                if len(files) > 3:
                    files.sort(key=os.path.getmtime)
                    for f in files[:-3]:
                        try:
                            os.remove(f)
                        except:
                            pass
        except Exception as e:
            print(f"[App] 限制 podcasts 失败: {e}")

    def on_quit(self, _):
        if self.backend_process:
            self.backend_process.terminate()
            self.backend_process.wait()
        try:
            self.clean_assets()
        except:
            pass
        rumps.quit_application()

    def on_restart_audio(self, _):
        try:
            self.session.post(f"{self.backend_url}/restart_audio", timeout=2)
            rumps.notification("QwenTTS", "音频设备已重启", "已尝试重新绑定默认输出设备")
        except Exception as e:
            rumps.notification("QwenTTS", "重启设备失败", str(e))

if __name__ == "__main__":
    QwenTTSApp().run()
