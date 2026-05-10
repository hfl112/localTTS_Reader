import subprocess
import os
import time

class PCMPlayer:
    def __init__(self, sample_rate=24000):
        self.sample_rate = sample_rate
        self.process = None
        self.ffplay_path = '/opt/homebrew/bin/ffplay'

    def start(self, speed=1.0):
        if self.process:
            self.stop()
            
        # 构建命令
        # -af "atempo=1.2" 用于控制倍速，注意 atempo 只支持 0.5-2.0
        cmd = [
            self.ffplay_path, 
            '-f', 's16le', 
            '-ar', str(self.sample_rate), 
            '-ch_layout', 'mono',
            '-nodisp', 
            '-autoexit',
            '-af', f'atempo={speed}',
            '-'
        ]
        
        try:
            self.process = subprocess.Popen(
                cmd, 
                stdin=subprocess.PIPE, 
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL
            )
            print(f"[PCMPlayer] ffplay 已启动 (PID: {self.process.pid}, Speed: {speed})")
        except Exception as e:
            print(f"[PCMPlayer] 启动 ffplay 失败: {e}")

    def play_chunk(self, pcm_bytes):
        if self.process and self.process.stdin:
            try:
                self.process.stdin.write(pcm_bytes)
                self.process.stdin.flush()
            except Exception as e:
                # 写入失败说明 ffplay 可能已经因为播完而自动退出了
                pass

    def stop(self, graceful=False):
        if self.process:
            try:
                if graceful and self.process.stdin:
                    self.process.stdin.close()
                else:
                    self.process.terminate()
                    self.process.wait(timeout=0.2)
            except:
                pass
            
            if not graceful:
                self.process = None
            else:
                pass

    def is_running(self):
        if self.process:
            return self.process.poll() is None
        return False
