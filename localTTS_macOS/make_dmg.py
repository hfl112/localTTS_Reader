"""Create a conventional drag-to-Applications DMG from a verified app."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
APP = DIST / "QwenTTS.app"
DMG = DIST / "QwenTTS.dmg"


def run(args: list[str]) -> None:
    print("[Exec]", " ".join(args))
    subprocess.run(args, check=True)


def main() -> None:
    if not APP.is_dir():
        raise SystemExit(f"Missing {APP}; run package_release.py first")
    run(["codesign", "--verify", "--deep", "--strict", str(APP)])
    if DMG.exists():
        DMG.unlink()

    with tempfile.TemporaryDirectory(prefix="qwentts-dmg-") as temporary:
        staging = Path(temporary) / "QwenTTS"
        staging.mkdir()
        shutil.copytree(APP, staging / APP.name, symlinks=True)
        (staging / "Applications").symlink_to("/Applications")
        run(
            [
                "hdiutil",
                "create",
                "-volname",
                "QwenTTS",
                "-srcfolder",
                str(staging),
                "-format",
                "UDZO",
                "-ov",
                str(DMG),
            ]
        )
    print(f"[DMG] Created {DMG}")


if __name__ == "__main__":
    main()
