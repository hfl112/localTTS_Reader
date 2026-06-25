"""Submit the created DMG for Apple Notarization and staple the ticket."""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DMG = ROOT / "dist/QwenTTS.dmg"

def run(args: list[str]) -> None:
    print("[Exec]", " ".join(args))
    subprocess.run(args, check=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keychain-profile", required=True, help="The notarytool keychain profile name")
    args = parser.parse_args()

    if not DMG.exists():
        sys.exit(f"Missing {DMG}; run make_dmg.py first")

    print("[Notarize] Submitting DMG to Apple Notary Service...")
    run([
        "xcrun", "notarytool", "submit",
        str(DMG),
        "--keychain-profile", args.keychain_profile,
        "--wait"
    ])

    print("[Notarize] Stapling ticket to DMG...")
    run(["xcrun", "stapler", "staple", str(DMG)])
    
    print("[Notarize] Success! The DMG is now notarized and stapled.")

if __name__ == "__main__":
    main()
