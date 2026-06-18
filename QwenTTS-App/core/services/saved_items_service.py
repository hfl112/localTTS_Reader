import hashlib
import json
import os
import threading
import time
from typing import Any


class SavedItemsService:
    def __init__(self, base_dir: str) -> None:
        self.save_file = os.path.join(base_dir, "data", "saved_for_later.json")
        self._lock = threading.Lock()

    def load(self) -> list[dict[str, Any]]:
        with self._lock:
            if not os.path.exists(self.save_file):
                return []
            try:
                with open(self.save_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []

    def write(self, items: list[dict[str, Any]]) -> None:
        with self._lock:
            os.makedirs(os.path.dirname(self.save_file), exist_ok=True)
            with open(self.save_file, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)

    def save(
        self,
        text: str,
        source: str = "web",
        voice: str | None = None,
        title: str | None = None,
    ) -> int:
        text = text.strip()
        if not text:
            return 0
        md5_val = hashlib.md5(text.encode("utf-8")).hexdigest()
        items = self.load()

        if not any(item.get("md5") == md5_val for item in items):
            display_title = title if title else (text[:20].replace("\n", " ") + "...")
            items.append(
                {
                    "timestamp": time.time(),
                    "text": text,
                    "title": display_title,
                    "source": source,
                    "voice": voice,
                    "is_exported": False,
                    "md5": md5_val,
                }
            )
            if len(items) > 5:
                items = items[-5:]
            self.write(items)

        return len(items)

    def delete(self, *, md5: str | None = None, index: int | None = None) -> bool:
        items = self.load()
        if md5:
            new_items = [item for item in items if item.get("md5") != md5]
            if len(new_items) < len(items):
                self.write(new_items)
                return True
            return False

        if index is not None and 0 <= index < len(items):
            items.pop(index)
            self.write(items)
            return True
        return False

    def clear(self) -> None:
        self.write([])

    def selected_text(self, indices: list[int]) -> tuple[str, str | None, str | None]:
        items = self.load()
        if not items:
            return "", None, None
        text = "\n\n".join(
            items[idx].get("text", "") for idx in indices if 0 <= idx < len(items)
        )
        first_idx = indices[0] if indices else 0
        voice = items[first_idx].get("voice") if 0 <= first_idx < len(items) else None
        md5 = items[first_idx].get("md5") if 0 <= first_idx < len(items) else None
        return text, voice, md5
