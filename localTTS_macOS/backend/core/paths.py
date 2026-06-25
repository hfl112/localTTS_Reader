import os
import shutil
import sys
from typing import ClassVar


class RuntimePaths:
    """Manages all runtime paths with fallback to local development directory."""

    # Default App Support path on macOS
    DEFAULT_APP_SUPPORT: ClassVar[str] = os.path.expanduser("~/Library/Application Support/QwenTTS")

    def __init__(self) -> None:
        # 1. Resolve Application Support Root
        self.app_support_path: str = os.path.abspath(
            os.environ.get("TTS_APP_SUPPORT_PATH") or self.DEFAULT_APP_SUPPORT
        )

        # 2. Resolve subdirectories
        self.data_path: str = os.path.abspath(
            os.environ.get("TTS_DATA_PATH") or os.path.join(self.app_support_path, "Data")
        )
        self.cache_path: str = os.path.abspath(
            os.environ.get("TTS_CACHE_PATH") or os.path.join(self.app_support_path, "Cache")
        )
        self.podcasts_path: str = os.path.abspath(
            os.environ.get("TTS_PODCASTS_PATH") or os.path.join(self.app_support_path, "Podcasts")
        )
        self.models_path: str = os.path.abspath(
            os.environ.get("TTS_MODELS_PATH") or os.path.join(self.app_support_path, "Models")
        )
        self.logs_path: str = os.path.abspath(
            os.environ.get("TTS_LOGS_PATH") or os.path.join(self.app_support_path, "Logs")
        )

        # 3. Resolve resource paths (ICL reference audio, mlx_audio directory, ffmpeg)
        # Native backend resources are physically self-contained next to
        # core/: backend/{core,mlx_audio,URL-Reader,reference}.  Never infer a
        # path through the legacy QwenTTS-App workspace.
        project_root: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        # Reference audio directory (often local in repo resource bundle or Contents/Resources/)
        default_ref_path: str = os.path.join(project_root, "reference")
        self.reference_path: str = os.path.abspath(
            os.environ.get("TTS_REFERENCE_PATH") or default_ref_path
        )

        # MLX-Audio directory
        self.mlx_audio_path: str = os.path.abspath(
            os.environ.get("MLX_AUDIO_PATH") or os.path.join(project_root, "mlx_audio")
        )

        # FFmpeg executable path
        self.ffmpeg_path: str | None = os.environ.get("TTS_FFMPEG_PATH")
        if self.ffmpeg_path:
            self.ffmpeg_path = os.path.abspath(self.ffmpeg_path)

        # Ensure all required directories exist (idempotent mkdir, benign on import).
        self.ensure_directories()

        # Heavy/legacy file migration is NOT run at import time — only via an
        # explicit init() at backend startup, so importing this module in
        # tests/CLI has no filesystem-copy side effects.
        self._project_root = project_root

    def init(self) -> None:
        """Explicit one-time startup step: run legacy data migration.

        Call once from backend startup (init_runtime_services). Idempotent —
        migration skips files that already exist at the destination.
        """
        self.migrate_legacy_data(self._project_root)

    def ensure_directories(self) -> None:
        for path in (self.app_support_path, self.data_path, self.cache_path, self.podcasts_path, self.models_path, self.logs_path):
            os.makedirs(path, exist_ok=True)

    def migrate_legacy_data(self, project_root: str) -> None:
        """
        One-time migration of developer data from the QwenTTS-App/data/ directory 
        and root podcasts/ directory to the new Application Support directory structure.
        """
        legacy_data_dir = os.path.join(project_root, "QwenTTS-App", "data")
        if not os.path.exists(legacy_data_dir):
            return

        # 1. Migrate primary JSON configs and DB files
        for item in os.listdir(legacy_data_dir):
            src_path = os.path.join(legacy_data_dir, item)
            if os.path.isdir(src_path):
                continue
            dst_path = os.path.join(self.data_path, item)
            if os.path.exists(dst_path):
                continue
            try:
                shutil.copy2(src_path, dst_path)
                print(f"[Migration] Copied Config/DB: {src_path} -> {dst_path}")
            except Exception as error:
                print(f"[Migration] Error copying data item {item}: {error}")

        # 2. Migrate legacy Cache (.npy) files
        legacy_cache_dir = os.path.join(legacy_data_dir, "cache")
        if os.path.exists(legacy_cache_dir) and os.path.isdir(legacy_cache_dir):
            for item in os.listdir(legacy_cache_dir):
                if not item.endswith(".npy"):
                    continue
                src_path = os.path.join(legacy_cache_dir, item)
                dst_path = os.path.join(self.cache_path, item)
                if os.path.exists(dst_path):
                    continue
                try:
                    shutil.copy2(src_path, dst_path)
                except Exception:
                    pass

        # 3. Migrate legacy podcasts (.wav) files from repo root 'podcasts/'
        legacy_podcasts_dir = os.path.join(project_root, "podcasts")
        if os.path.exists(legacy_podcasts_dir) and os.path.isdir(legacy_podcasts_dir):
            for item in os.listdir(legacy_podcasts_dir):
                if not item.endswith(".wav"):
                    continue
                src_path = os.path.join(legacy_podcasts_dir, item)
                dst_path = os.path.join(self.podcasts_path, item)
                if os.path.exists(dst_path):
                    continue
                try:
                    shutil.copy2(src_path, dst_path)
                except Exception:
                    pass


# Global single instance initialized once on import
runtime_paths: RuntimePaths = RuntimePaths()
