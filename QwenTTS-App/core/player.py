import sounddevice as sd
import numpy as np
import threading
import queue
import time
import ctypes
from ctypes import c_uint32, byref, Structure
from typing import Optional, Any

# CoreAudio ctypes helpers for macOS default device query
class AudioObjectPropertyAddress(Structure):
    _fields_ = [
        ('mSelector', c_uint32),
        ('mScope', c_uint32),
        ('mElement', c_uint32),
    ]

try:
    _coreaudio = ctypes.CDLL('/System/Library/Frameworks/CoreAudio.framework/CoreAudio')
except Exception as e:
    _coreaudio = None
    print(f"[PCMPlayer] 载入 CoreAudio 失败: {e}")

def get_default_output_device_id() -> int:
    if not _coreaudio:
        return 0
    try:
        # kAudioHardwarePropertyDefaultOutputDevice = 'dOut' = 1684370979
        # kAudioObjectSystemObject = 1
        # kAudioObjectPropertyScopeGlobal = 'glob' = 1735159650
        # kAudioObjectPropertyElementMaster = 0
        address = AudioObjectPropertyAddress(
            mSelector=1684370979,
            mScope=1735159650,
            mElement=0
        )
        device_id = c_uint32(0)
        data_size = c_uint32(ctypes.sizeof(device_id))
        status = _coreaudio.AudioObjectGetPropertyData(
            1, # kAudioObjectSystemObject
            byref(address),
            0,
            None,
            byref(data_size),
            byref(device_id)
        )
        if status == 0:
            return device_id.value
    except Exception as e:
        print(f"[PCMPlayer] 获取默认输出设备 ID 失败: {e}")
    return 0

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
        self.current_device_id: int = 0

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
                default_device_id = get_default_output_device_id()
                if default_device_id != 0 and self.current_device_id != default_device_id:
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
                
                # 重新初始化 PortAudio 以强制刷新 CoreAudio 的硬件设备列表
                try:
                    sd._terminate()
                    sd._initialize()
                except Exception as init_err:
                    print(f"[PCMPlayer] 重置 PortAudio 失败: {init_err}")

                # 获取具体的默认输出设备索引，防止 CoreAudio 默认设备自动切换导致的音频泄漏
                default_device_idx = sd.default.device[1]
                print(f"[PCMPlayer] 正在启动音频流 (采样率: {self.sample_rate}Hz, 设备索引: {default_device_idx})...")
                self.stream = sd.OutputStream(
                    device=default_device_idx,
                    samplerate=self.sample_rate,
                    channels=2,
                    dtype='float32',
                    callback=self._callback,
                    blocksize=8192 
                )
                self.stream.start()
                self.current_device_id = get_default_output_device_id()
                try:
                    device_info = sd.query_devices(self.stream.device, 'output')
                    print(f"[PCMPlayer] 音频流启动成功，当前绑定设备: {device_info.get('name')}")
                except Exception as e:
                    print(f"[PCMPlayer] 查询音频设备名称失败: {e}")
            except Exception as e:
                print(f"[PCMPlayer] 启动失败: {e}")

    def _device_monitor_loop(self) -> None:
        while True:
            time.sleep(0.1)  # 高频检测（100ms 延迟，极速切歌切设备）
            # 只有在播放器处于 active 状态时才进行检测 and 切换，避免空闲时频繁查询
            if not self.is_active:
                continue
            try:
                default_device_id = get_default_output_device_id()
                if default_device_id != 0 and self.current_device_id != 0 and self.current_device_id != default_device_id:
                    print(f"[PCMPlayer] 检测到 macOS 默认输出设备 ID 变更: {self.current_device_id} -> {default_device_id}，正在自动切换流...")
                    # 立即更新 ID 锁，防止重入
                    self.current_device_id = default_device_id
                    
                    # 在锁保护下立即停止当前流以切断扬声器输出，防止音量调节过渡时的爆音或音量暴增
                    with self._lock:
                        if self.stream:
                            try:
                                self.stream.stop()
                            except:
                                pass
                    self._recreate_stream()
            except Exception as e:
                pass

    def _recreate_stream(self) -> None:
        with self._lock:
            if self.stream:
                try:
                    self.stream.close()
                except Exception as e:
                    print(f"[PCMPlayer] 关闭旧音频流失败: {e}")
            
            # 重新初始化 PortAudio 以强制刷新 CoreAudio 的硬件设备列表
            try:
                sd._terminate()
                sd._initialize()
            except Exception as init_err:
                print(f"[PCMPlayer] 重置 PortAudio 失败: {init_err}")

            try:
                default_device_idx = sd.default.device[1]
                self.stream = sd.OutputStream(
                    device=default_device_idx,
                    samplerate=self.sample_rate,
                    channels=2,
                    dtype='float32',
                    callback=self._callback,
                    blocksize=8192
                )
                self.stream.start()
                self.current_device_id = get_default_output_device_id()
                try:
                    device_info = sd.query_devices(self.stream.device, 'output')
                    print(f"[PCMPlayer] 音频流切换成功，当前绑定设备: {device_info.get('name')}")
                except Exception as e:
                    print(f"[PCMPlayer] 查询音频设备名称失败: {e}")
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
