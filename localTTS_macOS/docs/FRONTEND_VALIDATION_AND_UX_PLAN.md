# Frontend Validation and UX Plan

本文档用于指导 `localTTS_macOS/QwenTTS/` 原生 macOS 前端的验证与用户体验改进。目标是让前端逻辑可验证、状态可解释、失败可恢复，并减少用户理解 Python、MLX、端口、模型路径等底层细节的成本。

## 1. 验证范围

前端验证覆盖以下链路：

```text
用户操作
-> ViewController / SwiftUI View
-> BackendAPIClient
-> BackendProcessManager
-> AppStateStore
-> UI 状态刷新
```

重点文件：

- `QwenTTS/QwenTTS/StatusBar/StatusItemController.swift`
- `QwenTTS/QwenTTS/StatusBar/PlaybackPopoverController.swift`
- `QwenTTS/QwenTTS/Windows/MainWindowController.swift`
- `QwenTTS/QwenTTS/Windows/ConsoleViewController.swift`
- `QwenTTS/QwenTTS/Windows/SettingsViewController.swift`
- `QwenTTS/QwenTTS/Windows/EnvironmentViewController.swift`
- `QwenTTS/QwenTTS/Backend/BackendProcessManager.swift`
- `QwenTTS/QwenTTS/Backend/BackendAPIClient.swift`
- `QwenTTS/QwenTTS/State/AppStateStore.swift`

验收标准：

- 每个用户可点击入口都有明确的 handler。
- 每个 handler 的成功、失败、禁用状态都能在 UI 上体现。
- UI 不直接猜测 backend 状态，状态来源应统一进入 `AppStateStore` 或明确的 coordinator。
- 后端不可用时，高风险按钮禁用或提示原因，不允许“点击后无反馈”。

## 2. 前端状态模型

建议把 UI 对用户展示的状态收敛为一组人类可理解的状态：

| 状态 | 用户文案 | 典型触发 |
|---|---|---|
| `notConfigured` | 需要完成配置 | Python/runtime、backend、模型缺失 |
| `starting` | 正在启动后端 | 点击启动或 App 自动启动 |
| `ready` | 准备就绪 | `/health` 正常 |
| `loadingModel` | 正在加载模型 | 首次朗读或切换模型 |
| `speaking` | 正在朗读 | `/read` 后播放中 |
| `paused` | 已暂停 | 用户暂停播放 |
| `generatingPodcast` | 正在生成播客 | podcast worker 运行中 |
| `error` | 需要处理 | backend 崩溃、依赖缺失、API 失败 |

验收标准：

- 菜单栏 popover、主窗口、设置页显示的状态一致。
- 技术状态如 `IDLE`、`BUSY`、`COOLING` 不直接暴露给普通用户。
- 任一状态变化后，主窗口和菜单栏 UI 在 1 秒内同步。
- `error` 状态必须包含下一步操作：查看日志、打开设置、重试、停止后端。

## 3. Mock Backend 验证

为避免每次验证前端都依赖真实 MLX 模型，建议新增 debug/mock backend 模式。它只模拟 HTTP 行为，不做真实推理。

建议模拟 endpoint：

```text
GET  /health
GET  /status
POST /read
POST /pause
POST /resume
POST /stop
GET  /url_jobs
GET  /podcasts/list
GET  /podcasts/jobs
GET  /cache/items
GET  /debug/state
GET  /debug/events
```

需要模拟的状态：

- backend ready
- backend startup timeout
- read accepted
- read failed
- playing
- paused
- podcast queued/running/done/failed
- URL job fetching/parsing/dispatching/failed

验收标准：

- 无模型、无 ffmpeg、无 MLX 环境时，仍能完整验证主要 UI 流程。
- 前端能通过配置或环境变量切到 mock backend。
- mock backend 下，朗读、暂停、继续、停止、URL job、podcast list、cache list 都能驱动 UI 状态变化。
- mock backend 的失败响应能触发真实错误 UI，而不是只在 console 打印。

## 4. UI Smoke Test

建议整理现有临时 UI 测试，把它们变成稳定 smoke test，例如放入 `experiments/ui-smoke/` 或后续正式测试目录。

最小 smoke 流程：

```text
启动 App
-> 菜单栏图标存在
-> 打开主窗口
-> 打开设置页
-> 打开控制台页
-> 启动 backend
-> 状态进入 ready 或 error
-> 点击停止 backend
-> App 未崩溃
```

验收标准：

- smoke test 可以在没有真实模型的机器上运行。
- 失败时能指出具体卡在哪一步，而不是只返回 “test failed”。
- App 启动、打开主窗口、打开设置、停止后端这些路径不能崩溃。
- 每次较大 UI 改动后至少执行一次 smoke test 或对应人工流程。

## 5. 人工验收流程

每次 UI/前端逻辑改动后，至少按以下 checklist 走一遍：

1. 首次启动：缺 Python/runtime、缺模型、缺 ffmpeg 时是否有清楚提示。
2. 后端启动：`starting -> ready` 或 `starting -> error` 是否可见。
3. 后端失败：是否显示错误原因、日志入口、重试入口。
4. 文本朗读：提交文本后，按钮状态、标题、进度是否更新。
5. 暂停/继续/停止：状态是否一致，按钮是否互斥。
6. URL 朗读：是否显示 fetching、parsing、dispatching、failed/done。
7. Podcast：生成、暂停、恢复、完成、失败、播放入口是否清楚。
8. 设置保存：是否提示需要重启 backend，是否避免静默失效。
9. App 退出：backend 进程组是否被结束，日志是否完整。

验收标准：

- 用户执行每一步都能得到可见反馈。
- 没有任何高频操作需要用户打开终端才能知道结果。
- 后端失败不会让 App 卡在“正在启动”超过既定 timeout。
- 退出 App 后没有遗留的 native backend 进程。

## 6. UX 改进方向

### 6.1 菜单栏 Popover

菜单栏只保留高频控制：

- 当前状态
- 当前播放标题
- 进度
- 播放 / 暂停 / 停止
- 朗读剪贴板
- 打开主窗口
- 设置

验收标准：

- popover 不承载复杂配置。
- 播放相关按钮根据状态禁用或切换。
- 当前状态不需要用户打开主窗口也能看懂。

### 6.2 设置页信息架构

建议拆成普通设置和高级设置：

普通设置：

- 模型
- 声音
- 语速
- 默认语言
- Chrome 扩展配对码
- 开机启动

高级设置：

- Python path
- backend path
- MLX path
- models path
- ffmpeg path
- debug logs
- runtime diagnostics

验收标准：

- 普通用户可以不接触 Python 路径也完成基础使用。
- 高级配置保存后明确提示是否需要重启 backend。
- 无效路径在保存前或保存后立即标出。

### 6.3 首次启动 Setup Wizard

建议首次启动引导按步骤检查：

```text
1. Python runtime
2. backend 文件
3. ffmpeg
4. 模型目录
5. 参考音频
6. backend 启动
7. 短句试读
```

验收标准：

- 每一步显示 `通过`、`需要处理` 或 `可跳过`。
- 失败项提供直接入口，例如选择路径、下载模型、打开日志。
- wizard 完成后，用户可以立即进行一次短句朗读。

### 6.4 错误提示规范

错误信息应包含：

```text
发生了什么
可能原因
下一步操作
日志入口
```

示例：

```text
后端启动失败
可能原因：Python 路径无效或依赖缺失。
你可以打开环境设置、查看后端日志，或重新启动后端。
```

验收标准：

- 不向普通用户只展示 exception、traceback 或 HTTP code。
- 错误弹窗或错误区域必须有可执行动作。
- 日志入口可以一键打开或复制路径。

### 6.5 Voice 与 TTS 参数

普通 UI 应优先展示稳定 voice preset：

```text
Serena - 女声，新闻播报
Ryan   - 男声，中文播报
Vivian - 备用声音
```

高级参数如 `seed`、`temperature`、`top_p`、`top_k`、`instruct`、`ref_audio`、`ref_text` 默认隐藏到高级区域。

验收标准：

- 默认 seed 保持锁定，避免声音漂移。
- 切换 Serena/Ryan 时，前端或后端使用匹配的 instruct 与参考音频。
- 普通用户不会被随机参数误导，也不容易破坏声音稳定性。

## 7. 最终验收标准

完成本计划后，前端应满足：

- 没有真实模型时，可以通过 mock backend 验证主要 UI 流程。
- 有真实环境时，可以完成一次从启动、朗读、暂停、继续、停止到退出的完整流程。
- 所有 backend 错误都有用户可理解的提示和下一步动作。
- 菜单栏 popover 能独立完成高频播放控制。
- 设置页把普通选项和高级环境配置分开。
- URL job、podcast、cache 等长任务都有阶段状态。
- App 退出后没有遗留 backend 进程。
- UI 改动有固定 smoke test 或人工验收清单可执行。

推荐最终验证命令：

```bash
cd /Users/funanhe/00_MyCode/TTS/localTTS_macOS/backend
python -m pytest core/tests/ -v

cd ../QwenTTS
xcodegen generate
xcodebuild -project QwenTTS.xcodeproj -scheme QwenTTS CODE_SIGNING_ALLOWED=NO build
```

若当前机器没有完整 Xcode，需要在交付记录中注明 Swift 编译验证未执行，并补充人工 UI 验收结果。
