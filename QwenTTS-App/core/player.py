import sounddevice as sd
import numpy as np
import threading
import queue
import time

class PCMPlayer:
    SENTINEL = object()

    def __init__(self, sample_rate=24000):
        self.sample_rate = sample_rate
        self.audio_queue = queue.Queue()
        self.is_active = False  
        self.is_prebuffering = True
        self.is_paused = False # New state for pausing
        self.min_chunks_to_start = 2
        
        self.stream = None
        self.leftover_data = None
        self.volume_scale = 1.0 
        self.playback_finished_event = threading.Event()
        self.playback_finished_event.set()

        self._ensure_stream_started()

    def _ensure_stream_started(self):
        if self.stream is None:
            try:
                # 核心：将 blocksize 提高到 8192，极大地降低回调频率
                # 340ms 触发一次，能有效对抗系统总线压力
                self.stream = sd.OutputStream(
                    samplerate=self.sample_rate,
                    channels=2,
                    dtype='float32',
                    callback=self._callback,
                    blocksize=8192 
                )
                self.stream.start()
            except Exception as e:
                print(f"[PCMPlayer] 启动失败: {e}")

    def _callback(self, outdata, frames, time_info, status):
        # 极简回调，排除一切干扰
        data_to_fill = np.zeros((frames, 2), dtype=np.float32)
        
        # If not active OR paused, output silence without consuming queue
        if not self.is_active or self.is_paused:
            outdata.fill(0)
            return

        if self.is_prebuffering:
            if self.audio_queue.qsize() >= self.min_chunks_to_start:
                self.is_prebuffering = False
            else:
                outdata.fill(0)
                return

        filled = 0
        try:
            # 1. 消费余料
            if self.leftover_data is not None:
                avail = len(self.leftover_data)
                needed = frames - filled
                if avail <= needed:
                    data_to_fill[filled:filled+avail] = self.leftover_data
                    filled += avail
                    self.leftover_data = None
                else:
                    data_to_fill[filled:filled+needed] = self.leftover_data[:needed]
                    self.leftover_data = self.leftover_data[needed:]
                    filled = frames

            # 2. 获取数据
            while filled < frames:
                try:
                    item = self.audio_queue.get_nowait()
                    if item is self.SENTINEL:
                        self.playback_finished_event.set()
                        continue
                    
                    samples = item
                    avail = len(samples)
                    needed = frames - filled
                    if avail <= needed:
                        data_to_fill[filled:filled+avail] = samples
                        filled += avail
                    else:
                        data_to_fill[filled:filled+needed] = samples[:needed]
                        self.leftover_data = samples[needed:]
                        filled = frames
                except queue.Empty:
                    break
        except:
            pass
            
        outdata[:] = data_to_fill * self.volume_scale

    def get_queue_duration(self):
        # 队列里有多少个 block，每个 block 假设为 0.5s 推理分片
        return self.audio_queue.qsize() * 0.5

    def start(self, speed=1.0):
        self.is_active = True
        self.is_paused = False
        self.is_prebuffering = True 
        self.leftover_data = None
        self.playback_finished_event.clear()
        while not self.audio_queue.empty():
            try: self.audio_queue.get_nowait()
            except: break

    def pause(self):
        self.is_paused = True

    def resume(self):
        self.is_paused = False

    def play_chunk(self, chunk):
        self.audio_queue.put(chunk)

    def signal_end_of_article(self):
        self.audio_queue.put(self.SENTINEL)

    def wait_until_finished(self, timeout=120.0):
        return self.playback_finished_event.wait(timeout=timeout)

    def stop(self, graceful=False):
        if graceful: self.wait_until_finished()
        self.is_active = False
        self.is_paused = False
        self.leftover_data = None
        while not self.audio_queue.empty():
            try: self.audio_queue.get_nowait()
            except: break

    def is_running(self):
        return not self.playback_finished_event.is_set()
