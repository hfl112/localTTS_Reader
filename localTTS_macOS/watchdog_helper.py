import os
import sys
import time
import subprocess
from typing import NoReturn

def main() -> None:
    print(f"[Helper] Started. PID: {os.getpid()}, PGID: {os.getpgrp()}", flush=True)
    
    # 模拟启动一个同进程组的孙子进程，用来测试进程组强杀 (Process Group Kill)
    # 使用换行符拼接的多行脚本，保证 -c 运行时不会触发语法错误
    script: str = (
        "import os\n"
        "import time\n"
        "print(f'[Helper-Child] Child PID: {os.getpid()}, Child PGID: {os.getpgrp()}', flush=True)\n"
        "while True:\n"
        "    time.sleep(0.5)\n"
    )
    
    child: subprocess.Popen = subprocess.Popen([sys.executable, "-c", script])
    print(f"[Helper] Spawned dummy child process. Child PID: {child.pid}", flush=True)

    fd_str: str | None = os.environ.get("TTS_WATCHDOG_FD")
    if not fd_str:
        print("[Helper] Error: TTS_WATCHDOG_FD is not set.", flush=True)
        sys.exit(1)
        
    try:
        fd: int = int(fd_str)
    except ValueError:
        print(f"[Helper] Error: invalid FD: {fd_str}", flush=True)
        sys.exit(1)

    print(f"[Helper] Waiting for watchdog EOF on FD: {fd}...", flush=True)
    try:
        data: bytes = os.read(fd, 1)
        if not data:
            print("[Helper] Watchdog EOF detected. Shutting down gracefully...", flush=True)
            child.terminate()
            child.wait()
            sys.exit(0)
    except OSError as e:
        print(f"[Helper] Watchdog FD error: {e}. Shutting down...", flush=True)
        child.terminate()
        child.wait()
        sys.exit(1)

if __name__ == "__main__":
    main()
