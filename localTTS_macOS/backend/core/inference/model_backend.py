"""ModelBackend — the narrow seam around the part that touches MLX/GPU.

Per ADR-001 (CONTEXT.md §3, decision #6): the seam wraps ONLY model load /
generate / unload. Everything valuable (prompt-kwargs building, normalization,
caching, priority queue, idle unload) lives ABOVE this seam in InferenceEngine,
so it is exercised by tests through FakeBackend without ever touching MLX.

Two adapters justify the seam:
  - MLXBackend  — production, runs the real model on the GPU.
  - FakeBackend — tests, returns a deterministic sine wave, never imports mlx.

mlx is imported lazily inside MLXBackend so that importing this module (and
instantiating FakeBackend) does not pull in mlx — see test_model_backend.py.
"""

import os
import sys
from typing import Iterator, Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class ModelBackend(Protocol):
    """The substitutable side of the inference seam.

    generate yields raw, mono model output frames (np.float32, 1-D). All
    post-processing (stereo broadcast, gain normalization, clipping) happens
    above this seam in InferenceEngine.
    """

    def load(self, abs_model_path: str) -> None: ...

    def generate(self, text: str, generate_kwargs: dict) -> Iterator[np.ndarray]: ...

    def unload(self) -> None: ...

    @property
    def is_loaded(self) -> bool: ...


class MLXBackend:
    """Production backend. Wraps mlx_audio.load_model + model.generate.

    The load/generate calls were lifted from the original tts_engine.py (now
    deleted); all synthesis now flows through InferenceEngine over this seam.
    """

    def __init__(self, mlx_audio_path: str | None = None):
        self.base_dir = self._resolve_base_dir(mlx_audio_path)
        # Make mlx_audio importable, same as tts_engine.py does.
        if self.base_dir not in sys.path:
            sys.path.insert(0, self.base_dir)
        self.model = None

    @staticmethod
    def _resolve_base_dir(mlx_audio_path: str | None) -> str:
        resolved = mlx_audio_path or os.environ.get("MLX_AUDIO_PATH")
        if not resolved:
            workspace_env = os.environ.get("TTS_WORKSPACE_PATH")
            if workspace_env:
                resolved = os.path.join(workspace_env, "mlx_audio")
        if not resolved:
            resolved = "../../mlx_audio"
        if os.path.isabs(resolved):
            return os.path.abspath(resolved)
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", resolved))

    @property
    def is_loaded(self) -> bool:
        return self.model is not None

    def load(self, abs_model_path: str) -> None:
        if self.model is not None:
            return
        # Lazy import: keeps mlx out of the module-import path (tests rely on this).
        from mlx_audio.utils import load_model

        print(f"[MLXBackend] 正在从绝对路径加载模型: {abs_model_path}...")
        self.model = load_model(abs_model_path)
        print("[MLXBackend] 模型加载完成")

    def generate(self, text: str, generate_kwargs: dict) -> Iterator[np.ndarray]:
        if self.model is None:
            raise RuntimeError("MLXBackend.generate called before load()")
        for result in self.model.generate(text, **generate_kwargs):
            # Yield a raw mono numpy frame; everything above the seam (stereo
            # broadcast, gain, clip) is backend-agnostic and pure numpy.
            yield np.asarray(result.audio, dtype=np.float32).reshape(-1)

    def unload(self) -> None:
        self.model = None
        try:
            import gc

            import mlx.core as mx

            mx.clear_cache()
            gc.collect()
        except Exception:
            pass

    def active_memory_mb(self) -> float:
        """Best-effort GPU memory query; 0.0 if Metal is unavailable/idle."""
        try:
            import mlx.core as mx

            return mx.get_active_memory() / 1024 / 1024
        except Exception:
            return 0.0


class FakeBackend:
    """Test backend. Deterministic sine wave, no GPU, no mlx import.

    Lets every layer above the seam (cache key, normalization, priority queue,
    idle unload) be unit-tested without loading a real model.
    """

    def __init__(self, sample_rate: int = 24000, frames: int = 3, samples_per_frame: int = 2400):
        self.sample_rate = sample_rate
        self.frames = frames
        self.samples_per_frame = samples_per_frame
        self._loaded_path: str | None = None

    @property
    def is_loaded(self) -> bool:
        return self._loaded_path is not None

    def load(self, abs_model_path: str) -> None:
        self._loaded_path = abs_model_path

    def generate(self, text: str, generate_kwargs: dict) -> Iterator[np.ndarray]:
        if self._loaded_path is None:
            raise RuntimeError("FakeBackend.generate called before load()")
        # A quiet 220Hz tone, scaled to ~0.3 so the normalization step above the
        # seam has headroom to exercise its gain path deterministically.
        t = np.arange(self.samples_per_frame, dtype=np.float32) / self.sample_rate
        tone = (0.3 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
        for _ in range(self.frames):
            yield tone.copy()

    def unload(self) -> None:
        self._loaded_path = None

    def active_memory_mb(self) -> float:
        return 0.0
