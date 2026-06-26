"""Step 1 acceptance (CONTEXT.md §4): ModelBackend seam + FakeBackend.

Key invariant: importing the seam and using FakeBackend must NOT import mlx,
so the layers above the seam can be unit-tested without a GPU or real model.
"""

import os
import subprocess
import sys

import numpy as np

from core.inference.model_backend import FakeBackend, MLXBackend, ModelBackend

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_fake_backend_yields_frames():
    be = FakeBackend(frames=3)
    assert not be.is_loaded
    be.load("/fake/model/path")
    assert be.is_loaded

    frames = list(be.generate("hello", {}))
    assert len(frames) == 3
    for f in frames:
        assert isinstance(f, np.ndarray)
        assert f.dtype == np.float32
        assert f.ndim == 1 and f.size > 0


def test_no_mlx_import_in_clean_process():
    # Order-independent guarantee: in a fresh interpreter, importing the seam
    # and exercising FakeBackend must not pull in mlx. (Asserting against the
    # shared pytest process is unreliable — sibling tests import core.backend,
    # which imports mlx globally.)
    snippet = (
        "import sys\n"
        "from core.inference.model_backend import FakeBackend\n"
        "be = FakeBackend(frames=2); be.load('/p'); list(be.generate('x', {}))\n"
        "assert 'mlx' not in sys.modules and 'mlx.core' not in sys.modules, sorted(m for m in sys.modules if m.startswith('mlx'))\n"
        "print('NO_MLX_OK')\n"
    )
    env = dict(os.environ, PYTHONPATH=_BACKEND_DIR)
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=_BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "NO_MLX_OK" in proc.stdout


def test_fake_backend_generate_before_load_raises():
    be = FakeBackend()
    try:
        list(be.generate("x", {}))
        assert False, "expected RuntimeError when generate() called before load()"
    except RuntimeError:
        pass


def test_fake_backend_unload_resets_state():
    be = FakeBackend()
    be.load("/p")
    assert be.is_loaded
    be.unload()
    assert not be.is_loaded


def test_mlx_backend_satisfies_protocol_and_resolves_base_dir():
    # Constructing MLXBackend must not load mlx (load is lazy inside .load()).
    be = MLXBackend(mlx_audio_path="/tmp/some/mlx_audio")
    assert isinstance(be, ModelBackend)
    assert be.base_dir == "/tmp/some/mlx_audio"
    assert not be.is_loaded
    # We do NOT call .load() here — that would require a real model + mlx.
