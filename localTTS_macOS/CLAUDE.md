# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**QwenTTS** is a native macOS TTS (Text-to-Speech) application built with:
- **Frontend**: AppKit (Swift) — native macOS UI with status bar, main window, and settings
- **Backend**: FastAPI (Python) — runs as an independent process managed by the app
- **Inference**: MLX-Audio + Qwen3-TTS (MLX-LM) — on-device ML inference for Apple Silicon
- **Architecture**: Separate process groups with watchdog-based lifecycle management, multiprocessing workers for inference/podcasts, and FastAPI for async services

**Deployment target**: macOS 14.0+, Apple Silicon only  
**Current development stage**: Weeks 1-3 milestone complete; ongoing AppKit UI and process supervision features

### Repository Layout & Git

This project lives in `localTTS_macOS/`, but the **git root is the parent `TTS/` directory**. Git operations (`status`, `commit`, `log`) span the whole monorepo, which also contains sibling top-level projects:

- `localTTS_macOS/` — **the active project** (AppKit app + `backend/`). All work here.
- `QwenTTS-App/` — legacy `rumps`-based Python app being replaced. **Separation constraint: do NOT import or sync code between `localTTS_macOS/backend/` and `QwenTTS-App/`.** They are independent.
- `mlx_audio/`, `URL-Reader/`, `reference/`, `qwen-tts-extension/` — upstream sources; `localTTS_macOS/backend/` bundles its own pinned snapshots of `mlx_audio/`, `URL-Reader/`, and `reference/`. Edit the copy under `backend/`, not the parent-level one, when changing backend behavior.

When committing, scope changes to `localTTS_macOS/` paths unless intentionally touching a sibling project.

---

## Build & Development Commands

### Building the macOS app

```bash
# Generate Xcode project (requires XcodeGen)
cd QwenTTS
xcodegen generate

# Build using Xcode command line
xcodebuild -project QwenTTS.xcodeproj \
  -scheme QwenTTS \
  -configuration Release \
  -derivedDataPath build/DerivedData \
  clean build

# Build without code signing (development)
xcodebuild ... CODE_SIGNING_ALLOWED=NO ...
```

### Running backend tests

```bash
# Run all tests (uses pytest)
cd backend
python -m pytest core/tests/ -v

# Run a specific test file
python -m pytest core/tests/test_services_smoke.py -v

# Run tests matching a pattern
python -m pytest core/tests/ -k "test_podcast" -v
```

### Running the backend (standalone development)

```bash
# With conda gemini environment (current dev)
source activate gemini
cd backend
python core/backend.py

# Or from root with environment setup
export PYTHONPATH=/Users/funanhe/00_MyCode/TTS/localTTS_macOS/backend:$PYTHONPATH
python backend/core/backend.py
```

### Packaging & release

```bash
# Full release package (builds app, runtime, and creates DMG)
python package_release.py

# Diagnostics check on an app bundle
python run_diagnostics.py dist/QwenTTS.app
```

> The standalone Python runtime is built by `package_release.py` (`create_python_runtime`,
> via python-build-standalone). The old `build_runtime.py` (a non-relocatable `uv venv`)
> was removed — do not reintroduce a venv-based runtime for packaging.

---

## Architecture

### Directory Structure

```
localTTS_macOS/
├── QwenTTS/
│   ├── QwenTTS.xcodeproj
│   └── QwenTTS/
│       ├── Application/          # AppDelegate, ApplicationCoordinator
│       ├── Backend/              # BackendProcessManager, APIClient
│       ├── StatusBar/            # NSStatusItem, NSPopover
│       ├── Windows/              # Main window, Settings, Dialogs
│       ├── Models/               # ModelManager
│       ├── State/                # AppStateStore
│       └── Updates/              # UpdateManager (future)
│
├── backend/
│   ├── core/
│   │   ├── backend.py            # FastAPI app, routes
│   │   ├── api_models.py         # Request/response models
│   │   ├── tts_engine.py         # MLX inference wrapper (live inference runs via inference_worker in backend.py)
│   │   ├── player.py             # PortAudio playback
│   │   ├── processor.py          # Text processing
│   │   ├── storage.py            # Saved items DB
│   │   ├── paths.py              # Runtime path resolution
│   │   ├── services/
│   │   │   ├── runtime_supervisor.py    # Process/thread lifecycle
│   │   │   ├── playback_service.py      # Playback state machine
│   │   │   ├── podcast_service.py       # Podcast generation
│   │   │   ├── saved_items_service.py   # Saved content DB
│   │   │   ├── cache_service.py         # Audio cache
│   │   │   ├── url_jobs.py              # URL task tracking
│   │   │   ├── performance.py           # Thermal profiles
│   │   │   └── runtime_log.py           # Event logging
│   │   ├── state/
│   │   │   └── runtime_state.py         # Snapshot container
│   │   └── tests/
│   │       ├── test_services_smoke.py   # Service integration tests
│   │       ├── test_runtime_supervisor.py
│   │       └── test_watchdog_token.py
│   │
│   ├── mlx_audio/                 # MLX-Audio source snapshot
│   ├── URL-Reader/                # URL processing pipeline
│   └── reference/                 # Bundled reference audio
│
├── docs/
│   ├── APPKIT_DEVELOPMENT_PLAN.md # Master development roadmap
│   ├── DEVELOPMENT_ROADMAP.md
│   └── COMPLETED_WORK.md
│
├── package_release.py             # Full release packaging
└── requirements.prod.txt          # Production dependencies
```

### Layers & Boundaries

**Layer 1: AppKit (macOS app)**
- Manages app lifecycle, menu bar, windows, settings UI
- Does NOT do MLX inference or manage multiprocessing (except launching backend)
- Launches the Python backend as an independent process group
- Communicates via HTTP (`localhost:8001`)
- Uses watchdog pipe to detect backend crash and clean up

**Layer 2: FastAPI Backend**
- Runs as an independent process, unrelated to AppKit's lifecycle
- Manages all Python sub-processes: inference workers, podcast workers, URL tasks
- `RuntimeSupervisor` centralizes shutdown: workers → threads → queues → cleanup
- Exports REST API for AppKit and Chrome extension
- Binds to `127.0.0.1:8001` by default; LAN mode disabled by default

**Layer 3: Worker Processes & Services**
- Multiprocessing workers for TTS inference (MLX on GPU/NPU)
- Multiprocessing workers for podcast rendering
- Background threads: audio playback (PortAudio), device monitoring, caching
- All async-friendly: asyncio tasks for URL reading, SSE streaming

**Separation constraint**: Do NOT import or sync code between `backend/core/` and `QwenTTS-App/` (legacy Python app). The two are independent.

### Process Lifecycle & Safety

1. **Launch**:
   - AppKit spawns backend via `posix_spawn` with independent process group
   - Passes management token via `TTS_MANAGEMENT_TOKEN` env var
   - Holds write end of watchdog pipe; passes read FD to backend
   - Polls `/health` until backend is ready

2. **Running**:
   - Backend's `RuntimeSupervisor` owns all workers/threads
   - If AppKit crashes, watchdog pipe closes → backend sees EOF → graceful shutdown
   - If backend crashes, AppKit detects via heartbeat and shows error

3. **Shutdown**:
   - AppKit calls `POST /control/shutdown` (with management token)
   - Backend: stop accepting tasks → stop playback → set shutdown event → join workers → cleanup queues
   - AppKit then forcibly kills process group if shutdown timeout expires (SIGTERM → SIGKILL)

### API Security

- **Management token** (`X-Management-Token` header): Random UUID per backend launch, required for `/control/*` and `/settings` endpoints. Only AppKit has it.
- **Extension pairing token** (user-provided): Stored in config, required by Chrome extension for any state-changing requests.
- Default: bind localhost only, CORS/debug APIs disabled in release builds.

### Runtime Paths

All paths resolved once at startup via environment variables (set by AppKit):

```
TTS_APP_SUPPORT_PATH      ~/Library/Application Support/QwenTTS
TTS_DATA_PATH             {APP_SUPPORT}/Data
TTS_CACHE_PATH            {APP_SUPPORT}/Cache
TTS_PODCASTS_PATH         {APP_SUPPORT}/Podcasts
TTS_MODELS_PATH           {APP_SUPPORT}/Models
TTS_REFERENCE_PATH        QwenTTS.app/Contents/Resources/ReferenceAudio
MLX_AUDIO_PATH            QwenTTS.app/Contents/Resources/MLXAudio
TTS_FFMPEG_PATH           QwenTTS.app/Contents/Resources/Tools/ffmpeg
```

Do NOT hardcode paths or infer them from repo depth. This allows the app to run from any install location.

---

## Key Features & Implementation Notes

### Python Backend (in `backend/core/`)

**FastAPI routes** (in `backend.py`):
- `GET /health` — instance ID, PID, status. On startup the backend also writes `~/Library/Application Support/QwenTTS/runtime.json` (port/pid/instance_id) for dynamic-port discovery; removed on shutdown.
- `GET /snapshot` — full runtime state for UI sync (incl. `current_article_chunks`/`current_article_index` that drive the Console karaoke scroll)
- `GET /settings` · `PATCH /settings` — config
- `POST /read` — read text; **`mode` field** (original/translate/podcast-discuss/podcast-trans): non-original is processed via the engine layer (`reader_service.process_with_llm`) before TTS
- `POST /seek` · `POST /stop` · `POST /pause` · `POST /resume` — playback control
- `POST /read_url` — URL processing (requires non-empty URL; fetch → process by mode → read/save/podcast)
- `GET/PATCH /engines` · `POST /engines/check` — provider-agnostic engine config (keys/models/order/target_lang) + connectivity probe. Management-token protected.
- `GET /podcasts/transcript?filename=` — reads the `.txt` script sidecar saved next to a generated podcast wav
- `POST /control/shutdown` — graceful exit

**Provider-agnostic engine layer** (in `backend/URL-Reader/`, added 2026-06):
- `engine_config.py` — reads `engines` section of `config.json`; keys **only** from config (fully decoupled from `.env`).
- `translation_engine.py` — machine translation for `translate` mode: Google (free, no key) / Microsoft / DeepL, with `LANG_MAP` for target language. **Does NOT use LLM.**
- `llm_engine.py` — `call_llm()` for `podcast-discuss`/`podcast-trans`: Gemini (inline `google-genai`) / Claude / OpenAI / DeepSeek / local MLX. One model per provider (`engines.llm.models`, rolling-alias defaults); `selected` first + cross-provider fallback. Each provider has `probe_provider()` for `/engines/check`.
- `gemini_engine.py` is now **orphaned** (GeminiProvider is inline); safe to delete.
- `reader_service.process_with_llm(text, mode)` is the dispatch entry; `process_url_job` handles YouTube transcript / HTML fetch → markdown → process.

**Services** (in `services/`):
- `RuntimeSupervisor`: Centralizes worker/thread lifecycle. Call `.shutdown(timeout=X)` for graceful exit with fallback to SIGKILL.
- `PlaybackService`: State machine for playback (IDLE → GENERATING → PLAYING → PAUSED). Auto-resumes after pause timeout (configurable).
- `PodcastService`: Manages podcast generation via multiprocessing workers. Tracks jobs in `podcast_jobs.json`.
- `SavedItemsService`: Persistent "read later" list in SQLite.
- `CacheService`: Manages cache limit (keeps N most recent audio files).
- `UrlJobStore`: Tracks long-running URL tasks (read/translate/save).
- `RuntimeEventLog`: Append-only JSON log for diagnostics.

**Performance profiles** (in `performance.py`, `PERFORMANCE_PROFILES`):
- `fast`: Higher power / lowest latency (small sleeps, large buffers, full model)
- `balanced`: Default, reasonable latency and power
- `quiet`: Low power / minimal thermal load (large sleeps, small buffers, smaller `Qwen3-TTS-0.6B` model)

Each profile defines `chunk_sleep`, `sentence_sleep`, `buffer_high_sec`, `buffer_low_sec`,
`podcast_pause_poll_sec`, and `model`. The profile names `fast`/`balanced`/`quiet` are the
single source of truth — used verbatim by `processor.smart_split`, `podcast_service`, and the
AppKit Settings picker (`SettingsViewController`). `get_performance_profile` falls back to
`balanced` for any unknown name. (Do not rename a key without updating all three callers.)

### AppKit Frontend (in `QwenTTS/`)

**Key classes**:
- `ApplicationCoordinator`: Boots backend, coordinates all app services
- `BackendProcessManager`: Owns backend process group, handles launch/shutdown/restart with state machine
- `BackendAPIClient`: HTTP client to backend, handles auth token
- `AppStateStore`: Central app state (mirrors backend snapshot + UI state)
- `StatusItemController`: Menu bar icon and menu
- `PlaybackPopoverController`: Quick playback controls and status display
- `ModelManager`: Download, verify, and manage Qwen TTS models in Application Support
- `MainWindowController`: Tab UI with Console, Saved Items, Podcasts, URL Reader, Cache, Settings

**UI considerations**:
- All windows use glassmorphism (NSVisualEffectView) for modern macOS look
- Console view implements Karaoke-style lyric scrolling with gradient fade
- Settings stored in `AppStateStore`, persisted to `settings.json` in Application Support

---

## Testing

Tests use **pytest**. Run from `backend/` directory:

```bash
python -m pytest core/tests/test_services_smoke.py -v
```

**Test categories**:
- `test_services_smoke.py`: Integration smoke tests (state, storage, podcast, playback)
- `test_runtime_supervisor.py`: Lifecycle tests (workers, threads, cleanup)
- `test_watchdog_token.py`: Management token and lifecycle tests
- `test_week3.py`: Path resolution and API tests

**Current limitation**: Tests use mock/stub inference (no real MLX models). Full model lifecycle tested separately in CI.

---

## Common Development Tasks

### Adding a new backend API endpoint

1. Define request/response models in `core/api_models.py` (Pydantic)
2. Add route to `core/backend.py` (FastAPI)
3. Check auth token if state-changing: `check_management_token(request.headers)`
4. Call appropriate service method
5. Add test in `core/tests/test_*.py`

### Adding a new service

1. Create new file in `core/services/`
2. If it has workers: inherit shutdown logic from `RuntimeSupervisor`
3. If it accesses files: use paths from `paths.runtime_paths`
4. Register with `RuntimeSupervisor` if it has background threads/processes
5. Document in this file and in docstrings

### Changing AppKit UI

1. Edit appropriate controller in `QwenTTS/QwenTTS/Windows/` or `StatusBar/`
2. Avoid layout hardcoding; use constraints or layout guides
3. All UI updates must be on main thread (use `DispatchQueue.main.async`)
4. For data binding, update `AppStateStore` → AppKit observes via KVO or polling

### Updating dependencies

**Python**: Edit `requirements.prod.txt`, then lock:
```bash
pip install uv
uv pip compile requirements.prod.txt -o requirements.prod.lock
```

**Swift**: Edit `QwenTTS/project.yml` (if using XcodeGen) or Xcode directly

---

## Useful References

- **Development roadmap**: `docs/APPKIT_DEVELOPMENT_PLAN.md` — 10-week plan with milestones
- **Completed work**: `docs/COMPLETED_WORK.md` — recent UI and packaging work
- **Backend README**: `backend/README.md` — overview of backend source structure
- **Recent commits**: Check `git log` for implementation context (e.g., "Allow podcast generation after long playback pause")

---

## Environment & Prerequisites

- **Xcode 15+** (for native build) or `xcodebuild` command-line tools
- **Python 3.11+** (current: Conda `gemini` env with Python 3.11; will migrate to 3.12 for release)
- **macOS 14+** (for development and deployment)
- **Apple Silicon Mac** (currently ARM64 only)
- **FFmpeg** (bundled in release; available via `brew` for dev)

Current dev environment: `/Users/funanhe/miniconda3/envs/gemini/bin/python`

---

## Notes for Future Work

1. **Milestone M4 (Week 4)**: AppKit framework foundation and process supervision. Focus on reliable process group management and watchdog pipe before adding UI features.

2. **Milestone M5 (Week 5)**: AppKit MVP — replace `rumps app.py` dependency. Core: clipboard read, playback controls, settings, error recovery.

3. **Release readiness**: Before shipping, verify no orphan processes after crash, no user data loss on restart, all nested binaries signed, and Chrome extension pairing works.

4. **Known limitations**:
   - No i386/Intel support planned
   - Model weights (~5.2 GB) not bundled; users download on first run
   - Podcasts stored in Application Support; no cloud sync

5. **Potential issues**:
   - Python subprocess cleanup on app crash — rely on watchdog pipe + process group cleanup
   - CoreAudio device switching during playback — handled by event-driven monitoring
   - Thermal throttling under sustained inference — use performance profiles

6. **Latest session log + open items**: see `docs/COMPLETED_WORK.md` §5 (2026-06-21) for the multi-provider engine layer, AI engine config page, content hub wiring, and the full **待测试/待办** list (§5.7). Highlights still untested/unbuilt: podcast `.txt` transcript final verify, URL-job error surfacing in UI, local MLX provider, Claude/OpenAI/DeepSeek real keys, English Serena voice, `package_release.py` under the new deps. To run AI summary/dual-podcast you MUST set an LLM key in the "AI 引擎" page (no `.env` fallback).

---

## Quick Links

- **Git status**: Use `git status` to check uncommitted changes before committing
- **Recent work**: Check `git log --oneline | head -20` for latest commits and context
- **Tests**: Run `pytest core/tests/ -v` to verify backend changes
- **App build**: Use `xcodebuild` command shown above; Xcode IDE also supported
