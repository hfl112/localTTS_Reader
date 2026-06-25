"""Fail-fast release checks for QwenTTS.app and its optional DMG."""

from __future__ import annotations

import os
import plistlib
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP = ROOT / "dist/QwenTTS.app"

# Hardened Runtime entitlements required for an embedded Python + MLX bundle
# (must match QwenTTS/QwenTTS/QwenTTS.entitlements).
REQUIRED_ENTITLEMENTS = [
    "com.apple.security.cs.allow-jit",
    "com.apple.security.cs.allow-unsigned-executable-memory",
    "com.apple.security.cs.disable-library-validation",
    "com.apple.security.cs.allow-dyld-environment-variables",
]


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)
    print(f"[PASS] {message}")


def run(args: list[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(args, capture_output=True, text=True, env=env)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def run_info(args: list[str]) -> tuple[str, int]:
    """Run without raising; return combined stdout+stderr and the return code.
    `codesign -d`, `spctl`, `stapler` write to stderr and may exit non-zero."""
    r = subprocess.run(args, capture_output=True, text=True)
    return (r.stdout + r.stderr), r.returncode


def signing_kind(app: Path) -> str:
    """'adhoc' | 'developer-id' | 'unsigned' | 'other'."""
    out, rc = run_info(["codesign", "-dvv", str(app)])
    if rc != 0 and "code object is not signed" in out:
        return "unsigned"
    if "Signature=adhoc" in out:
        return "adhoc"
    if "Developer ID Application" in out:
        return "developer-id"
    return "other"


def check_release_signing(app: Path) -> None:
    """发布预检：entitlements 必须存在；hardened runtime / Gatekeeper / 公证装订
    在 Developer ID 构建下作硬门禁，ad-hoc/未签名构建下作 INFO（仍可本地测试）。"""
    kind = signing_kind(app)
    print(f"[INFO] signing kind: {kind}")
    release = kind == "developer-id"

    # 1. Entitlements present（ad-hoc 也应带，因 package_release 始终传 --entitlements）
    ent_out, _ = run_info(["codesign", "-d", "--entitlements", "-", "--xml", str(app)])
    missing = [e for e in REQUIRED_ENTITLEMENTS if e not in ent_out]
    check(not missing, "hardened-runtime entitlements present"
          if not missing else f"MISSING entitlements: {missing}")

    # 2. Hardened Runtime flag
    dv_out, _ = run_info(["codesign", "-d", "--verbose=2", str(app)])
    hardened = "runtime" in dv_out  # flags=0x10000(runtime)
    _gate(release, hardened, "Hardened Runtime enabled",
          "Hardened Runtime not enabled (expected until Developer ID signed)")

    # 3. Gatekeeper assessment
    _, rc = run_info(["spctl", "--assess", "--type", "execute", "--verbose=4", str(app)])
    _gate(release, rc == 0, "Gatekeeper accepts the app",
          "Gatekeeper rejects (expected until Developer ID signed + notarized)")

    # 4. Notarization staple
    _, rc = run_info(["xcrun", "stapler", "validate", str(app)])
    _gate(release, rc == 0, "notarization ticket is stapled",
          "not stapled (run notarytool + stapler staple before release)")


def _gate(release: bool, ok: bool, pass_msg: str, info_msg: str) -> None:
    if release:
        check(ok, pass_msg)
    elif ok:
        print(f"[PASS] {pass_msg}")
    else:
        print(f"[INFO] {info_msg}")


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

    # Release / notarization readiness (entitlements always; HR/Gatekeeper/staple
    # are hard gates only for Developer ID builds, INFO for ad-hoc/dev).
    check_release_signing(APP)

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
