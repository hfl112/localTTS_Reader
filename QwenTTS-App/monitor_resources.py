import os
import sys
import time
import requests
import psutil
from typing import Optional, Dict, Any, List

def find_backend_process() -> Optional[psutil.Process]:
    """
    寻找运行中的 core/backend.py FastAPI 后端进程。
    """
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmd = proc.info['cmdline']
            if cmd and any('backend.py' in part for part in cmd):
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return None

def get_sys_info() -> Dict[str, Any]:
    """
    获取系统级的 CPU 与内存占用情况。
    """
    vm = psutil.virtual_memory()
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "mem_used_gb": vm.used / (1024**3),
        "mem_total_gb": vm.total / (1024**3),
        "mem_percent": vm.percent
    }

def monitor_loop() -> None:
    print("\033[2J\033[H", end="") # 清屏并将光标重置到左上角
    print("=" * 60)
    print(" QwenTTS 资源与运行状态实时监控面板 ".center(60, "="))
    print("=" * 60)
    
    url = "http://127.0.0.1:8001/status"
    
    # 初始化 CPU 百分比计算
    psutil.cpu_percent(interval=None)
    backend_proc = find_backend_process()
    if backend_proc:
        try:
            backend_proc.cpu_percent(interval=None)
            for child in backend_proc.children(recursive=True):
                child.cpu_percent(interval=None)
        except:
            pass

    while True:
        # 1. 系统级占用
        sys_info = get_sys_info()
        
        # 2. 从 FastAPI 接口拉取状态（包括模型显存占用）
        api_data: Optional[Dict[str, Any]] = None
        server_status = "OFFLINE"
        try:
            resp = requests.get(url, timeout=0.5)
            if resp.status_code == 200:
                api_data = resp.json()
                server_status = "ONLINE"
        except requests.RequestException:
            pass
            
        # 3. 统计后端与推理进程的资源占用
        backend_proc = find_backend_process()
        proc_info: List[Dict[str, Any]] = []
        if backend_proc:
            try:
                proc_info.append({
                    "name": "Backend Server (FastAPI)",
                    "pid": backend_proc.pid,
                    "cpu": backend_proc.cpu_percent(interval=None),
                    "rss_mb": backend_proc.memory_info().rss / (1024**2)
                })
                # 寻找推理子进程（FastAPI 通过 multiprocessing 派生的子进程）
                for child in backend_proc.children(recursive=True):
                    proc_info.append({
                        "name": "Inference Worker (MLX)",
                        "pid": child.pid,
                        "cpu": child.cpu_percent(interval=None),
                        "rss_mb": child.memory_info().rss / (1024**2)
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
                
        # 4. 终端渲染面板
        print("\033[H", end="") # 将光标移回左上角以实现无闪烁刷新
        print(f"当前时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("-" * 60)
        print(f"系统整体 CPU 占用:  {sys_info['cpu_percent']:5.1f}%")
        print(f"系统物理内存占用:  {sys_info['mem_used_gb']:5.2f} GB / {sys_info['mem_total_gb']:.2f} GB ({sys_info['mem_percent']}%)")
        print("-" * 60)
        
        print(f"TTS 后端服务状态:  {server_status}")
        if api_data:
            print(f"  └─ 模型激活显存 (MLX): {api_data.get('vram_mb', 0.0):.1f} MB")
            print(f"  └─ 调度器状态 (Status): {api_data.get('status_code', 'UNKNOWN')}")
            print(f"  └─ 播放器状态 (Playing): {'播放中' if api_data.get('is_playing') else '闲置'} (暂停: {api_data.get('is_paused')})")
            print(f"  └─ 音频缓冲区 (Buffer):  {api_data.get('buffer_sec', 0.0):.1f}s")
            print(f"  └─ 当前阅读进度:        {api_data.get('progress', 'N/A')}")
            title = api_data.get('title', '无')
            if len(title) > 30:
                title = title[:27] + "..."
            print(f"  └─ 文章标题:            {title}")
        else:
            print("  (FastAPI 服务未启动在 8001 端口)")
            
        print("-" * 60)
        print("进程级资源监控:")
        if proc_info:
            for p in proc_info:
                print(f"  └─ [{p['name']}] PID: {p['pid']:<5} | CPU: {p['cpu']:5.1f}% | 内存 RSS: {p['rss_mb']:6.1f} MB")
        else:
            print("  未检测到活跃的 QwenTTS 后端进程。")
        print("-" * 60)
        print("按 Ctrl+C 退出监控面板。")
        time.sleep(1.0)

if __name__ == "__main__":
    try:
        monitor_loop()
    except KeyboardInterrupt:
        print("\n监控已退出。")
