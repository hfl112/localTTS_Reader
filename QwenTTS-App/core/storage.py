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

    def _init_db(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
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

    def load_config(self) -> dict:
        if not os.path.exists(self.config_path):
            return self.default_config
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

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
        if not os.path.exists(self.state_path):
            return self.default_state
        with open(self.state_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_state(self, state: dict) -> None:
        self._atomic_save_json(self.state_path, state)


    def add_cache_metadata(self, md5: str, text: str, model: str, voice: str, duration: float, file_path: str) -> None:
        conn = sqlite3.connect(self.db_path)
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
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cache_metadata ORDER BY created_at DESC")
        rows = cursor.fetchall()
        result = [dict(row) for row in rows]
        conn.close()
        return result

    def delete_cache_by_md5(self, md5: str) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cache_metadata WHERE md5 = ?", (md5,))
        conn.commit()
        conn.close()

    def get_cache_by_md5(self, md5: str) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.db_path)
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
