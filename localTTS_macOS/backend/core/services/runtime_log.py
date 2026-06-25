import json
import os
import threading
import time
from typing import Any


class RuntimeEventLog:
    def __init__(self, path: str, max_events: int = 500) -> None:
        self.path = path
        self.max_events = max_events
        self._lock = threading.Lock()
        # Trim periodically rather than on every append (each trim is an O(n)
        # full-file read+rewrite). File stays bounded at max_events + interval.
        self._trim_interval = max(50, max_events // 5)
        self._writes_since_trim = 0

    def record(self, event: str, **fields: Any) -> None:
        row = {
            "ts": time.time(),
            "event": event,
            **fields,
        }
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            self._writes_since_trim += 1
            if self._writes_since_trim >= self._trim_interval:
                self._trim_locked()
                self._writes_since_trim = 0

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        # 逻辑上日志只保留最近 max_events 条；trim 是延迟触发的，文件可能暂时
        # 多出若干行，因此读取上限取 min(limit, max_events)，与延迟 trim 解耦。
        effective = min(limit, self.max_events)
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    lines = f.readlines()[-effective:]
            except Exception:
                return []

        events: list[dict[str, Any]] = []
        for line in lines:
            try:
                events.append(json.loads(line))
            except Exception:
                continue
        return events

    def _trim_locked(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) <= self.max_events:
                return
            tmp_path = self.path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.writelines(lines[-self.max_events :])
            os.replace(tmp_path, self.path)
        except Exception:
            pass
