# Repository Guidelines

## Project Structure & Module Organization

This directory is the active native macOS project inside the larger TTS monorepo. Keep native work scoped here unless the user explicitly approves sibling changes.

- `QwenTTS/` contains the AppKit app and Xcode project. Swift source lives in `QwenTTS/QwenTTS/`, grouped by `Application/`, `Backend/`, `StatusBar/`, `Windows/`, `UI/`, `Models/`, and `State/`.
- `backend/` is the independent Python backend snapshot used by the native app. Modify this copy only; do not edit or sync from sibling `QwenTTS-App/`, root `URL-Reader/`, or root `mlx_audio/`.
- `backend/core/tests/` contains pytest coverage for backend services, lifecycle, and watchdog behavior.
- `docs/` stores development plans and completed-work notes.
- `package_release.py`, `make_dmg.py`, `notarize_dmg.py`, and `run_diagnostics.py` support release packaging and verification.

## Build, Test, and Development Commands

Run macOS app commands from `QwenTTS/`:

```bash
xcodegen generate
xcodebuild -project QwenTTS.xcodeproj -scheme QwenTTS -configuration Release -derivedDataPath build/DerivedData clean build
xcodebuild -project QwenTTS.xcodeproj -scheme QwenTTS CODE_SIGNING_ALLOWED=NO build
```

Run backend commands from `backend/`:

```bash
python core/backend.py
python -m pytest core/tests/ -v
python -m pytest core/tests/test_services_smoke.py -v
```

Release checks from repo root:

```bash
python package_release.py
python run_diagnostics.py dist/QwenTTS.app
```

## Coding Style & Naming Conventions

Swift uses standard AppKit conventions: `UpperCamelCase` types, `lowerCamelCase` methods/properties, and controller suffixes such as `SettingsViewController`. Keep UI code on the main actor where state or AppKit APIs require it. Python backend code should follow Black/isort-compatible formatting, type-oriented service boundaries, and existing Pydantic request models in `core/api_models.py`.

## Testing Guidelines

Use pytest for backend changes. Name tests `test_*.py` and prefer focused service/lifecycle tests under `backend/core/tests/`. For Swift changes, build with `xcodebuild` or verify in Xcode. If full Xcode is unavailable, state that clearly in the handoff.

## Commit & Pull Request Guidelines

Recent commits use short imperative subjects, for example `Add runtime events and podcast job tracking`. Keep commits scoped to `localTTS_macOS/` paths when possible. PRs should include a concise behavior summary, test/build commands run, screenshots for visible UI changes, and any packaging or migration impact.

## Agent-Specific Guardrails

Do not modify the stable legacy app in sibling `QwenTTS-App/` or its `core/` files. Native backend resources are owned by `localTTS_macOS/backend/`; model weights may be referenced externally as read-only data.
