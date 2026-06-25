import copy
import json
import os
import sqlite3
import time
from typing import Any
from core.paths import runtime_paths

class Storage:
    def __init__(self, data_dir: str | None = None) -> None:
        self.data_dir = data_dir or runtime_paths.data_path
        self.config_path = os.path.join(self.data_dir, "config.json")
        self.state_path = os.path.join(self.data_dir, "state.json")
        self.db_path = os.path.join(self.data_dir, "cache.db")
        
        # 默认设置
        self.default_config = {
            # First Sound 默认走轻量 0.6B（向导也只下载/推荐它）；1.7B-8bit 是高质量进阶项，
            # 由用户在设置里显式选择。务必与 SetupWizard 下载的模型一致，否则首启试音会
            # 因加载不存在的 1.7B 而无声。
            "model": "Qwen3-TTS-0.6B",
            "voice": "Serena",
            "temperature": 0.2,
            "top_p": 0.5,
            "seed": 42,
            "repetition_penalty": 1.1,
            "lang_code": "zh",
            "battery_podcast_policy": "pause",
        }
        
        # 默认运行状态（断点续传）
        self.default_state = {
            "current_article": {
                "title": "",
                "chunks": [],
                "current_index": 0
            },
            "history": []
        }

        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        # timeout 让并发写（推理 worker + API 线程 + clear）在锁竞争时重试而非
        # 立即抛 "database is locked"。WAL 在 _init_db 中启用一次后对该库持久生效。
        return sqlite3.connect(self.db_path, timeout=10.0)

    def _init_db(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        conn = self._connect()
        cursor = conn.cursor()
        # WAL 允许并发读与单写，显著降低多进程/多线程下的锁冲突。
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except Exception as e:
            print(f"[Storage] Could not enable WAL: {e}")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cache_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                md5 TEXT UNIQUE,
                text TEXT,
                model TEXT,
                voice TEXT,
                duration REAL,
                created_at REAL,
                file_path TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _load_json_or_default(self, path: str, default: dict) -> dict:
        """Load a JSON object, tolerating missing/corrupt files.

        Always returns a fresh deepcopy of `default` on miss/corruption so the
        shared `self.default_*` dict can never be mutated by callers. A corrupt
        file is backed up (not silently overwritten) for diagnosis.
        """
        if not os.path.exists(path):
            return copy.deepcopy(default)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            return data
        except Exception as error:
            try:
                backup = f"{path}.corrupt.{int(time.time())}"
                os.replace(path, backup)
                print(f"[Storage] Corrupt JSON {path} backed up to {backup}: {error}")
            except Exception:
                pass
            return copy.deepcopy(default)

    def load_config(self) -> dict:
        return self._load_json_or_default(self.config_path, self.default_config)

    def _atomic_save_json(self, file_path: str, data: dict) -> None:
        temp_path = file_path + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, file_path)
        except Exception as error:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            raise error

    def save_config(self, config: dict) -> None:
        self._atomic_save_json(self.config_path, config)

    def load_state(self) -> dict:
        return self._load_json_or_default(self.state_path, self.default_state)

    def save_state(self, state: dict) -> None:
        self._atomic_save_json(self.state_path, state)


    def add_cache_metadata(self, md5: str, text: str, model: str, voice: str, duration: float, file_path: str) -> None:
        conn = self._connect()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO cache_metadata (md5, text, model, voice, duration, created_at, file_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (md5, text, model, voice, duration, time.time(), file_path))
            conn.commit()
        except Exception as e:
            print(f"[Storage] SQLite add_cache error: {e}")
        finally:
            conn.close()

    def get_all_cache(self) -> list[dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cache_metadata ORDER BY created_at DESC")
        rows = cursor.fetchall()
        result = [dict(row) for row in rows]
        conn.close()
        return result

    def delete_cache_by_md5(self, md5: str) -> None:
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cache_metadata WHERE md5 = ?", (md5,))
        conn.commit()
        conn.close()

    def get_cache_by_md5(self, md5: str) -> dict[str, Any] | None:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM cache_metadata WHERE md5 = ?", (md5,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        except Exception as e:
            print(f"[Storage] SQLite get_cache_by_md5 error: {e}")
            return None
        finally:
            conn.close()

    def clear_cache(self) -> None:
        """Delete all cache metadata rows. Raises on failure so callers can
        surface it (cache_service previously opened a raw connection here and
        swallowed every error, reporting success even when the clear failed)."""
        conn = self._connect()
        try:
            conn.execute("DELETE FROM cache_metadata")
            conn.commit()
        finally:
            conn.close()
