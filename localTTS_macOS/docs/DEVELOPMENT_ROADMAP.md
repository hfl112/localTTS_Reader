# QwenTTS macOS 后续开发路线图

## 核心目标

下一个开发里程碑应以完整用户体验为验收标准：

> 在一台没有开发环境、没有 Conda、没有模型的新 Mac 上，用户从安装 DMG 到成功朗读第一段文本，全流程不需要使用终端。

在实现更多业务功能前，应优先确保应用能够稳定安装、启动、下载模型、朗读和退出。

## 第一阶段：完成基础可用性闭环

### 运行环境兼容性

应用需要同时支持普通用户和已经配置过本地 TTS 开发环境的用户，但两种环境必须明确隔离，避免内置依赖与本机依赖混用。

建议分阶段提供运行环境模式。第一阶段只实现以下两种：

1. **内置环境（默认）**
   - 使用 App 自带的 Python Runtime、MLX-Audio 和后端代码。
   - 模型存放在 `~/Library/Application Support/QwenTTS/Models/`。
   - 不依赖 Homebrew、Conda、系统 Python 或当前终端的环境变量。
   - 这是普通用户和正式发布版本的默认模式。

2. **开发者自定义环境**
   - 允许用户手动指定：
     - Python 可执行文件；
     - `backend.py` 路径；
     - MLX-Audio 根目录；
     - 模型目录；
     - reference 音频目录；
     - ffmpeg 路径。
   - 自定义配置保存在用户 Application Support 中，不写入 App Bundle。
   - 提供“恢复内置环境”按钮，确保配置错误时可以回退。

“自动检测本机环境”推迟到第二阶段以后再评估。自动识别 Conda、pyenv、Homebrew、系统 Python、虚拟环境和 shell shim 的边界条件很多，第一阶段不应为此扩大故障面。开发者手动指定路径已经可以满足复用现有环境和模型的需求。

例如，本机已经存在完整开发环境时，可以配置：

```text
Python:          /path/to/.venv/bin/python
Backend:         /path/to/TTS/localTTS_macOS/backend/core/backend.py
MLX-Audio:       /path/to/TTS/mlx_audio
Models:          /path/to/TTS/mlx_audio/models
Reference Audio: /path/to/TTS/reference
FFmpeg:          /opt/homebrew/bin/ffmpeg
```

应用启动后将这些路径转换成后端环境变量：

```text
TTS_DEV_PYTHON
TTS_DEV_BACKEND
MLX_AUDIO_PATH
TTS_MODELS_PATH
TTS_REFERENCE_PATH
TTS_FFMPEG_PATH
```

#### 兼容性检查

选择本机环境前至少验证：

- Python 架构为 Apple Silicon `arm64`；
- Python 版本符合项目要求；
- `mlx`、`mlx_audio`、`fastapi`、`uvicorn`、`numpy` 和 `sounddevice` 可以导入；
- MLX、MLX-LM、Transformers 等核心依赖版本满足约束；
- `backend.py` 存在并可以通过启动前检查；
- 模型目录包含 `config.json`、权重和 tokenizer 文件；
- ffmpeg 可执行且架构兼容；
- 后端能够在随机测试端口启动，并成功返回 `/health`；
- 本机环境不得与 App 内置 `site-packages` 混合使用。

兼容性检查失败时，应显示具体缺失项，并继续使用内置环境。不能因为检测到一个 `python` 可执行文件就直接认为环境可用。

#### 路径优先级

建议使用以下明确优先级：

```text
用户在设置中选择并通过验证的开发环境
→ App 内置环境
```

正式发布版始终保留内置环境作为最终回退。开发环境中的模型可以复用，不需要重复下载数 GB 权重；数据、缓存、日志和配置仍建议保存在 Application Support，避免污染源码目录。

### 开发内容

- 增加首次启动向导，检查：
  - macOS 和 Apple Silicon 兼容性；
  - 可用磁盘空间；
  - 模型安装状态；
  - 默认音频输出设备；
  - 后端服务状态；
  - 用户指定的 TTS/Python 环境及其兼容性。
- 设置页增加运行环境选择：内置环境、自定义开发环境。
- 增加环境兼容性检测报告和一键恢复内置环境功能。
- 模型下载显示真实进度、速度和预计剩余时间。
- 支持模型下载暂停、恢复、失败重试和文件完整性校验。
- 后端启动失败时，在 UI 中直接显示错误原因和日志入口。
- 明确区分以下状态：
  - 后端未启动；
  - 后端启动中；
  - 模型未安装；
  - 模型加载中；
  - 空闲；
  - 正在生成；
  - 正在播放；
  - 暂停；
  - 错误。
- 检测端口 `8001` 冲突，不得终止未知进程。
- 完整验证以下场景：
  - Serena 和 Ryan 单人朗读；
  - 多人对话；
  - 中文、英文和中英混合文本；
  - 长文本分段；
  - 暂停、恢复、停止和切换任务；
  - 睡眠唤醒和音频设备切换。

### 验收标准

- 新用户无需终端即可安装模型并完成首次朗读。
- 已有兼容 TTS 环境的开发者可以通过手动配置复用运行环境和模型，不需要重复下载。
- 常见故障均能在 UI 中看到明确原因和处理方式。
- **严格子进程管理**：应用退出后（包括 Force Quit 或 Crash）绝不残留 Python、推理或 resource tracker 进程。
  - *技术决策*：子进程回收不能依赖 Python `atexit` 钩子（无法捕获 SIGKILL 强退），也不能仅凭轮询 stale PID（进程号可能被复用）。后端应基于标准输入（stdin pipe）探活，或通过 macOS 的 `kqueue` (`EVFILT_PROC`) 系统调用监听父进程退出事件，以确保能够绝对可靠地自我终止。
- VoiceOver 能够读出主要控件、状态和错误信息，核心操作可通过键盘完成。

## 第二阶段：缩小安装包并建立独立运行环境

当前 DMG 约为 995 MB，仍包含较多无关依赖，例如：

- PyTorch；
- pandas、scikit-learn、matplotlib；
- Playwright；
- MLX-Audio Web UI 和 `node_modules`；
- 测试与开发工具。

### 开发内容

- 建立专用 Python 发布环境，不再直接复制个人 Conda 环境。
- 引入 `uv` 或 `pip-tools` 等依赖锁定工具生成明确的依赖配置锁文件，替代人工检查。
- 只安装 QwenTTS 实际运行所需的生产依赖。
- 排除测试、文档、缓存、Web UI 和前端构建依赖。
- 使用 `uv` 或等价工具生成可复现的依赖锁定结果；发布构建不得依赖开发者当前环境中的隐式包。
- 增加打包后 import smoke test，验证所有生产模块可导入。
- 审计所有 Mach-O、动态库和 `@rpath` 依赖。
- 单独验证 `sounddevice` 与 `libportaudio` 的打包和签名，不依赖 Homebrew 的动态库路径。
- 在开始裁剪前测量最小环境中 MLX、MLX-LM、Transformers、音频依赖和 Python 标准库的实际体积，依据测量结果确定最终 DMG 目标。
- 评估自动检测本机 Python/TTS 环境；只有兼容性矩阵和测试覆盖足够时才实施。
- 在 UI 文案继续增加前引入 `Localizable.strings`，至少支持中文和英文。

### 目标

```text
DMG：300–500 MB
模型：首次启动后独立下载
```

### 验收标准

- 应用在不存在 Homebrew、Conda 和系统 Python 依赖的机器上运行。
- 删除任意必需依赖时，构建诊断必须失败，而不是运行时静默失败。

## 第三阶段：正式发布工程

### 开发内容

- 配置 Apple Developer ID Application 签名。
- 启用 Hardened Runtime。
- 完成 Apple notarization 和 staple。
- 移除要求用户执行 `xattr -cr` 的安装流程。
- 统一版本号和构建号管理。
- 引入安全的自动更新机制。
- 将发布流程固定为：

```bash
python package_release.py
python run_diagnostics.py
python make_dmg.py
```

- CI 自动执行：
  - Swift Release 编译；
  - Python 单元测试；
  - Swift 类型检查；
  - Python Runtime 可迁移性检查；
  - 严格代码签名检查；
  - DMG 完整性验证。
- 在干净用户账户及 macOS 14、15、26 上进行兼容性测试。
- 编写隐私声明，明确文本、网页内容、模型、日志和诊断信息是否离开本机。
- 建立本地 crash report 检测和用户主动导出流程。
- 如需远程崩溃收集，必须默认关闭或明确征得用户同意，并在上传前移除朗读正文、URL、令牌和本地路径。

### 验收标准

- 用户可以直接双击安装和启动，不需要绕过 Gatekeeper。
- 发布产物可追溯到明确的 Git commit、依赖版本和构建日志。

## 第四阶段：架构与用户体验优化

### Swift 客户端

- 将 `[String: Any]` JSON 解析逐步替换为 `Codable` 模型。
- 为后端连接、模型管理和播放建立明确状态机。
- 避免 ViewController 直接承担网络、文件和业务状态管理。
- 所有 UI 更新保持在主线程，消除 Swift 6 并发警告。
- BackendLauncher 增加崩溃次数限制和指数退避。

### Python 后端

- 后端错误使用稳定的错误码和结构化响应。
- 播客、URL 抓取和模型下载使用 SSE 或事件推送。
- 对模型加载失败、Metal 错误和音频设备错误进行分类处理。
- 为运行日志增加文件大小限制和日志轮转。
- 对所有后台任务增加取消、超时和幂等关闭测试。

### 诊断能力

- 增加“一键导出诊断包”。
- 诊断包可以包含：
  - App 和后端版本；
  - macOS 与硬件信息；
  - 模型安装状态；
  - 最近结构化日志；
  - 后端及子进程状态；
  - 音频设备信息。
- 诊断包不得包含用户朗读正文、配对令牌或其他敏感信息。

### Chrome 扩展

- 增加重新配对和连接诊断。
- 显示 App、后端和扩展之间的版本兼容状态。
- 明确区分 App 未启动、配对失败、API 版本不兼容和任务失败。

### 质量与合规性保障

- **无障碍访问 (Accessibility)**：确保主要 UI 控件对 VoiceOver 友好，支持全键盘导航。
- **隐私声明**：提供明文隐私政策，强调所有文本处理和模型推理完全在本地执行，保障数据隐私（上架 App Store 及 Apple 公证的前提）。
- **崩溃分析**：引入轻量级崩溃日志收集与上报功能（需获得用户授权）。

## 关键设计决策

### 1. 第一阶段不实现自动环境发现

第一阶段只支持“内置环境”和“开发者手动指定环境”。自动发现会涉及 Conda base/env、pyenv shim、Homebrew Python、系统 Python、shell 初始化状态和多架构环境，投入大且容易产生不可复现问题。

手动配置仍必须经过完整兼容性检查，不能因为路径存在就直接启动。自动发现可以在第二阶段完成环境矩阵和测试后再决定是否开发。

### 2. 模型目录和已有模型复用

默认模型目录继续使用：

```text
~/Library/Application Support/QwenTTS/Models/
```

`Application Support` 通常不属于 iCloud Drive 自动同步目录，因此不需要依赖 `.nosync` 标记。更值得处理的是 Time Machine 和其他备份工具：对可重新下载的大模型，可使用 macOS 的 `isExcludedFromBackup` 资源属性排除备份，并在设置中向用户说明。

开发者复用 `mlx_audio/models/` 时使用经过验证的绝对路径，不在 Application Support 中创建 symlink。直接保存外部模型目录配置更清晰，也避免链接失效、权限变化和路径穿越问题。应用不得移动、删除或更新外部模型，除非用户明确授权。

### 3. DMG 体积目标必须先测量

从约 995 MB 缩减到 300–500 MB 有可能实现，但不能先设目标再盲目删除依赖。应先创建最小发布环境，分别记录以下部分的体积：

- Python Runtime 和标准库；
- MLX、MLX-LM 和 Transformers；
- NumPy、SciPy 和音频依赖；
- `sounddevice` 与 `libportaudio`；
- QwenTTS 后端源码和 reference 音频；
- 签名及 DMG 压缩前后的体积。

依赖使用 `uv` 或等价锁定方案管理，并通过导入测试、后端启动测试和一次真实 TTS 推理证明裁剪没有破坏功能。

### 4. 不残留进程是 P0，但不依赖 `atexit`

当前原生 AppKit 客户端直接启动 Python 后端，不再通过旧 `app.py` 启动。可靠关闭应继续使用现有三层机制：

1. `RuntimeSupervisor` 负责正常关闭 worker、线程、队列、播放器和任务；
2. Watchdog Pipe 在 App 崩溃或被 Force Quit 后通过 EOF 通知后端自我关闭；
3. 独立进程组在优雅关闭超时后使用 `SIGTERM`，最后才使用 `SIGKILL`。

`atexit` 在 crash、Force Quit 和 `SIGKILL` 下不可靠，只能作为辅助。启动时也不能仅凭 stale PID 文件杀进程；PID 可能已被系统复用。若保存运行记录，必须同时验证 instance ID、PID、PGID、可执行路径和 `/health` 返回的身份，无法确认时只提示用户，不终止未知进程。

### 5. 无障碍、隐私、国际化和崩溃诊断

- **无障碍访问**：值得提前进入第一阶段。首次启动、模型下载、播放控制和错误提示必须支持 VoiceOver 和键盘导航。
- **隐私与数据政策**：在第三阶段发布前必须完成，但从现在开始就要约束日志和诊断数据，避免写入用户正文和令牌。
- **国际化**：应在第二阶段 UI 大规模扩展前引入，否则后期迁移硬编码中文成本会持续增加。
- **崩溃收集**：优先支持读取系统 crash report 和一键导出诊断包；远程收集应作为可选能力，不能默认上传敏感内容。

## 推荐优先级

| 优先级 | 工作项 | 原因 |
|---|---|---|
| P0 | 首次启动、模型安装、错误提示 | 决定新用户能否完成首次朗读 |
| P0 | 干净机器安装测试 | 验证 DMG 是否真正可分发 |
| P0 | 后端和子进程生命周期 | 防止残留进程、GPU 和音频资源泄漏 |
| P0 | 基础无障碍支持 | 确保核心流程可通过 VoiceOver 和键盘操作 |
| P1 | 精简 Python Runtime | 降低下载和安装成本 |
| P1 | Developer ID 与公证 | 支持正常对外分发 |
| P1 | 结构化状态和诊断 | 降低后续维护成本 |
| P1 | 国际化基础设施 | 避免 UI 扩展后再迁移硬编码文案 |
| P1 | 隐私与本地崩溃诊断 | 为正式分发建立清晰的数据边界 |
| P2 | 自动检测本机环境 | 边界条件多，应在兼容性测试完善后实施 |
| P2 | SSE、自动更新、扩展体验 | 提升长期使用体验 |
| P2 | 新业务功能 | 应在基础稳定性达标后进行 |

## 建议的近期迭代顺序

1. 完成首次启动向导和真实模型下载进度。
2. 在全新 macOS 用户账户中完成端到端测试。
3. 精简 Python Runtime 和 MLX-Audio 打包内容。
4. 建立 Developer ID 签名、公证和 CI 发布流程。
5. 完善状态机、结构化错误和诊断包。
6. 再开始扩展新的朗读、播客和自动化功能。
