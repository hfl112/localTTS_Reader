"""Fail-fast release checks for QwenTTS.app and its optional DMG."""

from __future__ import annotations

import os
import plistlib
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP = ROOT / "dist/QwenTTS.app"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)
    print(f"[PASS] {message}")


def run(args: list[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(args, capture_output=True, text=True, env=env)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def main() -> None:
    check(APP.is_dir(), f"application exists: {APP}")
    executable = APP / "Contents/MacOS/QwenTTS"
    python = APP / "Contents/Resources/PythonRuntime/bin/python3"
    backend = APP / "Contents/Resources/Backend/core/backend.py"
    check(os.access(executable, os.X_OK), "native executable is executable")
    check(os.access(python, os.X_OK), "bundled Python is executable")
    check(backend.is_file(), "backend entry point is present")
    check((APP / "Contents/Resources/Backend/mlx_audio").is_dir(), "MLX-Audio package is present")
    check((APP / "Contents/Resources/Backend/URL-Reader/reader_service.py").is_file(), "URL Reader is present")

    with (APP / "Contents/Info.plist").open("rb") as handle:
        info = plistlib.load(handle)
    check(info.get("LSMinimumSystemVersion") == "14.0", "deployment target is macOS 14.0")
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(APP)])
    print("[PASS] strict code-signing verification")

    runtime = APP / "Contents/Resources/PythonRuntime"
    environment = os.environ.copy()
    environment["PYTHONHOME"] = str(runtime)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = ":".join(
        [
            str(APP / "Contents/Resources"),
            str(APP / "Contents/Resources/Backend"),
            str(runtime / "lib/python3.11/site-packages"),
        ]
    )
    environment["TTS_APP_SUPPORT_PATH"] = tempfile.mkdtemp(prefix="qwentts-diagnostic-")
    probe = run(
        [
            str(python),
            "-c",
            "import sys, fastapi, mlx_audio; "
            "assert sys.prefix.endswith('PythonRuntime'); print(sys.prefix)",
        ],
        env=environment,
    )
    check(probe.endswith("PythonRuntime"), "Python runtime is relocatable and imports production modules")

    dmg = ROOT / "dist/QwenTTS.dmg"
    if dmg.exists():
        run(["hdiutil", "verify", str(dmg)])
        print("[PASS] DMG checksum verification")
    else:
        print("[INFO] DMG not built yet; run make_dmg.py after app diagnostics")


if __name__ == "__main__":
    main()
