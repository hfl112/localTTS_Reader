"""Step 2 acceptance (CONTEXT.md §4): InferenceEngine kwargs/normalization/cache.

All exercised through FakeBackend — no GPU, no mlx.
"""

import os

import numpy as np

from core.inference.engine import (
    InferenceEngine,
    build_generate_kwargs,
    cache_key,
    normalize_frame,
)
from core.inference.model_backend import FakeBackend


class CountingBackend(FakeBackend):
    """FakeBackend that records how many times generate() is invoked."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.generate_calls = 0

    def generate(self, text, generate_kwargs):
        self.generate_calls += 1
        yield from super().generate(text, generate_kwargs)


class FakeStorage:
    def __init__(self, rows):
        self._rows = list(rows)  # newest-first
        self.deleted = []

    def add_cache_metadata(self, **kw):
        pass

    def get_all_cache(self):
        return list(self._rows)

    def delete_cache_by_md5(self, md5):
        self.deleted.append(md5)


def _engine(tmp_path, backend=None, storage=None):
    be = backend or FakeBackend()
    be.load("/fake")
    return InferenceEngine(be, cache_dir=str(tmp_path), storage=storage), be


# --- 串音 bug regression: key must distinguish voice / model / lang ---

def test_cache_key_distinguishes_voice():
    k1 = cache_key("你好世界", "Serena", "Qwen3-TTS-0.6B", "zh")
    k2 = cache_key("你好世界", "Ryan", "Qwen3-TTS-0.6B", "zh")
    assert k1 != k2, "same text, different voice must not collide"


def test_cache_key_distinguishes_model_and_lang():
    base = cache_key("hi", "Serena", "Qwen3-TTS-0.6B", "en")
    assert base != cache_key("hi", "Serena", "Qwen3-TTS-1.7B", "en")
    assert base != cache_key("hi", "Serena", "Qwen3-TTS-0.6B", "zh")


# --- read-through cache: hit must skip the backend (no GPU) ---

def test_cache_hit_skips_backend(tmp_path):
    eng, be = _engine(tmp_path, backend=CountingBackend(frames=2))

    first = list(eng.synthesize_local("你好世界", {"voice": "Serena"}))
    assert be.generate_calls == 1
    assert len(first) > 0

    second = list(eng.synthesize_local("你好世界", {"voice": "Serena"}))
    assert be.generate_calls == 1, "cache hit must not call the backend again"
    assert len(second) > 0


def test_different_voice_is_a_cache_miss(tmp_path):
    eng, be = _engine(tmp_path, backend=CountingBackend(frames=2))
    list(eng.synthesize_local("你好世界", {"voice": "Serena"}))
    list(eng.synthesize_local("你好世界", {"voice": "Ryan"}))
    assert be.generate_calls == 2, "different voice must re-synthesize, not replay"


# --- normalization: stereo, clamped to [-0.98, 0.98] ---

def test_normalize_frame_is_clamped_stereo():
    loud = np.full(1000, 5.0, dtype=np.float32)  # way over range
    out = normalize_frame(loud)
    assert out.ndim == 2 and out.shape[1] == 2
    assert out.dtype == np.float32
    assert float(np.max(np.abs(out))) <= 0.98 + 1e-6


def test_synthesized_frames_are_clamped(tmp_path):
    eng, _ = _engine(tmp_path)
    frames = list(eng.synthesize_local("hello world", {"voice": "Serena"}))
    assert frames
    for f in frames:
        assert f.shape[1] == 2
        assert float(np.max(np.abs(f))) <= 0.98 + 1e-6


# --- kwargs: per-chunk language autodetect ---

def test_kwargs_autodetect_language():
    # Declared zh but English text -> override to en.
    _, kw_en, lang_en = build_generate_kwargs("hello there", {"lang_code": "zh"}, None)
    assert lang_en == "en" and kw_en["lang_code"] == "en"
    # Declared en but Chinese text -> override to zh.
    _, kw_zh, lang_zh = build_generate_kwargs("你好啊", {"lang_code": "en"}, None)
    assert lang_zh == "zh" and kw_zh["lang_code"] == "zh"


def test_kwargs_max_tokens_capped():
    _, kw, _ = build_generate_kwargs("x" * 10000, {}, None)
    assert kw["max_tokens"] == 8192


# --- eviction mirrors manage_cache_limit ---

def test_evict_cache_drops_beyond_limit(tmp_path):
    rows = [{"md5": f"k{i}", "file_path": None} for i in range(13)]  # newest-first
    storage = FakeStorage(rows)
    eng, _ = _engine(tmp_path, storage=storage)
    eng.max_cache_items = 10
    eng.evict_cache()
    assert storage.deleted == ["k10", "k11", "k12"]
