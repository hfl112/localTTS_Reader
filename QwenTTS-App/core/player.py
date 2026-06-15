import sounddevice as sd
import numpy as np
import threading
import queue
import time
from typing import Optional, Any

class PCMPlayer:
    SENTINEL: str = "PIPELINE_END_STRICT_V1"

    def __init__(self, sample_rate: int = 24000) -> None:
        self.sample_rate: int = sample_rate
        self.audio_queue: queue.Queue = queue.Queue()
        self.is_active: bool = False  
        self.is_prebuffering: bool = True
        self.is_paused: bool = False  # New state for pausing
        self.min_chunks_to_start: int = 1
        
        self.stream: Optional[sd.OutputStream] = None
        self.leftover_data: Optional[np.ndarray] = None
        self.volume_scale: float = 1.0 
        self.playback_finished_event: threading.Event = threading.Event()
        self.playback_finished_event.set()
        self.current_device_name: Optional[str] = None

        # 锁保护多线程共享的播放器状态
        self._lock: threading.Lock = threading.Lock()

        self._ensure_stream_started()

        # 启动后台设备检测线程，动态支持耳机等音频输出设备的切换
        threading.Thread(target=self._device_monitor_loop, daemon=True).start()

    def _ensure_stream_started(self) -> None:
        need_reopen = False
        if self.stream is None or not self.stream.active:
            need_reopen = True
        else:
            try:
                default_device_info = sd.query_devices(kind='output')
                default_device_name = default_device_info.get('name')
                if self.current_device_name != default_device_name:
                    need_reopen = True
            except:
                pass

        if need_reopen:
            try:
                if self.stream:
                    try:
                        self.stream.stop()
                        self.stream.close()
                    except:
                        pass
                print(f"[PCMPlayer] 正在启动音频流 (采样率: {self.sample_rate}Hz)...")
                self.stream = sd.OutputStream(
                    samplerate=self.sample_rate,
                    channels=2,
                    dtype='float32',
                    callback=self._callback,
                    blocksize=8192 
                )
                self.stream.start()
                try:
                    device_info = sd.query_devices(self.stream.device, 'output')
                    self.current_device_name = device_info.get('name')
                    print(f"[PCMPlayer] 音频流启动成功，当前绑定设备: {self.current_device_name}")
                except Exception as e:
                    self.current_device_name = None
                    print(f"[PCMPlayer] 查询音频设备名称失败: {e}")
            except Exception as e:
                print(f"[PCMPlayer] 启动失败: {e}")

    def _device_monitor_loop(self) -> None:
        while True:
            time.sleep(1.5)
            # 只有在播放器处于 active 状态时才进行检测 and 切换，避免空闲时频繁查询
            if not self.is_active:
                continue
            try:
                default_device_info = sd.query_devices(kind='output')
                default_device_name = default_device_info.get('name')
                if self.current_device_name and default_device_name and self.current_device_name != default_device_name:
                    print(f"[PCMPlayer] 检测到 macOS 默认输出设备变更: {self.current_device_name} -> {default_device_name}，正在自动切换流...")
                    self._recreate_stream()
            except Exception as e:
                pass

    def _recreate_stream(self) -> None:
        with self._lock:
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception as e:
                    print(f"[PCMPlayer] 停止旧音频流失败: {e}")
            try:
                self.stream = sd.OutputStream(
                    samplerate=self.sample_rate,
                    channels=2,
                    dtype='float32',
                    callback=self._callback,
                    blocksize=8192
                )
                self.stream.start()
                device_info = sd.query_devices(self.stream.device, 'output')
                self.current_device_name = device_info.get('name')
                print(f"[PCMPlayer] 音频流切换成功，当前绑定设备: {self.current_device_name}")
            except Exception as e:
                print(f"[PCMPlayer] 音频流重建失败: {e}")

    def _callback(self, outdata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        data_to_fill = np.zeros((frames, 2), dtype=np.float32)
        
        if status:
            if 'output_underflow' not in str(status): # Ignore minor underflows
                print(f"[PCMPlayer] Stream status: {status}")
        
        with self._lock:
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
                        if isinstance(item, str) and item == self.SENTINEL:
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
            except Exception as e:
                print(f"[PCMPlayer] Callback Error: {e}")
                
            outdata[:] = data_to_fill * self.volume_scale

    def get_queue_duration(self) -> float:
        # 队列里有多少个 block，每个 block 假设为 0.5s 推理分片
        return self.audio_queue.qsize() * 0.5

    def start(self, speed: float = 1.0) -> None:
        self._ensure_stream_started()
        with self._lock:
            self.is_active = True
            self.is_paused = False
            self.is_prebuffering = True 
            self.leftover_data = None
            self.playback_finished_event.clear()
            while not self.audio_queue.empty():
                try:
                    self.audio_queue.get_nowait()
                except:
                    break

    def pause(self) -> None:
        with self._lock:
            self.is_paused = True

    def resume(self) -> None:
        with self._lock:
            self.is_paused = False

    def play_chunk(self, chunk: np.ndarray) -> None:
        self.audio_queue.put(chunk)

    def signal_end_of_article(self) -> None:
        self.audio_queue.put(self.SENTINEL)

    def wait_until_finished(self, timeout: float = 120.0) -> bool:
        return self.playback_finished_event.wait(timeout=timeout)

    def stop(self, graceful: bool = False) -> None:
        if graceful:
            self.wait_until_finished()
        with self._lock:
            self.is_active = False
            self.is_paused = False
            self.leftover_data = None
            while not self.audio_queue.empty():
                try:
                    self.audio_queue.get_nowait()
                except:
                    break

    def is_running(self) -> bool:
        return not self.playback_finished_event.is_set()
