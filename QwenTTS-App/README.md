# QwenTTS-App 本地 TTS 菜单栏服务

QwenTTS-App 是整个项目的本地语音基站：macOS 状态栏负责控制，FastAPI 后端负责接收浏览器插件、URL-Reader 和菜单栏请求，MLX/Qwen3-TTS 推理子进程负责生成音频，`sounddevice` 播放器负责把 PCM/WAV 播出来。

当前代码已经从早期的单体 `backend.py` 拆成服务层。`backend.py` 现在主要保留路由、推理 worker、音频 feeder 和生命周期管理，播放会话、播客生成、缓存和稍后朗读分别由独立 service 接管。

## 运行方式

```bash
cd /Users/funanhe/00_MyCode/TTS/QwenTTS-App
python app.py
```

- `app.py` 会启动 `core/backend.py`，后端监听 `127.0.0.1:8001`。
- 模型默认从项目根目录的 `mlx_audio/models/` 读取。
- 可用环境变量 `MLX_AUDIO_PATH` 和 `TTS_WORKSPACE_PATH` 解耦相对路径。
- macOS/Apple Silicon 环境必须使用 `multiprocessing.spawn`，不要切到 `fork`。

## 运行链路

```text
app.py / Chrome extension / URL-Reader
        ↓
FastAPI routes in core/backend.py
        ↓
PlaybackService / PodcastService / SavedItemsService / CacheService
        ↓
mp.Queue + inference_worker
        ↓
TTSEngine.generate_stream()
        ↓
audio_feeder_thread
        ↓
PCMPlayer(sounddevice/CoreAudio)
```

## 核心文件

| File | Role |
|---|---|
| `app.py` | macOS `rumps` 菜单栏 UI；启动后端；1Hz 轮询 `/status`；触发朗读、暂停、停止、seek、播客播放 |
| `core/api_models.py` | FastAPI 请求模型；定义 `/read`、`/read_url`、podcast、cache、saved-items 等 endpoint 的请求字段和默认值 |
| `core/backend.py` | FastAPI 路由、推理子进程、音频 feeder、Bonjour、生命周期管理 |
| `core/state/runtime_state.py` | 当前标题、进度、播放状态、当前播客、播客 buffer 等运行态 |
| `core/services/playback_service.py` | `PlaybackController` + `PlaybackService`；统一管理播放 session、task id、队列清理、TTS/WAV 播放 |
| `core/services/podcast_service.py` | 后台播客生成、GPU 独占锁、暂停调度、chunk 断点、播客文件管理 |
| `core/services/podcast_jobs.py` | `podcast_jobs.json` 任务状态持久化，记录 queued/running/done/failed/canceled |
| `core/services/runtime_log.py` | `runtime_events.jsonl` 结构化事件日志，记录播放、URL、播客任务和错误事件 |
| `core/services/performance.py` | `fast`、`balanced`、`quiet` 三档性能 profile 与阅读时长估算 |
| `core/services/saved_items_service.py` | `data/saved_for_later.json` 的增删查清理 |
| `core/services/cache_service.py` | 临时音频缓存元数据、播放、导出、删除和清空 |
| `core/tts_engine.py` | MLX/Qwen3-TTS 推理封装；音色锁定、ICL 参考音频、流式 PCM 输出 |
| `core/player.py` | 24kHz/2ch/float32 `sounddevice.OutputStream` 播放器；设备切换重试与手动重启 |
| `core/processor.py` | Markdown 清洗、智能分段、双人对话 `[Serena]`/`[Ryan]` 解析 |
| `core/storage.py` | JSON config/state 与 SQLite cache metadata |
| `core/worker.py` | 独立 CLI worker；不参与 `app.py`/`backend.py` 主链路 |

## 关键约束

- 播放入口必须通过 `PlaybackService.start_new_session()` 或 `stop_current_session()` 换代 session，不能只清 `stop_event`。这是修复 TTS 和 podcast “串台”的核心机制。
- endpoint 请求参数必须优先在 `core/api_models.py` 中定义 Pydantic model，避免 route 内继续手写松散 `dict.get()` 解析。
- `GLOBAL_SENTINEL = "PIPELINE_END_STRICT_V1"` 必须保持字符串，保证 `mp.Queue` 跨进程序列化稳定。
- 实时朗读默认 `balanced`；后台 podcast 默认 `quiet`；长单篇和合集 podcast 优先使用 `Qwen3-TTS-0.6B` 降温。
- `PodcastService` 是后台 podcast 进程、暂停事件、GPU 锁和 chunk checkpoint 的唯一 owner。
- `podcast_jobs.json` 是后台 podcast 任务的状态快照；`runtime_events.jsonl` 是排查串台、静音、任务残留和过热暂停的事件历史。
- `PCMPlayer` 只在主进程初始化，推理子进程不要加载 CoreAudio 设备。

## 常用接口

- 播放控制：`POST /read`、`POST /stop`、`POST /pause`、`POST /resume`、`POST /seek`、`POST /restart_audio`
- URL 任务：`POST /read_url`
- 稍后朗读：`POST /save_for_later`、`GET /saved_items`、`POST /play_saved`、`POST /delete_saved`、`POST /saved_items/clear`
- 播客：`POST /generate_single_podcast`、`POST /generate_podcast`、`GET /podcasts/list`、`GET /podcasts/jobs`、`POST /podcasts/play`、`POST /podcasts/delete`、`POST /podcasts/toggle_pin`、`POST /podcasts/clear`
- 缓存：`GET /cache/items`、`POST /cache/play`、`POST /cache/export`、`POST /cache/delete`、`POST /cache/clear`
- 诊断：`GET /status`、`GET /debug/state`、`GET /debug/events?limit=50`

## 运行文件

```text
QwenTTS-App/data/
├── config.json
├── state.json
├── saved_for_later.json
├── podcast_jobs.json
├── runtime_events.jsonl
├── cache/*.npy
└── podcast_chunks/*/chunk_*.npy

/Users/funanhe/00_MyCode/TTS/podcasts/
└── podcast_*.wav
```

`data/cache/` 是临时朗读缓存；根目录 `podcasts/` 是用户可保留的成品音频；`data/podcast_chunks/` 是长播客分段 checkpoint；`podcast_jobs.json` 记录后台任务当前状态；`runtime_events.jsonl` 记录最近运行事件。

## 验证命令

```bash
python -m py_compile QwenTTS-App/core/backend.py QwenTTS-App/core/player.py QwenTTS-App/core/processor.py QwenTTS-App/core/tts_engine.py QwenTTS-App/core/services/*.py QwenTTS-App/core/state/runtime_state.py QwenTTS-App/core/tests/test_services_smoke.py
python -m pytest -q QwenTTS-App/core/tests/test_services_smoke.py
```
