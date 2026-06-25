# localTTS macOS AppKit 开发方案与 Timeline

## 1. 项目目标

将现有 QwenTTS 菜单栏工具升级为可独立安装、稳定运行和后续发布的原生 macOS App。

- 使用纯 AppKit 开发前端，不使用 SwiftUI。
- 保留现有 Python、FastAPI、MLX-Audio 和 Qwen3-TTS 推理层。
- AppKit 负责界面、后端进程监管、模型管理、设置和应用更新。
- Python 负责推理、音频播放、播客生成、URL 任务和缓存。
- 第一阶段仅支持 Apple Silicon。
- 发布方式为 Developer ID、公证和 DMG，暂不进入 Mac App Store。

## 2. 总体架构

```text
QwenTTS.app
│
├── AppKit UI
│   ├── NSStatusItem / NSMenu
│   ├── NSPopover
│   ├── NSWindowController
│   └── SettingsWindowController
│
├── ApplicationCoordinator
│   ├── BackendProcessManager
│   ├── BackendAPIClient
│   ├── AppStateStore
│   ├── ModelManager
│   └── UpdateManager
│
├── POSIX BackendLauncher
│   ├── 独立进程组
│   └── Watchdog Pipe
│
└── FastAPI Backend
    └── RuntimeSupervisor
        ├── Inference Worker
        ├── Podcast Workers
        ├── URL Tasks
        ├── Audio Feeder
        ├── PCM Player
        └── Bonjour
```

核心边界：

1. AppKit 不参与 MLX 推理。
2. Python 不管理 macOS 界面和应用更新。
3. 所有 Python 子进程由 `RuntimeSupervisor` 统一管理。
4. AppKit 只终止自己创建且身份验证通过的进程组。
5. 模型、用户数据和应用程序分别管理、分别更新。

## 3. AppKit 前端设计

### 3.1 菜单栏

使用 `NSStatusItem + NSMenu` 提供稳定的快捷操作：

```text
QwenTTS
────────────────
● 空闲 / 正在生成 / 正在播放
文章标题                         3 / 12

朗读剪贴板
暂停 / 继续
上一段
下一段
停止

最近播客 >
稍后阅读 >
────────────────
打开主窗口
设置
诊断
退出
```

使用 `NSPopover` 显示当前标题、进度、播放控制、模型、音色以及后端错误。

### 3.2 主窗口

使用 `NSWindowController + NSSplitViewController` 实现：

- 播放
- 稍后阅读
- 播客
- URL 阅读
- 缓存
- 设置
- 诊断日志

业务状态集中存放在 `AppStateStore`，不散落在 ViewController 中。进程、网络和文件操作使用 Swift Concurrency 或 actor 隔离，所有 UI 更新回到主线程。

### 3.3 AppKit 工程结构

```text
localTTS_macOS/
├── APPKIT_DEVELOPMENT_PLAN.md
└── QwenTTS/
    ├── QwenTTS.xcodeproj
    └── QwenTTS/
        ├── Application/
        │   ├── AppDelegate.swift
        │   └── ApplicationCoordinator.swift
        ├── Backend/
        │   ├── BackendProcessManager.swift
        │   ├── BackendLauncher.swift
        │   ├── BackendAPIClient.swift
        │   └── BackendModels.swift
        ├── State/
        │   └── AppStateStore.swift
        ├── StatusBar/
        │   ├── StatusItemController.swift
        │   └── PlaybackPopoverController.swift
        ├── Windows/
        │   ├── MainWindowController.swift
        │   ├── SettingsWindowController.swift
        │   └── DiagnosticsWindowController.swift
        ├── Models/
        │   └── ModelManager.swift
        └── Updates/
            └── UpdateManager.swift
```

`BackendProcessManager` 使用明确的状态机：

```text
stopped → launching → waitingForHealth → ready → stopping → failed
```

只有 `ApplicationCoordinator` 可以启动或停止后端。

## 4. Python 生命周期重构

这是开始 AppKit 功能开发前的阻断项。

当前后端和播客服务存在只调用 `terminate()`、不执行 `join()` 和超时强杀的问题。需要新增统一的 `RuntimeSupervisor`。

### 4.1 RuntimeSupervisor 职责

- 保存推理 Worker 引用。
- 保存全部播客 Worker 引用。
- 跟踪 URL asyncio task。
- 管理音频、性能监控等后台线程。
- 管理 multiprocessing Queue 和 Event。
- 停止接收新任务。
- 提供可重复调用且结果一致的幂等关闭方法。

### 4.2 统一关闭顺序

```text
停止接受新任务
→ 停止播放并使当前 session 失效
→ 设置全局 shutdown event
→ 取消并等待 URL task
→ 向推理 Worker 发送退出哨兵
→ join(timeout)
→ 超时后 terminate + join
→ 再超时后 kill + join
→ 以相同方式回收播客 Worker
→ 停止后台线程
→ 关闭播放器
→ close()/join_thread() multiprocessing Queue
→ 注销 Bonjour
```

FastAPI lifespan 使用 `try/finally`，确保启动失败或退出异常时仍执行清理。FastAPI 主进程不覆盖 Uvicorn 的 SIGTERM handler。

## 5. 三层进程保护

### 5.1 优雅关闭

AppKit 退出时调用：

```text
POST /control/shutdown
→ 等待后端正常退出
```

### 5.2 独立进程组

通过 `posix_spawn` 创建独立 process group。优雅退出超时后：

```text
killpg(SIGTERM)
→ 等待
→ killpg(SIGKILL)
```

禁止通过扫描命令行或端口杀死未知进程。运行记录保存 instance ID、PID、PGID 和启动时间，清理前必须验证身份。

### 5.3 Watchdog Pipe

- AppKit 持有 Pipe 写端。
- FastAPI 主进程持有读端。
- AppKit 正常退出或崩溃后写端关闭。
- Python 检测到 EOF 后调用同一个 `RuntimeSupervisor.shutdown()`。
- Watchdog FD 不允许被推理和播客 Worker 继承。

HTTP heartbeat 仅作为诊断和第二保险，不能作为主要死亡检测，避免睡眠、App Nap 或调试暂停造成误判。

## 6. API 调整

新增：

```http
GET   /health
GET   /snapshot
GET   /settings
PATCH /settings
POST  /control/heartbeat
POST  /control/shutdown
```

`/health` 示例：

```json
{
  "status": "ready",
  "instance_id": "uuid",
  "pid": 12345,
  "managed": true,
  "accepting_requests": true
}
```

AppKit 不直接修改 `config.json`。普通播放状态在 MVP 阶段每秒轮询；播客、URL 等长任务使用 SSE，断线重连后通过 `/snapshot` 恢复完整状态。

## 7. 本地 API 安全

默认策略：

- 只监听 `127.0.0.1:8001`。
- 默认关闭 LAN 和 Bonjour。
- 移除 CORS `*`。
- 发布版关闭或认证 `/debug/*`。
- 端口冲突时显示错误，不终止未知进程。

保留固定端口 `8001`，因为 Chrome 扩展目前依赖该端口。

使用两类令牌：

1. 每次启动随机生成的管理令牌，仅供 AppKit 调用设置、诊断、心跳和关闭接口。
2. Chrome 扩展配对令牌，由用户从 App 复制配对码并保存在 `chrome.storage`。

所有改变状态的接口都必须认证。LAN 模式后续单独设计配对、限流和监听提示。

## 8. 安装目录和运行时路径

```text
QwenTTS.app/Contents/Resources/
├── Backend/
├── PythonRuntime/
├── MLXAudio/
├── ReferenceAudio/
└── Tools/
    └── ffmpeg

~/Library/Application Support/QwenTTS/
├── Models/
├── Data/
├── Cache/
├── Podcasts/
└── Logs/
```

Python 新增集中式 `RuntimePaths`，所有路径在启动时解析一次，通过以下环境变量传入：

```text
TTS_APP_SUPPORT_PATH
TTS_DATA_PATH
TTS_CACHE_PATH
TTS_PODCASTS_PATH
TTS_MODELS_PATH
TTS_REFERENCE_PATH
MLX_AUDIO_PATH
TTS_FFMPEG_PATH
```

禁止模块根据仓库深度推导运行路径，禁止 App 启动或退出时自动清空缓存。JSON 数据写入使用临时文件加原子替换，并提供一次性旧数据迁移。

## 9. 模型管理

模型约 5.2 GB，必须保存在 Application Support，不进入 `.app`。

`ModelManager` 负责：

- 检查可用磁盘空间。
- 断点下载。
- SHA-256 校验。
- 临时文件下载完成后原子重命名。
- 模型版本和应用兼容性检查。
- 损坏检测与修复。
- 删除和迁移。
- 下载进度及取消。
- 缺少模型时允许用户先进入 UI。

App 更新和模型更新完全分离。

## 10. Python Runtime 与发布

当前开发阶段暂用 Conda `gemini` 环境；进入打包 Spike 后使用独立 Python 3.12
构建环境和 production requirements，从空环境生成发布 Runtime。

### 10.1 当前开发环境决策（2026-06-19）

- 当前终端实际使用 `/Users/funanhe/miniconda3/envs/gemini/bin/python`。
- 当前 Conda `gemini` 环境为 Python 3.11，可用于近期开发和服务层测试。
- 仓库根目录 `.venv` 未被激活，且其 Python 链接仍指向已不存在的
  `/Users/funanhe/miniconda3/envs/kokorotts/bin/python3.12`，因此属于失效环境，可以删除。
- 不修复或复用这个旧 `.venv`，避免把历史依赖带入发布产物。
- Runtime 打包 Spike 开始前，必须建立独立、可复现的 Python 3.12 构建环境。
- 正式发布环境不得直接复制 Conda `gemini`，也不得依赖用户电脑上的 Conda、
  Homebrew Python 或系统 Python。
- 最终 Runtime 方案根据 PyInstaller `onedir` Spike 结果决定；若其无法稳定支持
  MLX、multiprocessing、签名和公证，再评估 `python-build-standalone`。

> 临时约束：Python 3.11 仅用于当前开发，不代表发布版本要求。涉及依赖锁定、
> multiprocessing 行为和最终打包的验收，必须在 Python 3.12 环境重新执行。

明确排除：

- pytest、Black、isort、pre-commit
- STT、STS、VAD 可选依赖
- torch、torchaudio
- 测试文件和开发缓存
- 模型权重

发布资源必须包含并正确签名：

- MLX 相关动态库
- Python `.so`、`.dylib` 和 framework
- PortAudio
- ICL reference audio
- ffmpeg

GUI App 的 `PATH` 通常无法找到 Homebrew，因此发布版不能依赖用户自行安装 ffmpeg。所有嵌套 Mach-O 先签名，再签主 App。

发布目标：

- Apple Silicon only
- Developer ID
- Hardened Runtime
- Apple Notarization + stapling
- DMG
- 后续使用 Sparkle 2 自动更新

## 11. 十周开发 Timeline

前提：一名全职开发者，已有 Python 后端和模型，优先保证可靠性而不是并行堆叠 UI 功能。

| 周期 | 工作重点 | 主要产出 | 验收标准 |
|---|---|---|---|
| 第 1 周 | 后端关闭协议 | `RuntimeSupervisor`、进程注册、幂等 shutdown | 推理和播客任务结束后无残留进程 |
| 第 2 周 | 进程监督 | 独立进程组、Watchdog Pipe、URL task/Queue/线程清理 | AppKit 或后端崩溃后能清理完整进程树 |
| 第 3 周 | 路径与 API | `RuntimePaths`、Application Support、health/settings/shutdown | 脱离仓库相对路径后仍能启动 |
| 第 4 周 | AppKit 基础 | Xcode 工程、菜单栏、Popover、后端状态机 | App 可启动、监控和停止 Python 后端 |
| 第 5 周 | AppKit MVP | 剪贴板朗读、播放控制、设置、错误提示 | 可以替代现有 `rumps app.py` 日常使用 |
| 第 6 周 | 主窗口 | 稍后阅读、播客、URL、缓存和诊断 | 主要 HTTP 功能均可从 App 操作 |
| 第 7 周 | 模型管理 | 检测、下载、校验、迁移、删除和修复 | 新用户无需手动放置模型 |
| 第 8 周 | 发布 Runtime | Python Runtime、ffmpeg、PortAudio 和资源打包 | 无 Homebrew、Python、`.venv` 也可运行 |
| 第 9 周 | 安全和发布 | API 认证、扩展配对、签名、公证和 DMG | Gatekeeper 通过且扩展能正常连接 |
| 第 10 周 | 稳定性与 RC | 故障注入、迁移、性能和发布测试 | 睡眠唤醒、异常退出和更新均稳定 |

### 第 1 周：Python 生命周期重构

- [x] 新建 `RuntimeSupervisor`。
- [x] 管理推理及播客 Worker。
- [x] 跟踪 URL asyncio task。
- [x] 给后台线程增加 shutdown event。
- [x] 实现 `join → terminate → kill` 升级回收。
- [x] lifespan 改为 `try/finally`。
- [x] 删除启动和退出时的 `clear_all_cache()`。
- [x] 修正已退出进程不 `join()` 的问题。
- [x] 增加不加载真实模型的生命周期测试。

实施记录（2026-06-19）：23 项服务及生命周期测试通过；真实不可响应子进程的
SIGTERM → SIGKILL 升级回收测试通过；后端在可访问 Metal 的宿主环境中导入通过。
完整模型推理后的显存释放仍保留为人工验收项。

里程碑 M1：关闭后端后无残留 Python 进程、无 MLX 显存占用，缓存和播客不会被误删。

### 第 2 周：AppKit—Python 进程监督

- [ ] 使用 `posix_spawn` 启动后端。
- [ ] 创建独立 process group。
- [ ] 建立 Watchdog Pipe。
- [ ] 实现管理令牌传递。
- [ ] 实现优雅退出和进程组强制退出。
- [ ] 保存并验证 instance ID、PID、PGID。
- [ ] 测试 AppKit 崩溃、backend 崩溃、Worker 无响应和端口冲突。
- [ ] 测试系统睡眠及唤醒。

里程碑 M2：故障场景下不留下推理或播客孤儿进程，也不误杀其他进程。

### 第 3 周：路径、数据和 API

- [ ] 实现 `RuntimePaths`。
- [ ] 使用 Application Support 目录。
- [ ] 实现旧数据一次性迁移。
- [ ] 增加 health、snapshot、settings、heartbeat、shutdown API。
- [ ] 设置文件使用原子写入。
- [ ] 默认绑定 localhost。
- [ ] 默认关闭 Bonjour 和 LAN。

里程碑 M3：工程移动到其他目录深度后，后端仍可正常启动和读写数据。

### 第 4 周：AppKit 框架

- [ ] 创建纯 AppKit Xcode 工程。
- [ ] 实现 `NSApplicationDelegate` 和 `ApplicationCoordinator`。
- [ ] 实现 `BackendProcessManager` 和 `BackendAPIClient`。
- [ ] 实现 `AppStateStore`。
- [ ] 实现 `NSStatusItem + NSMenu`。
- [ ] 实现基础 `NSPopover`。
- [ ] 实现后端状态机。
- [ ] 正确处理 App 激活和窗口置前。

里程碑 M4：菜单栏 App 能启动、监控、停止后端，并准确显示后端状态。

### 第 5 周：AppKit MVP

- [ ] 朗读剪贴板。
- [ ] 暂停、继续和停止。
- [ ] 上一段和下一段。
- [ ] 显示当前标题和进度。
- [ ] 音色、模型、语速及性能模式设置。
- [ ] 打开播客目录。
- [ ] macOS 通知。
- [ ] 后端错误提示及受控重启。
- [ ] 基础日志查看。

里程碑 M5（Internal Alpha）：日常运行不再依赖 `python app.py`。

### 第 6 周：完整主窗口

- [ ] 播放页面。
- [ ] 稍后阅读管理。
- [ ] 播客任务及文件管理。
- [ ] URL 阅读。
- [ ] 缓存管理。
- [ ] 设置和诊断页面。
- [ ] SSE 长任务状态同步。
- [ ] 通过 snapshot 恢复断线状态。

里程碑 M6：现有主要功能均可从 macOS App 操作。

### 第 7 周：模型管理器

- [ ] 检测已安装模型。
- [ ] 磁盘空间预检。
- [ ] 断点下载及取消。
- [ ] SHA-256 校验。
- [ ] 原子安装。
- [ ] 损坏检测及修复。
- [ ] 模型删除和迁移。
- [ ] 版本兼容清单。
- [ ] 首次启动引导。

里程碑 M7：全新用户无需手动复制模型文件。

### 第 8 周：独立 Runtime 与 Beta

- [ ] 构建最小 Python Runtime。
- [ ] 建立 production requirements。
- [ ] 排除测试和无关依赖。
- [ ] 打包 MLX、Transformers、SciPy 等依赖。
- [ ] 打包 PortAudio、ffmpeg 和 ICL 资源。
- [ ] 检查全部嵌套二进制。
- [ ] 记录实际 App 包体积。

里程碑 M8（Distribution Beta）：在没有 Homebrew、Python、开发 `.venv` 和源代码的干净 Mac 上运行。

### 第 9 周：安全、扩展与发布

- [ ] 管理令牌。
- [ ] Chrome 扩展配对令牌。
- [ ] 状态变更接口认证。
- [ ] 收紧 CORS、Origin 和 debug API。
- [ ] 固定端口冲突测试。
- [ ] Developer ID 签名。
- [ ] Hardened Runtime。
- [ ] 公证和 stapling。
- [ ] 制作 DMG。
- [ ] 接入 Sparkle 2 基础流程。

里程碑 M9：DMG 通过 Gatekeeper，Chrome 扩展通过配对连接后端。

### 第 10 周：稳定性与 Release Candidate

- [ ] 连续朗读一小时。
- [ ] 连续生成多个播客。
- [ ] 播放期间切换模型。
- [ ] AppKit 强制退出测试。
- [ ] Python 后端崩溃测试。
- [ ] Worker 无响应测试。
- [ ] 睡眠和唤醒测试。
- [ ] 音频设备切换测试。
- [ ] 端口冲突测试。
- [ ] 模型下载中断及磁盘不足测试。
- [ ] App 更新和数据迁移测试。
- [ ] Chrome 扩展断线重连测试。
- [ ] 离线使用测试。

里程碑 M10（Release Candidate）：无遗留进程、无用户数据误删、后端可恢复、模型可修复、公证通过。

## 12. 发布门槛

以下条件全部满足后才能发布：

- [ ] 后端正常退出后没有遗留 Python 子进程。
- [ ] AppKit 崩溃后没有遗留 MLX 推理进程。
- [ ] Worker 无响应时能够升级到 SIGKILL 并完成回收。
- [ ] 不扫描或误杀未知进程。
- [ ] App 启停不会删除缓存、播客或用户数据。
- [ ] 模型不打包进 App，且支持校验和修复。
- [ ] 无 Homebrew、无 Python 的干净 Apple Silicon Mac 可以运行。
- [ ] 所有嵌套二进制和主 App 签名、公证通过。
- [ ] 默认只监听 localhost，状态变更 API 需要认证。
- [ ] Chrome 扩展配对、断线和重连正常。
- [ ] 睡眠唤醒及音频设备切换后可以继续使用。

## 13. 关键里程碑

```text
第 2 周：进程安全基础完成
第 3 周：Python 后端具备 App 化条件
第 5 周：AppKit MVP / Internal Alpha
第 8 周：可分发 Beta
第 10 周：Release Candidate
```

如果进度紧张，可以延后完整主窗口、模型下载器和 Sparkle，但不能跳过进程回收、路径解耦、API 安全和独立 Runtime 验证。
