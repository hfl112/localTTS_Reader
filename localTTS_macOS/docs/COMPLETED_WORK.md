# localTTS macOS 项目已完成工作整理

你好，LALALA。以下是为您整理的本项目（`localTTS_macOS`）已完成开发工作的结构化总结：

---

## 1. macOS Swift 客户端界面改造（AppKit 原生）

前端界面摒弃了传统的标准 macOS 窗口样式，全面升级为轻色系极简毛玻璃质感（Glassmorphism）：

* **全窗口毛玻璃底盘**：
  * 修改文件：`/Users/funanhe/00_MyCode/TTS/localTTS_macOS/QwenTTS/QwenTTS/Windows/MainWindowController.swift`
  * 核心逻辑：在 Window 底层注入 `NSVisualEffectView`（`behindWindow` 混合模式），同时隐藏系统标题栏（Titlebar），实现无边框的全窗口磨砂毛玻璃底座。
* **Tab 路由透明适配**：
  * 修改文件：`/Users/funanhe/00_MyCode/TTS/localTTS_macOS/QwenTTS/QwenTTS/Windows/MainTabViewController.swift`
  * 核心逻辑：调整子标签页的初始化，使子视图背景继承主窗口的透明毛玻璃，消除视觉割裂。
* **控制台实时朗读卡片化与歌词滚动**：
  * 修改文件：`/Users/funanhe/00_MyCode/TTS/localTTS_macOS/QwenTTS/QwenTTS/Windows/ConsoleViewController.swift`
  * 核心逻辑：
    * 采用阴影分离容器架构，在外层 `shadowContainer` 投射阴影，内层 `NSVisualEffectView` 裁剪圆角，实现平滑的连续超椭圆（Squircle）边缘。
    * 字体统一使用 **SF Pro Text** (英文) 与 **PingFang SC** (中文)，仅通过字号、字重与透明度区分层级。
    * 重构底部控制栏为**单行三段式 overlay 布局**，主播放控制组（后退15s → 上一句 → 播放/暂停 → 停止 → 下一句 → 前进15s）绝对居中。
    * 实现 **Karaoke 歌词滚动 (Rolling Lyrics)**：使用 `NSScrollView` 动态排布文本行，利用 `CAGradientLayer` 遮罩实现上下边缘 15% 渐变淡出（Gradient Fade Mask），并用 `NSAnimationContext` 配合平滑缓动实现 0.45s 视窗垂直居中滚动，且距离当前句越远的句子字号及不透明度越低。

---

## 2. 苹果标准高清应用图标集成

* **图标编译与导出**：
  * 输出文件：`/Users/funanhe/00_MyCode/TTS/localTTS_macOS/AppIcon.icns`
  * 核心逻辑：裁剪原桌面截图的正中心像素，利用 Lanczos 插值无损缩放至 $1024\times1024$，配合超椭圆曲线方程（$n=4.5$）生成透明圆角遮罩，剔除杂质后利用 macOS 系统的 `iconutil` 工具编译成苹果 Retina 视网膜标准的多分辨率 `.icns` 图标。
* **集成覆盖**：
  * 已在打包发布脚本中配置，自动覆盖至打包产物 `/Users/funanhe/00_MyCode/TTS/localTTS_macOS/dist/QwenTTS.app/Contents/Resources/AppIcon.icns` 中。

---

## 3. Python 后端生命周期与安全认证重构

为了配合原生客户端的启动和异常退出管理，对物理独立的后端 snapshot 进行了以下重构：

* **Watchdog 死亡匿名管道 (双向退出保障)**：
  * 修改文件：`/Users/funanhe/00_MyCode/TTS/localTTS_macOS/backend/core/services/runtime_supervisor.py` 及 `/Users/funanhe/00_MyCode/TTS/localTTS_macOS/backend/core/backend.py`
  * 核心逻辑：后端在 `lifespan` 启动时运行 `watchdog_thread` 并阻塞在 `os.read(watchdog_fd, 1)` 上。当 Swift 客户端退出或异常崩溃时，操作系统会自动关闭管道写端，后端立即收到 EOF 并触发 `shutdown` 优雅退出，防止推理和播客进程残留。
* **管理令牌认证 (Token API Security)**：
  * 修改文件：`/Users/funanhe/00_MyCode/TTS/localTTS_macOS/backend/core/backend.py`
  * 核心逻辑：加入 API 拦截中间件，核心控制类 API 均需校验 HTTP Header 中的 `X-Management-Token`，该 Token 在 Swift 启动后端时通过环境变量 `TTS_MANAGEMENT_TOKEN` 随机传入，防止本地未授权程序恶意调用。

---

## 4. 独立发行版打包与打包前诊断脚本

* **独立运行环境打包**：
  * 修改文件：`/Users/funanhe/00_MyCode/TTS/localTTS_macOS/package_release.py`
  * 核心逻辑：下载并集成 python-standalone 运行时环境，使用 `uv` 自动化编译安装 `requirements.prod.lock` 中的生产依赖；打包时将 `mlx_audio` 库放置到独立资源目录中；脚本末尾遍历所有 Mach-O 二进制文件，由深到浅依次执行 `codesign` 强行签名。
* **诊断与环境校验**：
  * 修改文件：`/Users/funanhe/00_MyCode/TTS/localTTS_macOS/run_diagnostics.py`
  * 核心逻辑：对打包后的 AppBundle 结构进行快速验证，包括可执行权限检查、Info.plist 部署目标检查、Relocatable 运行期环境测试等，确保打包产物在无 Python 开发环境的 Mac 上可顺利运行。
* **依赖环境配置**：
  * 修改文件：`/Users/funanhe/00_MyCode/TTS/localTTS_macOS/build_runtime.py`、`/Users/funanhe/00_MyCode/TTS/localTTS_macOS/requirements.prod.txt`、`/Users/funanhe/00_MyCode/TTS/localTTS_macOS/requirements.prod.lock`
  * 核心逻辑：锁定生产发布所需的最小依赖包，隔离开发用测试、格式化等冗余依赖。

---

## 5. 2026-06-21 会话更新（多供应商引擎 / AI 配置页 / 内容中心 / 一批修复）

### 5.1 原生 App 启动链路（已稳定，可出声）
* **DEBUG 自动探测开发环境**：`QwenTTS/QwenTTS/Backend/BackendProcessManager.swift` 的 `seedDevEnvironmentIfNeeded()`（`#if DEBUG`，用 `#filePath` 推导仓库根）——未打包时自动用同仓库 conda `gemini` 解释器 + `backend/core/backend.py` + `mlx_audio/models` 模型，无需手填环境。
* **动态端口 + 发现文件**：App 在 8002~8100 扫空闲端口（**绝不用 8001**，用户自占用）；后端 lifespan 写 `~/Library/Application Support/QwenTTS/runtime.json`（port/pid/instance_id），关闭时删除，供扩展/其他客户端发现。
* **修复"TTS 不发声"三处根因**：① builtin 模式 + 自定义环境空 → scriptPath 空；② `ApplicationCoordinator.start()` 模型缺失强弹设置向导拦截 → DEBUG 下探测到环境就跳过向导直接 `startBackend`；③ `BackendLauncher` 的 App Support 块无条件覆盖 `TTS_MODELS_PATH` → 改为仅在未设时填默认。
* **补主菜单**：`AppDelegate.setupMainMenu()` 加标准"编辑"菜单，修复菜单栏 App 文本框 Cmd+C/V/X/A 失效。
* 已用 **Xcode 26.5**（`DEVELOPER_DIR=/Applications/Xcode.app/...`）构建验证；实跑确认 App 自动起后端、模型从正确路径加载、合成播放 `BUSY→IDLE`。

### 5.2 Provider-agnostic 翻译 / LLM 引擎层（`backend/URL-Reader/`）
* **两个家族**：`translation_engine.py`（机器翻译，Google 免费/微软/DeepL）用于 `translate`；`llm_engine.py`（Gemini 内联 google-genai / Claude / OpenAI / DeepSeek / 本地 MLX）用于 `podcast-discuss` / `podcast-trans`。`engine_config.py` 统一从 config.json `engines` 段读配置。
* **完全解耦本地 .env**：所有 key 只来自前端配置（`engines.llm.keys` / `engines.translate.*`），无 .env / 无 env-var 兜底；`gemini_engine.py` 不再被依赖（已成孤儿可删）。
* **去 tier / 单模型**：每家一个普通模型（`engines.llm.models`），默认用滚动别名抗下线：gemini=`gemini-flash-latest`、claude=`claude-sonnet-4-6`、openai=`gpt-4o`、deepseek=`deepseek-chat`。`selected` 排首位 + 跨供应商 fallback。
* **翻译与 LLM 分离**：普通翻译只走机器翻译（`translate.order=[google,microsoft,deepl]`，无 llm）；双人总结/翻译才走 LLM。
* **目标语言**：`engines.translate.target_lang`（UI 中/英下拉，即时保存）。`LANG_MAP` 映射各家语言码；LLM 翻译 prompt、`title_for_mode` 译文前缀 `[译·<语言名>]` 都跟随它。
* **后端端点**（均需管理令牌，中间件 `path.startswith("/engines")`）：`GET/PATCH /engines`、`POST /engines/check`（带 key 先存再探测，复用各 provider 的 `probe_provider`）。

### 5.3 AI 引擎配置页（`QwenTTS/QwenTTS/Windows/EngineSettingsViewController.swift`）
* sidebar 新增第 4 项「AI 引擎」（`MainSplitViewController` + `SidebarViewController`，1:1 映射）。
* 两个 section（翻译 / LLM），每个：下拉选供应商 → 动态显示对应 key 框（`isHidden` 切换）+「检测连通性」按钮（成功提示"可以使用相关功能了"）。翻译 section 含「目标语言」中/英下拉（改变即 PATCH，无需点保存）。
* `BackendAPIClient` 加 `fetchEngines/updateEngines/checkEngine/fetchPodcastTranscript`；`APIModels` 加 `EngineConfig`/`EngineLLMConfig`/`EngineTranslateConfig`。

### 5.4 内容中心耦合真实数据（`QwenTTS/QwenTTS/UI/Library/LibraryView.swift`）
* 新增 `LibraryViewModel`（注入 coordinator），四分类接真实接口：即时阅读→`/saved_items`(source=clipboard)、稍后阅读→`/saved_items`(其他)、播客文稿→`/podcasts/list`、缓存→`/cache/items`。
* 接通播放/删除/清空缓存；**双击查看完整文本**（saved/instant/cache 用 `fullText`；播客异步取 `.txt` 文稿）。
* **播客文稿 sidecar**：`podcast_service.py` 生成 wav 时写同名 `.txt`；后端 `GET /podcasts/transcript?filename=` 读取。

### 5.5 模式打通与一批修复
* `/read` 加 `mode` 字段：非 original 时先 `reader_service.process_with_llm`（run_in_threadpool）再朗读 → 纯文本也能翻译/双人。
* `ConsoleViewController`：修正模式字符串（translate/podcast-discuss/podcast-trans）；`handleModeChange` 删除写死的 "Coming Soon" 拦截（之前点翻译会弹回原文）；「稍后/播客」按钮判断 URL → 走 `/read_url` 抓取处理（之前把 URL 原样存了，导致"播放没反应"）。
* **没配 LLM key 拦截**：AI 模式（双人总结/翻译）触发前检查 `engines.llm` 选中项是否有 key，无则弹窗提示去「AI 引擎」页配置。
* **TTS 中英 ref 自动切**：`tts_engine.py` 按文本内容自动判断语种，中文→`ref_serena_zh.wav`、英文→`bbc_news.wav`（本来就有，之前"中文锁音"是 target_lang bug 的副作用）。
* **播客模型路径 bug**：`podcast_service.py` 原写死 `mlx_audio_path="../../mlx_audio"`（解析到不存在的 `localTTS_macOS/mlx_audio`），改用 `runtime_paths` —— 这是播客生成失败的真因（非 ffmpeg）。

### 5.6 已端到端验证（YouTube）
* 双人总结 + 稍后阅读：✅ saved item 是真正的双人脚本（TED `arj7oStGLkU`）。
* 播客生成：✅ 生成 16M wav（`dQw4w9WgXcQ`，Serena/Ryan 交替配音）。
* 播放 saved 项 → snapshot 带 `current_article_chunks`（20 chunks）→ Console 自动滚动：✅。
* `/engines` GET/PATCH/check、翻译中→英、Gemini 真 key 连通：✅。

### 5.7 已解决的问题
* **测试用例硬编码断言失败**：`test_services_smoke.py` 原来硬编码校验了 `[中文翻译]Title`，现已修复为兼容动态目标语言设置的模糊匹配断言。
* **合集播客生成缺少文稿**：`podcast_service.py` 里的 `run_concat_podcast_worker` 原在生成大合集时无同名 `.txt` 脚本保存逻辑，现已补全。

### 5.8 待测试 / 待办 / 遗留缺陷
* **老旧播客文稿缺失的退化体验**：双击旧播客时，若找不到 `.txt` 文稿，客户端直接播放音频，缺乏友好弹窗提示用户“该旧版播客无可用文稿”。
* **URL 任务失败静默**：字幕禁用、抓取网页失败等，前端没有错误提示——建议补“把 `url_jobs` 的 error 弹给用户”。
* **多 LLM 供应商的 Key 连通性**：Claude、OpenAI 和 DeepSeek 目前在后端已解耦支持，但尚未输入有效 API Key 进行端到端调试，仅 Gemini 确认联调无误。
* **本地 MLX provider**：未端到端测过（best-effort，默认禁用，需配 `local_model_path`）。
* **英文 TTS 音色**：英文 ref 用 `bbc_news.wav`；要“Serena 英文本人声”需录制英文参考音频并更新 `tts_engine.py`。
* **发布打包**：`package_release.py` 未在新引擎架构 + anthropic/openai 新依赖下重测。
* **遗留清理**：`gemini_engine.py`（孤儿）、config 里残留的旧 `tiers` 字段（已忽略，无害）。

### 5.9 2026-06-22 会话更新（即时文稿、Saved Item 及播客播放精确滚动定位）
* **即时文稿与 Saved Item 实时定位**：
  * 修改文件：
    * [`/Users/funanhe/00_MyCode/TTS/localTTS_macOS/backend/core/player.py`](file:///Users/funanhe/00_MyCode/TTS/localTTS_macOS/backend/core/player.py)
    * [`/Users/funanhe/00_MyCode/TTS/localTTS_macOS/backend/core/services/playback_service.py`](file:///Users/funanhe/00_MyCode/TTS/localTTS_macOS/backend/core/services/playback_service.py)
    * [`/Users/funanhe/00_MyCode/TTS/localTTS_macOS/backend/core/backend.py`](file:///Users/funanhe/00_MyCode/TTS/localTTS_macOS/backend/core/backend.py)
  * 核心逻辑：重构实时推理和播发流程。在推理进程生成各分句音频时，将句段索引通过 `shared_state.audio_q` 级联传递至播放层。`PCMPlayer` 的 CoreAudio 回调线程物理消费数据包时更新 `currently_playing_index`，由 `/snapshot` 定时轮询返回给 Swift 原生层。同时，将原来推入队列时超前将 `current_index` 拉满的缺陷修复，彻底实现即时与已保存项目的动态高亮卡拉 OK 滚动。
* **播客（预渲染 WAV）字数自适应加权滚动**：
  * 修改文件：[`/Users/funanhe/00_MyCode/TTS/localTTS_macOS/backend/core/services/playback_service.py`](file:///Users/funanhe/00_MyCode/TTS/localTTS_macOS/backend/core/services/playback_service.py)
  * 核心逻辑：在 `_play_wav_thread` 中实现根据同名 `.txt` 文本字数、标点等特征算得的字数停顿加权二分定位算法。同时在估算比率时扣除了播放器缓冲队列 (`player.audio_queue.qsize()`) 的待播时长，消除了 10 秒左右的文本提前滚动偏差，实现物理发声与视觉高亮的高度一致。
