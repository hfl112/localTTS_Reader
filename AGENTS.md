# TTS — Agent Guidelines

## Repo layout

- `mlx_audio/` — upstream MLX-Audio v0.4.3 fork; all Python tooling lives here (`pyproject.toml`, `pytest.ini`, `.pre-commit-config.yaml`)
- `QwenTTS-App/` — macOS menu-bar App, the primary deliverable
- `qwen-tts-extension/` — Chrome extension (WXT+TypeScript), HTTP client of QwenTTS-App
- `URL-Reader/` — URL extractor & cleanser CLI tool
- `docs/` — global documentation (e.g. development history)
- `podcasts/` — directory to store generated podcast WAV files
- `archive/` — archived legacy experiments (e.g. 02_txt2speech, whisper, QwenTTS-MacOS)
- `01_ref/` — reference materials / notes
- `qwen_reader.py`, `verify_features.py` — ad-hoc root-level scripts, not part of the build

## Environment

- Python 3.12 (`.python-version` at root), `.venv/` at root
- Apple Silicon only (MLX framework); no CUDA/ROCm
- `ffmpeg` required on `PATH`
- Source code, comments, and internal docs are **largely in Chinese**

## mlx_audio/ — dev commands

Run from `mlx_audio/`.

```bash
black .                          # line-length=88
isort .                          # profile=black
pre-commit run --all-files       # Black + isort

pytest -s tests/                          # core tests
pytest -s mlx_audio/tts/tests/            # modular (also stt/sts/vad/codec/lid)

python -m mlx_audio.tts.generate          # TTS CLI
python -m mlx_audio.stt.generate          # STT CLI
python -m mlx_audio.server                # Web UI + API (port 8000)
```

## QwenTTS-App — startup invariants (read before editing)

- Launch from `QwenTTS-App/` with `python app.py`. `app.py` spawns `core/backend.py` on port 8001 as a subprocess and pings `/status` at 1Hz.
- `backend.py` resolves the model via a RELATIVE path `models/{name}` rooted at `mlx_audio/`, and sets `mlx_audio_path="../../mlx_audio"` rooted at `QwenTTS-App/`. **Do not move `QwenTTS-App/` to a different depth** — both relative paths will break (unless overridden by environment variables `MLX_AUDIO_PATH` and `TTS_WORKSPACE_PATH` which decouple these paths).
- Model weights must exist at `mlx_audio/models/{Qwen3-TTS-1.7B-8bit, Qwen3-TTS-0.6B}`.
- `backend.py` calls `mp.set_start_method("spawn", force=True)` in the FastAPI lifespan. On macOS MLX/Metal is spawn-only; do not switch to fork.
- **Voice Stability / Double Lock / ICL**: Both 0.6B and 1.7B models suffer from severe random drift in voice and gender if not properly constrained. To fix this, you must apply the following techniques:
  1. **Seed Lock**: Always enforce a fixed seed (e.g. `seed=42`) in `generate_stream` kwargs to stop codebook stochasticity.
  2. **Semantic Anchor (Instruct) Lock**: The `instruct` string heavily influences the generated voice. If switching voices (e.g. "Serena" to "Ryan"), the `instruct` MUST reflect the gender and style (e.g., `"A professional male anchor."` for Ryan).
  3. **In-Context Learning (ICL) Lock (In-Context Clone)**: For production-grade voice locking, zero-shot is insufficient. We inject `ref_audio` and `ref_text` parameters to the model generation options. 
     - **Serena Reference**: Audio at `reference/bbc_news.wav`, Text: `"This is the research headquarters for one of the oldest companies in tech, IBM."`
     - **Ryan Reference**: Audio at `reference/ref_ryan.wav`, Text: `"各位听众大家好，欢迎收听本期的新闻快报，我是男主持瑞恩。"`
  4. **Dialogue Parser for Multi-Speaker Podcasts**: The `TextProcessor` provides `parse_dialogue_or_text(text)` which parses turn-based dialogues tagged with `[Serena]:` or `[Ryan]:` (case-insensitive, optional brackets and colons). It outputs a list of chunks where dialogue sentences are automatically annotated with their respective speaker name, instruct prompt, and ICL references (`ref_audio` and `ref_text`). Ordinary paragraphs fall back to standard single-speaker text generation.
  *(Note: For 0.6B Base model specifically, it requires the literal prefix `"Persona Anchor: {voice}. "` injected into `instruct` to hold the voice, see `backend.py`.)*

## QwenTTS-App — architecture

| File | Role |
|---|---|
| `app.py` | `rumps` UI; spawns backend on :8001; 1Hz `/status` poll; menu items trigger `/read`/`/stop`/`/seek`/`/pause` |
| `core/api_models.py` | Pydantic request models for FastAPI endpoints; keep route input defaults and compatibility here |
| `core/backend.py` | FastAPI route layer on :8001; spawns inference worker as `mp.Process`; owns audio feeder, Bonjour (`_qwentts._tcp`), lifespan wiring, and 10min idle model unload |
| `core/state/runtime_state.py` | Main runtime state container for current title/progress, current podcast/md5, podcast buffer, and last activity |
| `core/services/playback_service.py` | `PlaybackController` + `PlaybackService`; playback session invalidation, `current_task_id` bumping, stale queue cleanup, TTS playback, WAV playback |
| `core/services/podcast_service.py` | Background podcast generation processes, pause manager, GPU lock, chunk checkpoints, and podcast file list/delete/pin/clear |
| `core/services/podcast_jobs.py` | File-backed `podcast_jobs.json` store for queued/running/done/failed/canceled podcast jobs |
| `core/services/runtime_log.py` | Append-only `runtime_events.jsonl` structured event log for playback, URL, podcast, and error diagnostics |
| `core/services/url_jobs.py` | File-backed `url_jobs.json` store for URL fetch/parse/Gemini/dispatch job status |
| `core/services/performance.py` | `fast`/`balanced`/`quiet` profiles plus reading-time estimation |
| `core/services/saved_items_service.py` | Saved-for-later JSON queue backed by `data/saved_for_later.json` |
| `core/services/cache_service.py` | Cache metadata/list/play/export/delete/clear helpers for `data/cache/*.npy` and exported WAVs |
| `core/tts_engine.py` | MLX inference wrapper; **24kHz stereo float32** native output; dynamic timeout `max(30, min(len*1, 120))s`; streams with `streaming_interval=0.5`, `response_format="pcm"` |
| `core/player.py` | `sounddevice.OutputStream` 24kHz/2ch/float32/blocksize=8192; zero-copy in-memory callback |
| `core/processor.py` | `smart_split`: Chinese ≤250 chars, English ≤600; strips Obsidian-flavored markdown (YAML frontmatter, `![[wikilinks]]`, headings, lists) |
| `core/storage.py` | JSON config + state (breakpoint resume) |
| `core/worker.py` | Standalone CLI (`python core/worker.py --text "..."`). **Not used by `app.py` / `backend.py`**. |

**IPC**: `mp.Queue` for text/audio, `mp.Event` for stop, `mp.Value` for status (IDLE/BUSY/COOLING).
**Playback controller**: `PlaybackService` owns `PlaybackController` plus `S.current_task_id` to invalidate stale TTS and WAV playback threads. Any new playback entrypoint must go through `playback_service.start_new_session()` or `stop_current_session()`, then only feed audio while `playback_service.controller.can_feed_audio(session_id, task_id)` remains true.
**API request schemas**: Define new endpoint request bodies in `core/api_models.py` with Pydantic models. Avoid adding new loose `dict = Body(...)` parsing in `backend.py`.
**URL input pipeline**: `/read_url` calls `URL-Reader/reader_service.py` directly from a backend async task. `read_url_cli.py` is only a thin manual CLI wrapper; do not reintroduce per-request CLI subprocess dispatch.
**Performance profiles**: `fast`, `balanced`, and `quiet` live in `core/services/performance.py`. Realtime reading defaults to `balanced`; podcast generation defaults to `quiet`; long single podcasts and all batch podcasts should prefer `Qwen3-TTS-0.6B`.
**Audio cache**: 10 `.npy` files in `QwenTTS-App/data/cache/`, MD5-keyed, LRU by mtime.
**Sentinel**: string `"PIPELINE_END_STRICT_V1"` shared by inference worker and player (must remain a `str` to survive `mp.Queue` pickling).
**Cruise mode**: realtime inference uses profile-specific buffer high/low watermarks (`balanced`: 20s/8s, `quiet`: 10s/4s, `fast`: 30s/12s) to cool the GPU.
**Runtime files** under `QwenTTS-App/data/`: `config.json`, `state.json`, `cache/*.npy`, `podcast_chunks/*/chunk_*.npy`, `saved_for_later.json` (max 5 items), `podcast_jobs.json`, `url_jobs.json`, `runtime_events.jsonl`. Finished podcast WAVs live in the repo-level `podcasts/` directory.

## Default TTS config

Defined in `QwenTTS-App/data/config.json` and `core/tts_engine.py` defaults:

```
model: Qwen3-TTS-1.7B-8bit        (alt: Qwen3-TTS-0.6B)
voice: Serena                      (alts: Ryan, Vivian)
instruct: "Professional female anchor, steady and clear."
temperature: 0.2  top_p: 0.5  top_k: 10  seed: 42  repetition_penalty: 1.1
lang_code: zh  speed: 1.0
performance_profile: balanced           (alts: fast, quiet; podcast defaults to quiet)
```

## Endpoints worth knowing

Standard playback endpoints (`/read`, `/status`, `/stop`, `/pause`, `/resume`, `/seek`, `/restart_audio`) are obvious from the menu callbacks in `app.py`. The non-obvious ones:

- `POST /save_for_later` / `GET /saved_items` / `POST /play_saved` / `POST /delete_saved` / `POST /saved_items/clear` — saved-items queue backed by `data/saved_for_later.json` (max 5, FIFO).
- `POST /read_url` / `GET /url_jobs` — URL input pipeline backed by `URL-Reader/reader_service.py`, `URL-Reader/cache/`, and `data/url_jobs.json`.
- `POST /generate_single_podcast` — starts one background podcast process for a single text item → repo-level `podcasts/podcast_单篇_{source}_{title}_{hash}_{ts}.wav` (24kHz int16).
- `POST /generate_podcast` — concatenates all saved items → repo-level `podcasts/podcast_合集_web_大合集播客_{ts}.wav` (24kHz int16).
- `GET /podcasts/list` / `GET /podcasts/jobs` / `POST /podcasts/play` / `POST /podcasts/delete` / `POST /podcasts/toggle_pin` / `POST /podcasts/clear` — finished podcast file and job operations owned by `PodcastService`.
- `GET /cache/items` / `POST /cache/play` / `POST /cache/export` / `POST /cache/delete` / `POST /cache/clear` — temp cache operations owned by `CacheService`.
- `GET /debug/state` — local diagnostics for playback session id, task id, queues, stop event, current title, active URL tasks, active podcast worker count, and recent podcast jobs.
- `GET /debug/events?limit=50` — recent structured runtime events from `data/runtime_events.jsonl`.

## Constraints

- `transformers>=5.5.0`, `mlx>=0.31.1`, `mlx-lm>=0.31.1`, `miniaudio>=1.61` pinned
- `setuptools<81` pinned (webrtcvad requires `pkg_resources`, removed in setuptools 81)
- `TRANSFORMERS_NO_ADVISORY_WARNINGS=1` set in `mlx_audio/mlx_audio/__init__.py`
- CORS open (`*`) for LAN clients (Chrome extension on the same machine)
- Bonjour registers `_qwentts._tcp.local.:8001` on backend startup (via `zeroconf`); if registration fails, manual `http://<host>:8001` still works

## Testing

- `pytest.ini` sets `asyncio_mode = auto`, `asyncio_default_fixture_loop_scope = function`
- No mypy, no ruff — only Black + isort
- CI order: `pre-commit run --all-files` → core tests → modular tests
- Core tests under `mlx_audio/tests/` may require model weights on disk
- QwenTTS-App service smoke tests: `python -m pytest -q QwenTTS-App/core/tests/test_services_smoke.py` (covers service basics, API model defaults, podcast/url job stores, runtime event log, reader helpers, and playback session invalidation)
