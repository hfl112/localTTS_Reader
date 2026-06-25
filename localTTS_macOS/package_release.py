"""Build a relocatable QwenTTS.app from source and validate its signature."""

from __future__ import annotations

import argparse
import hashlib
import os
import plistlib
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_SOURCE = ROOT / "backend"
XCODE_ROOT = ROOT / "QwenTTS"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
ENTITLEMENTS = XCODE_ROOT / "QwenTTS" / "QwenTTS.entitlements"

# Pinned relocatable Python runtime (python-build-standalone).
# To bump: change the three constants below and update the SHA256 from the
# published "<asset>.sha256" sibling file on the release page.
PYTHON_BUILD_TAG = "20240107"
PYTHON_VERSION = "3.11.7"
PY_MINOR = ".".join(PYTHON_VERSION.split(".")[:2])  # e.g. "3.11"
PYTHON_RUNTIME_URL = (
    "https://github.com/indygreg/python-build-standalone/releases/download/"
    f"{PYTHON_BUILD_TAG}/cpython-{PYTHON_VERSION}+{PYTHON_BUILD_TAG}"
    "-aarch64-apple-darwin-install_only.tar.gz"
)
PYTHON_RUNTIME_SHA256 = "b042c966920cf8465385ca3522986b12d745151a72c060991088977ca36d3883"

def run(args: list[str], *, cwd: Path | None = None) -> str:
    print("[Exec]", " ".join(args))
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()

def build_native_app() -> Path:
    if shutil.which("xcodegen") is not None:
        run(["xcodegen", "generate"], cwd=XCODE_ROOT)
    xcode_available = subprocess.run(
        ["xcodebuild", "-version"], capture_output=True, text=True
    ).returncode == 0
    if xcode_available:
        run(
            [
                "xcodebuild",
                "-project",
                "QwenTTS.xcodeproj",
                "-scheme",
                "QwenTTS",
                "-configuration",
                "Release",
                "-derivedDataPath",
                str(BUILD_DIR / "DerivedData"),
                "MACOSX_DEPLOYMENT_TARGET=14.0",
                "CODE_SIGNING_ALLOWED=NO",
                "clean",
                "build",
            ],
            cwd=XCODE_ROOT,
        )
        app = BUILD_DIR / "DerivedData/Build/Products/Release/QwenTTS.app"
        if not app.is_dir():
            raise RuntimeError(f"Xcode did not produce {app}")
        return app

    app = BUILD_DIR / "Swift/QwenTTS.app"
    if app.exists():
        shutil.rmtree(app)
    executable = app / "Contents/MacOS/QwenTTS"
    executable.parent.mkdir(parents=True)
    (app / "Contents/Resources").mkdir()
    sources = sorted(str(path) for path in (XCODE_ROOT / "QwenTTS").rglob("*.swift"))
    run(
        [
            "xcrun",
            "swiftc",
            "-O",
            "-target",
            "arm64-apple-macosx14.0",
            "-module-cache-path",
            str(BUILD_DIR / "ModuleCache"),
            *sources,
            "-o",
            str(executable),
        ]
    )
    info = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleExecutable": "QwenTTS",
        "CFBundleIdentifier": "com.localtts.QwenTTS",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "QwenTTS",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "14.0",
        "NSPrincipalClass": "NSApplication",
    }
    with (app / "Contents/Info.plist").open("wb") as handle:
        plistlib.dump(info, handle)
    return app

def copy_tree(source: Path, destination: Path, *ignored: str) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        source,
        destination,
        symlinks=False,
        ignore=shutil.ignore_patterns("*.pyc", "__pycache__", *ignored),
    )

def copy_python_sources(resources_dir: str) -> None:
    mlx_src = BACKEND_SOURCE / "mlx_audio" / "mlx_audio"
    mlx_dest = Path(resources_dir) / "Backend" / "mlx_audio"
    if os.path.exists(mlx_dest):
        shutil.rmtree(mlx_dest)
    shutil.copytree(mlx_src, mlx_dest, ignore=shutil.ignore_patterns("*.pyc", "__pycache__", "tests", "node_modules", "ui", ".git"))

# dylib paths under these prefixes ship with every macOS and need no bundling.
_SYSTEM_LIB_PREFIXES = ("/usr/lib/", "/System/")

def _nonportable_ffmpeg_deps(ffmpeg: Path) -> list[str]:
    """Return dynamic dependencies that won't exist on a clean user machine."""
    out = run(["otool", "-L", str(ffmpeg)])
    deps: list[str] = []
    for line in out.splitlines()[1:]:  # first line is the binary's own id
        dep = line.strip().split(" (", 1)[0].strip()
        if dep and not dep.startswith(_SYSTEM_LIB_PREFIXES):
            deps.append(dep)
    return deps

def bundle_ffmpeg(tools: Path) -> None:
    """Bundle a self-contained arm64 ffmpeg.

    Set TTS_FFMPEG_PATH to a static/portable build. A stock Homebrew ffmpeg is
    rejected because it links a deep tree of /opt/homebrew dylibs that are
    absent on users' machines (the app would crash invoking ffmpeg).
    """
    override = os.environ.get("TTS_FFMPEG_PATH")
    ffmpeg = Path(override) if override else Path(shutil.which("ffmpeg") or "")
    if not ffmpeg.is_file():
        raise RuntimeError("ffmpeg not found (set TTS_FFMPEG_PATH or put it on PATH)")

    archs = run(["lipo", "-archs", str(ffmpeg)]).split()
    if "arm64" not in archs:
        raise RuntimeError(f"ffmpeg must be arm64, got {archs or ['unknown']}: {ffmpeg}")

    nonportable = _nonportable_ffmpeg_deps(ffmpeg)
    if nonportable:
        listing = "\n  ".join(nonportable)
        raise RuntimeError(
            f"ffmpeg ({ffmpeg}) links non-portable dylibs that won't exist on a "
            f"clean machine:\n  {listing}\n"
            "Provide a static/self-contained arm64 ffmpeg via TTS_FFMPEG_PATH "
            "(e.g. a static build from evermeet.cx or osxexperts.net)."
        )

    bundled_ffmpeg = tools / "ffmpeg"
    if bundled_ffmpeg.exists():
        bundled_ffmpeg.unlink()
    shutil.copy2(ffmpeg, bundled_ffmpeg)

def install_backend(app: Path) -> None:
    resources = app / "Contents/Resources"
    backend = resources / "Backend"
    backend.mkdir(parents=True, exist_ok=True)

    copy_tree(BACKEND_SOURCE / "core", backend / "core", "tests")
    copy_python_sources(str(resources))
    copy_tree(
        BACKEND_SOURCE / "URL-Reader",
        backend / "URL-Reader",
        "cache",
        "__pycache__",
        "temp_*.md",
        "usage_stats.jsonl",
    )
    copy_tree(BACKEND_SOURCE / "reference", backend / "reference")
    shutil.copy2(ROOT / "AppIcon.icns", resources / "AppIcon.icns")

    tools = resources / "Tools"
    tools.mkdir(exist_ok=True)
    bundle_ffmpeg(tools)

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def _safe_extractall(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract with path-traversal protection.

    Prefer tarfile's built-in 'data' filter (Python 3.12, backported to
    3.8.17/3.9.17/3.10.12/3.11.4+); fall back to manual member validation on
    older interpreters that lack the kwarg.
    """
    try:
        tar.extractall(path=dest, filter="data")
        return
    except TypeError:
        pass
    dest_root = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if dest_root != target and dest_root not in target.parents:
            raise RuntimeError(f"Unsafe path in archive: {member.name}")
    tar.extractall(path=dest)

def download_standalone_python(dest: Path):
    print(f"[Pack] Downloading Standalone Python {PYTHON_VERSION} for macOS aarch64...")
    tar_path = BUILD_DIR / "cpython.tar.gz"

    # Re-download if missing or if a stale/corrupt file fails the checksum.
    if tar_path.exists() and _sha256(tar_path) != PYTHON_RUNTIME_SHA256:
        tar_path.unlink()
    if not tar_path.exists():
        urllib.request.urlretrieve(PYTHON_RUNTIME_URL, tar_path)

    actual = _sha256(tar_path)
    if actual != PYTHON_RUNTIME_SHA256:
        tar_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Python runtime checksum mismatch:\n  expected {PYTHON_RUNTIME_SHA256}\n  got      {actual}"
        )

    print("[Pack] Extracting Python to AppBundle...")
    with tarfile.open(tar_path, "r:gz") as tar:
        _safe_extractall(tar, dest.parent)

    # python-build-standalone extracts to a folder named "python"
    extracted_python = dest.parent / "python"
    if dest.exists():
        shutil.rmtree(dest)
    extracted_python.rename(dest)

    # Strip test directories
    lib_path = dest / f"lib/python{PY_MINOR}"
    for item in ["test", "idlelib", "turtledemo"]:
        p = lib_path / item
        if p.exists():
            shutil.rmtree(p)

def create_python_runtime(app_path: str) -> None:
    resources_dir = Path(app_path) / "Contents/Resources"
    runtime_dest = resources_dir / "PythonRuntime"
    
    download_standalone_python(runtime_dest)
    prod_reqs = ROOT / "requirements.prod.lock"
    if not prod_reqs.exists():
        print("[Pack] Generating requirements.prod.lock...")
        txt_reqs = ROOT / "requirements.prod.txt"
        with open(txt_reqs, "w") as f:
            f.write("""huggingface_hub>=1.0\nminiaudio>=1.61\nmlx-lm>=0.31.1\nmlx>=0.31.1\nnumpy>=1.26.4\nscipy>=1.10.0\nsounddevice>=0.5.3\ntqdm>=4.67.1\ntransformers>=5.5.0\nfastapi>=0.95.0\nuvicorn[standard]>=0.22.0\npython-multipart>=0.0.22\npydantic>=2.0.0\nrequests\npsutil\n""")
        subprocess.run(["uv", "pip", "compile", str(txt_reqs), "-o", str(prod_reqs)], check=True)

    print("[Pack] Installing production dependencies with uv...")
    python_bin = runtime_dest / "bin/python3"
    subprocess.run(["uv", "pip", "install", "-r", str(prod_reqs), "--python", str(python_bin)], check=True)
    
    site_packages = runtime_dest / f"lib/python{PY_MINOR}/site-packages"
    for cache in site_packages.rglob("__pycache__"):
        if cache.is_dir():
            shutil.rmtree(cache)

def update_plist(app: Path) -> None:
    plist_path = app / "Contents/Info.plist"
    with plist_path.open("rb") as handle:
        info = plistlib.load(handle)
    info.update(
        {
            "CFBundleDisplayName": "QwenTTS",
            "CFBundleIconFile": "AppIcon",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1",
            "LSMinimumSystemVersion": "14.0",
        }
    )
    with plist_path.open("wb") as handle:
        plistlib.dump(info, handle)

def clean_generated_caches(app: Path) -> None:
    for cache in app.rglob("__pycache__"):
        if cache.is_dir():
            shutil.rmtree(cache)
    for bytecode in app.rglob("*.py[co]"):
        bytecode.unlink()

def is_macho(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with path.open("rb") as handle:
            magic = handle.read(4)
    except OSError:
        return False
    return magic in {
        b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
    }

def sign_app(app: Path, identity: str) -> None:
    if not ENTITLEMENTS.is_file():
        raise RuntimeError(f"Missing Hardened Runtime entitlements: {ENTITLEMENTS}")
    sign_args = ["codesign", "--force", "--sign", identity, "--entitlements", str(ENTITLEMENTS)]
    if identity != "-":
        sign_args += ["--options", "runtime", "--timestamp"]

    nested = sorted(
        (path for path in app.rglob("*") if is_macho(path)),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in nested:
        run(sign_args + [str(path)])
    # Sign the outer bundle WITHOUT --deep: nested Mach-O are already signed
    # inner-to-outer above, and `--deep` is deprecated by Apple for distribution
    # (it re-signs inconsistently and can break the seal on an embedded runtime).
    run(sign_args + [str(app)])
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])

def get_dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--signing-identity",
        default=os.environ.get("TTS_SIGNING_IDENTITY", "-"),
        help="Developer ID Application identity; '-' creates a local ad-hoc build",
    )
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    source_app = build_native_app()
    DIST_DIR.mkdir(exist_ok=True)
    app = DIST_DIR / "QwenTTS.app"
    if app.exists():
        shutil.rmtree(app)
    shutil.copytree(source_app, app, symlinks=False)

    install_backend(app)
    create_python_runtime(str(app))
    update_plist(app)
    clean_generated_caches(app)
    
    print("[Pack] Re-signing QwenTTS.app bundle...")
    run(["xattr", "-cr", str(app)])
    sign_app(app, args.signing_identity)
    
    app_size: int = get_dir_size(app)
    print(f"[Pack] QwenTTS.app package size: {app_size / (1024 * 1024):.2f} MB")
    print(f"[Pack] Success! Release app created at: {app}")

if __name__ == "__main__":
    main()
