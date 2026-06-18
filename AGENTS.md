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
| `core/backend.py` | FastAPI on :8001; spawns inference worker as `mp.Process`; Bonjour (`_qwentts._tcp`); auto VRAM unload after 10min idle |
| `core/tts_engine.py` | MLX inference wrapper; **24kHz stereo float32** native output; dynamic timeout `max(30, min(len*1, 120))s`; streams with `streaming_interval=0.5`, `response_format="pcm"` |
| `core/player.py` | `sounddevice.OutputStream` 24kHz/2ch/float32/blocksize=8192; zero-copy in-memory callback |
| `core/processor.py` | `smart_split`: Chinese ≤250 chars, English ≤600; strips Obsidian-flavored markdown (YAML frontmatter, `![[wikilinks]]`, headings, lists) |
| `core/storage.py` | JSON config + state (breakpoint resume) |
| `core/worker.py` | Standalone CLI (`python core/worker.py --text "..."`). **Not used by `app.py` / `backend.py`**. |

**IPC**: `mp.Queue` for text/audio, `mp.Event` for stop, `mp.Value` for status (IDLE/BUSY/COOLING).
**Playback controller**: `backend.py` uses `PlaybackController` plus `S.current_task_id` to invalidate stale TTS and WAV playback threads. Any new playback entrypoint must go through `playback_controller.start_new_session()` or `stop_current_session()`, then only feed audio while `can_feed_audio(session_id, task_id)` remains true.
**Audio cache**: 10 `.npy` files in `QwenTTS-App/data/cache/`, MD5-keyed, LRU by mtime.
**Sentinel**: string `"PIPELINE_END_STRICT_V1"` shared by inference worker and player (must remain a `str` to survive `mp.Queue` pickling).
**Cruise mode**: inference pauses when `audio_queue.qsize() * (2048/24000) > 20s` to cool the GPU.
**Runtime files** under `QwenTTS-App/data/`: `config.json`, `state.json`, `cache/*.npy`, `saved_for_later.json` (max 3 items), `podcasts/*.wav`.

## Default TTS config

Defined in `QwenTTS-App/data/config.json` and `core/tts_engine.py` defaults:

```
model: Qwen3-TTS-1.7B-8bit        (alt: Qwen3-TTS-0.6B)
voice: Serena                      (alts: Ryan, Vivian)
instruct: "Professional female anchor, steady and clear."
temperature: 0.2  top_p: 0.5  top_k: 10  seed: 42  repetition_penalty: 1.1
lang_code: zh  speed: 1.0
```

## Endpoints worth knowing

Standard playback endpoints (`/read`, `/status`, `/stop`, `/pause`, `/resume`, `/seek`) are obvious from the menu callbacks in `app.py`. The non-obvious ones:

- `POST /save_current` / `GET /saved_items` / `POST /play_saved` / `POST /delete_saved` — saved-items queue backed by `data/saved_for_later.json` (max 3, FIFO).
- `POST /generate_podcast` — concatenates all saved items → `data/podcasts/podcast_{ts}.wav` (24kHz int16).
- `GET /debug/state` — local diagnostics for playback session id, task id, queues, stop event, current title, active URL tasks, and active podcast worker count.

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
