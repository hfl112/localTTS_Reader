import threading
import time
from typing import Any


class RuntimeState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.main_is_playing = False
        self.main_title = ""
        self.main_progress = "0/0"
        # Live karaoke index — RuntimeState is the single in-memory authority
        # for the currently-playing chunk; persisted to state.json only by the
        # playback thread (throttled), never written from read-only endpoints.
        self.main_index = 0
        self.main_total = 0
        self.current_playing_podcast: str | None = None
        self.current_playing_md5: str | None = None
        self.last_active_time = time.time()

    def set_main(
        self,
        *,
        is_playing: bool | None = None,
        title: str | None = None,
        progress: str | None = None,
        index: int | None = None,
        total: int | None = None,
    ) -> None:
        with self._lock:
            if is_playing is not None:
                self.main_is_playing = is_playing
            if title is not None:
                self.main_title = title
            if index is not None:
                self.main_index = index
            if total is not None:
                self.main_total = total
            if index is not None or total is not None:
                # derive the display string from the numeric authority
                self.main_progress = (
                    f"{self.main_index + 1}/{self.main_total}" if self.main_total else "0/0"
                )
            elif progress is not None:
                self.main_progress = progress

    def set_current_media(
        self,
        *,
        podcast: str | None = None,
        md5: str | None = None,
    ) -> None:
        with self._lock:
            self.current_playing_podcast = podcast
            self.current_playing_md5 = md5

    def clear_current_media(self, *, keep_md5: bool = False) -> None:
        with self._lock:
            self.current_playing_podcast = None
            if not keep_md5:
                self.current_playing_md5 = None

    def touch_activity(self) -> None:
        with self._lock:
            self.last_active_time = time.time()

    def update_activity_if_busy(self, active: bool) -> None:
        if active:
            self.touch_activity()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "main_is_playing": self.main_is_playing,
                "main_title": self.main_title,
                "main_progress": self.main_progress,
                "main_index": self.main_index,
                "main_total": self.main_total,
                "current_podcast_file": self.current_playing_podcast,
                "current_playing_md5": self.current_playing_md5,
                "last_active_time": self.last_active_time,
            }
