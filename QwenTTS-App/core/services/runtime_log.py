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
            self._trim_locked()

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    lines = f.readlines()[-limit:]
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
