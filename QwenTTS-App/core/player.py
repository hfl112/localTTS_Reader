import sounddevice as sd
import numpy as np
import queue
import threading
import time
import ctypes
from ctypes import c_uint32, byref, Structure, CFUNCTYPE, c_void_p, c_int
from typing import Optional, Any

# CoreAudio ctypes 结构体，用于在 macOS 硬件层实时检测默认输出通道变化
class AudioObjectPropertyAddress(Structure):
    _fields_ = [
        ('mSelector', c_uint32),
        ('mScope', c_uint32),
        ('mElement', c_uint32),
    ]

AudioObjectPropertyListenerProc = CFUNCTYPE(
    c_int,
    c_uint32, # AudioObjectID
    c_uint32, # inNumberAddresses
    c_void_p, # const AudioObjectPropertyAddress*
    c_void_p  # void* inClientData
)

try:
    _coreaudio = ctypes.CDLL('/System/Library/Frameworks/CoreAudio.framework/CoreAudio')
except Exception as e:
    _coreaudio = None
    print(f"[PCMPlayer] 载入 CoreAudio 失败: {e}")

def get_default_output_device_id() -> int:
    if not _coreaudio:
        return 0
    try:
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
        self.is_paused: bool = False  
        self.is_prebuffering: bool = True
        self.min_chunks_to_start: int = 1
        
        self.stream: Optional[sd.OutputStream] = None
        self.leftover_data: Optional[np.ndarray] = None
        self.volume_scale: float = 1.0  
        self.playback_finished_event: threading.Event = threading.Event()
        self.playback_finished_event.set()
        
        self.current_device_id: int = 0
        self.device_is_changing: bool = False
        self._last_device_change_time: float = 0.0
        self.device_changed_event: threading.Event = threading.Event()
        self._shutdown_event = threading.Event()
        
        self._lock: threading.Lock = threading.Lock()
        
        self._ensure_stream_started()
        self._register_device_change_listener()
        
        self._device_monitor_thread = threading.Thread(
            target=self._device_monitor_loop,
            name="audio-device-monitor",
            daemon=True,
        )
        self._device_monitor_thread.start()

    def _register_device_change_listener(self) -> None:
        if not _coreaudio:
            return
        try:
            self._c_listener = AudioObjectPropertyListenerProc(self._on_device_changed)
            address = AudioObjectPropertyAddress(
                mSelector=1684370979,
                mScope=1735159650,
                mElement=0
            )
            status = _coreaudio.AudioObjectAddPropertyListener(
                1,
                byref(address),
                self._c_listener,
                None
            )
            if status == 0:
                print("[PCMPlayer] 成功注册 CoreAudio 默认输出设备监听器")
        except Exception as e:
            print(f"[PCMPlayer] 注册 CoreAudio 监听器异常: {e}")

    def _on_device_changed(self, obj_id: int, n_addresses: int, addresses: c_void_p, client_data: c_void_p) -> int:
        with self._lock:
            self.device_is_changing = True
            self._last_device_change_time = time.time()
        self.device_changed_event.set()
        return 0

    def _ensure_stream_started(self) -> None:
        if self.stream is None or not self.stream.active:
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                except:
                    pass
                self.stream = None

            # 指数退避重试，等待 CoreAudio 设备切换稳定
            delays = [0.0, 0.5, 1.0, 2.0]
            for attempt, delay in enumerate(delays):
                if delay > 0:
                    time.sleep(delay)
                try:
                    print(f"[PCMPlayer] 正在启动音频流 (采样率: {self.sample_rate}Hz, 尝试 {attempt+1}/{len(delays)})...")
                    new_stream = sd.OutputStream(
                        samplerate=self.sample_rate,
                        channels=2,
                        dtype='float32',
                        callback=self._callback,
                        blocksize=8192
                    )
                    new_stream.start()
                    self.stream = new_stream
                    self.current_device_id = get_default_output_device_id()
                    with self._lock:
                        self.device_is_changing = False
                    print("[PCMPlayer] 音频流启动成功")
                    return
                except Exception as e:
                    print(f"[PCMPlayer] 启动失败 (尝试 {attempt+1}): {e}")
                    try:
                        sd._terminate()
                        sd._initialize()
                    except Exception:
                        pass
            print("[PCMPlayer] 所有重试均失败，音频流未能启动")

    def _device_monitor_loop(self) -> None:
        while not self._shutdown_event.is_set():
            self.device_changed_event.wait()
            self.device_changed_event.clear()
            if self._shutdown_event.is_set():
                break
            # 等待 1.5s 让 CoreAudio 完成设备图重建，防止在切换抖动期间立即开流
            if self._shutdown_event.wait(1.5):
                break
            self.device_changed_event.clear()

            try:
                default_device_id = get_default_output_device_id()
                print(f"[DeviceMonitor] 切换检测: {self.current_device_id} -> {default_device_id}", flush=True)
                if default_device_id != 0 and self.current_device_id != 0 and self.current_device_id != default_device_id:
                    self.current_device_id = default_device_id
                    with self._lock:
                        self._last_device_change_time = time.time()
                    
                    with self._lock:
                        self.device_is_changing = True
                        stream_to_abort = self.stream
                        
                    if stream_to_abort:
                        try:
                            stream_to_abort.abort()
                        except Exception as abort_err:
                            print(f"[DeviceMonitor] stream.abort() 异常: {abort_err}", flush=True)
                    
                    if self.is_active:
                        self._recreate_stream()
            except Exception as e:
                print(f"[DeviceMonitor] 发生异常: {e}", flush=True)

    def _recreate_stream(self) -> None:
        with self._lock:
            if not self.is_active:
                return
            stream_to_close = self.stream
            self.stream = None
            
        if stream_to_close:
            try:
                stream_to_close.close()
            except Exception as e:
                print(f"[PCMPlayer] 关闭旧音频流失败: {e}")
        
        try:
            sd._terminate()
            sd._initialize()
        except Exception as init_err:
            print(f"[PCMPlayer] 重置 PortAudio 失败: {init_err}")

        # 指数退避重试，等待新设备 AU 图完全就绪
        delays = [0.0, 0.5, 1.0, 2.0]
        for attempt, delay in enumerate(delays):
            if delay > 0:
                time.sleep(delay)
            try:
                new_stream = sd.OutputStream(
                    samplerate=self.sample_rate,
                    channels=2,
                    dtype='float32',
                    callback=self._callback,
                    blocksize=8192
                )
                new_stream.start()

                with self._lock:
                    if not self.is_active:
                        new_stream.close()
                        return
                    self.stream = new_stream
                    self.current_device_id = get_default_output_device_id()
                    self.device_is_changing = False

                try:
                    device_info = sd.query_devices(new_stream.device, 'output')
                    print(f"[PCMPlayer] 音频流自动切换成功 (尝试 {attempt+1})，当前绑定设备: {device_info.get('name')}")
                except Exception as e:
                    print(f"[PCMPlayer] 查询音频设备名称失败: {e}")
                return
            except Exception as e:
                print(f"[PCMPlayer] 音频流自动重建失败 (尝试 {attempt+1}): {e}")
                try:
                    sd._terminate()
                    sd._initialize()
                except Exception:
                    pass
        print("[PCMPlayer] _recreate_stream: 所有重试均失败")

    def restart_device(self) -> None:
        """强行重置音频流并重新绑定至最新系统默认输出设备"""
        print("[PCMPlayer] 手动重启音频流并刷新设备列表...")
        with self._lock:
            self.device_is_changing = True
            self._last_device_change_time = time.time()
            stream_to_close = self.stream
            self.stream = None
            
        if stream_to_close:
            try:
                stream_to_close.stop()
                stream_to_close.close()
            except Exception as e:
                print(f"[PCMPlayer] 关闭旧流失败: {e}")
                
        try:
            sd._terminate()
            sd._initialize()
            print("[PCMPlayer] PortAudio 刷新初始化成功")
        except Exception as e:
            print(f"[PCMPlayer] 刷新 PortAudio 失败: {e}")
            
        self._ensure_stream_started()

    def is_device_switching(self, grace_sec: float = 5.0) -> bool:
        with self._lock:
            return self.device_is_changing or (
                time.time() - self._last_device_change_time < grace_sec
            )

    def close(self) -> None:
        self._shutdown_event.set()
        self.device_changed_event.set()
        self.playback_finished_event.set()
        self.remove_listener()
        with self._lock:
            self.is_active = False
            self.is_paused = False
            self.device_is_changing = False
            if self.stream is not None:
                try:
                    self.stream.stop()
                    self.stream.close()
                except:
                    pass
                self.stream = None
            try:
                sd._terminate()
                print("[PCMPlayer] PortAudio 已终止")
            except:
                pass
        if (
            hasattr(self, "_device_monitor_thread")
            and self._device_monitor_thread is not threading.current_thread()
        ):
            self._device_monitor_thread.join(2.0)

    def remove_listener(self) -> None:
        if hasattr(self, '_c_listener') and _coreaudio:
            try:
                address = AudioObjectPropertyAddress(
                    mSelector=1684370979,
                    mScope=1735159650,
                    mElement=0
                )
                _coreaudio.AudioObjectRemovePropertyListener(
                    1,
                    byref(address),
                    self._c_listener,
                    None
                )
                print("[PCMPlayer] CoreAudio 监听器已成功卸载")
                self._c_listener = None
            except:
                pass

    def __del__(self) -> None:
        try:
            self.close()
        except:
            pass

    def _callback(self, outdata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        data_to_fill = np.zeros((frames, 2), dtype=np.float32)
        
        if status:
            if 'output_underflow' not in str(status):
                print(f"[PCMPlayer] Stream status: {status}")
                
        with self._lock:
            if self.is_prebuffering:
                if self.audio_queue.qsize() >= self.min_chunks_to_start:
                    self.is_prebuffering = False
                else:
                    outdata.fill(0)
                    return

            if not self.is_active or self.is_paused:
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
                
            outdata[:] = np.clip(data_to_fill * self.volume_scale, -0.98, 0.98)

    def get_queue_duration(self) -> float:
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
        self.playback_finished_event.set()

    def is_running(self) -> bool:
        return not self.playback_finished_event.is_set()
