# Frontend Validation and UX Plan

本文档指导 `localTTS_macOS/QwenTTS/` 原生 macOS 前端的验证与 UX 改进。目标:让前端逻辑可测试、状态可解释、失败可恢复,并且普通用户**不需要理解 Python、FastAPI、MLX、端口和模型路径**也能完成日常使用。

> **文档约定(现状 vs 目标)**
> 每条能力用以下标记区分,避免把"蓝图"当成"现成可跑":
> - `[现状]` 代码里已存在、可直接验证。
> - `[待实现]` 计划目标,当前代码没有,执行前需先建设。
> - `[待核实]` 文档断言,尚未在代码中确认,使用前需先核对。
>
> 标记基于 2026-06-24 对照代码的核对结果(见本文末「附录 A:现状核对」)。

---

## 1. 验证范围与主链路

当前前端主链路:

```text
用户操作
-> AppKit ViewController / SwiftUI View
-> BackendAPIClient
-> BackendProcessManager
-> AppStateStore (单一 snapshot 轮询器写入,订阅者被通知)
-> 菜单栏 popover / 主窗口 / 子页面刷新
```

`[现状]` 重点文件(均已确认存在):

- `StatusBar/StatusItemController.swift`、`StatusBar/PlaybackPopoverController.swift`
- `Windows/MainWindowController.swift`、`Windows/MainSplitViewController.swift`、`Windows/SidebarViewController.swift`
- `Windows/ConsoleViewController.swift`(含 4 段 `modeSegmentedControl`:原文/翻译/总结/双人)
- `Windows/EnvironmentViewController.swift`、`Windows/EngineSettingsViewController.swift`
- `UI/Library/LibraryView.swift`、`UI/Settings/SettingsView.swift`
- `Backend/BackendProcessManager.swift`、`Backend/BackendAPIClient.swift`、`Backend/APIModels.swift`
- `State/AppStateStore.swift`

`[现状]` `MainTabViewController.swift` 仍存在,标注为 **legacy/备用路径**;验收主路径以 `MainSplitViewController` 为准。

**验证范围目标:**

| 检查项 | 通过标准 |
|---|---|
| 主窗口结构 | `MainWindowController` 打开后显示 split view,侧边栏可切换主要页面 |
| 状态来源 | 播放状态只从 `AppStateStore` 派生,不由各页面各自轮询猜测 |
| 错误反馈 | API 失败后 UI 有可见错误、重试或日志入口,不只打印 console |
| 禁用规则 | backend 未就绪时,朗读、暂停、停止、生成播客等不应静默失败 |
| **处理管线** | **翻译/总结/双人 模式、URL→中文 等依赖 LLM/翻译引擎的流程,失败必须前台可见** |

---

## 2. 核心用户流程验收(第一优先级)

这是本计划的核心:验收必须覆盖用户的真实高频流程,而不只是机械播放控制。每条流程写明**步骤 / 目标 / 验证标准 / 主要失败**。

### 流程 A — 短内容粘贴直读 `[现状]`

- **步骤**:复制网页/txt(短)→ 粘贴到 Console 对话框 → 选 `原文` → 点击朗读。
- **后端**:`POST /read`(mode=original)。
- **目标**:无需任何引擎配置即可朗读;状态由 `/snapshot` 驱动。
- **验证标准**:提交后 1 秒内状态变为「正在朗读」,标题/进度来自 `/snapshot`;停止后回到 idle。
- **主要失败**:backend 未 ready → 朗读按钮禁用并显示原因;TTS 不发声 → 显示错误而非静默。

### 流程 B — 长内容清洗/总结后保存 `[现状]`

- **步骤**:粘贴长文 → 选 `翻译` 或 `总结` → 后端经 `reader_service.process_with_llm` 走引擎层 LLM 处理 → 处理结果保存为文章(saved item)。
- **后端**:`POST /read`(mode=translate/podcast-discuss/podcast-trans)→(处理)→ `POST /save_for_later`。
- **目标**:把"处理 → 保存"当作**长任务**对待,过程可见、失败可恢复;**不是瞬时保存**。
- **验证标准**:
  - 处理中有可见的进行态(spinner/状态文案),不是界面假死。
  - 处理成功后文章出现在内容中心。
  - **没配 LLM key 时必须明确提示去「AI 引擎」页配置(无 `.env` fallback)**,而不是静默失败或空结果。
- **主要失败**:无 key / 鉴权失败 / 网络超时 / LLM 返回空。每种都要有可执行提示。
- **状态(阶段 4)**:URL 路径保存后经 `watchJobForFailure` 轮询 job,失败可见;无 key 由 `ensureLLMConfigured` 预检拦截。剩余:纯文本处理的"进行态"spinner 待打磨。

### 流程 C — saved item 稍后处理 `[现状]`

- **步骤**:文章已保存 → 要么直接朗读,要么生成 podcast 存档稍后听。
- **后端**:`POST /play_saved`;或 `POST /generate_single_podcast` / `POST /generate_podcast` →(轮询)`GET /podcasts/jobs` → `POST /podcasts/play`。
- **目标**:从保存内容到朗读/存档全程可见;podcast 生成是后台任务,不阻塞前台。
- **验证标准**:
  - saved item 播放进入朗读态,标题/进度更新。
  - podcast 生成显示 `queued/running/done/failed`;完成后可在播客列表播放。
  - 生成的 podcast 有 `.txt` transcript sidecar(`GET /podcasts/transcript?filename=`)。
- **主要失败**:生成失败显示原因;长时间无进展不应卡死。

### 流程 D — YouTube 网址 → 中文 podcast / 中文总结 `[现状]`

- **步骤**:输入 utube 网址 → 选「中文总结」或「中文 podcast」→ 抓取字幕 → 引擎层处理 → 朗读/存档。
- **后端**:`POST /read_url`(mode=translate 为中文总结;mode=podcast-trans/podcast-discuss 为中文 podcast)→ `GET /url_jobs` 轮询;YouTube 经 `youtube_transcript_api` 抓字幕。
- **目标**:这是用户招牌用法,必须把 **mode 维度** 和 **YouTube 特有失败** 当一等公民验收。
- **验证标准**:
  - URL job 阶段可见:`fetching → parsing → dispatching → done/failed`。
  - mode=translate 产出中文总结文本;mode=podcast-trans/discuss 产出中文双人 podcast。
  - **YouTube 无字幕 / 地区限制 → 明确提示**,不混在通用 "parsing failed" 里。
  - **LLM 无 key / 超时 → 明确提示去配置**。
- **状态(阶段 4)**:§6 已补 URL→中文总结、URL→中文 podcast 两行;无字幕/无 key/超时/鉴权四种失败经 `watchJobForFailure` 分别明确提示(`--smoke-drive-read` 实跑验证)。

---

## 3. 状态模型与映射 `[现状]`

UI 展示状态必须从现有信号推导,不能只靠文案约定。

| 优先级 | 展示状态 | 输入信号 | UI 规则 |
|---:|---|---|---|
| 1 | 需要处理 | `connectionHealthy == false`、`BackendState.failed`、启动超时 | 显示错误原因;提供查看日志、打开环境设置、重试 |
| 2 | 需要完成配置 | Python/runtime/backend/model/ffmpeg 检查失败 | 禁用朗读;显示配置入口 |
| 3 | 正在启动后端 | `BackendState.launching` / `waitingForHealth` | 禁用播放;显示启动进度或超时倒计时 |
| 4 | 正在朗读 | `/snapshot.main_is_playing == true` 且 `is_paused != true` | 显示标题、进度;启用暂停/停止 |
| 5 | 已暂停 | `/snapshot.is_paused == true` | 显示继续/停止;暂停按钮切换为继续 |
| 6 | 正在生成播客 | `/snapshot.active_podcast_processes > 0` 或 `/podcasts/jobs` 有 running/queued | 显示后台任务状态;不误导为前台朗读 |
| 7 | 正在处理 URL/文本 | `/snapshot.active_url_tasks` 非空或 `/url_jobs` 有 running 阶段 | 显示 fetching/parsing/dispatching 或文本处理中 |
| 8 | 后端就绪 | `BackendState.ready` 且无更高优先级状态 | 启用高频操作 |

`[现状]` `BackendState` 枚举确有 `launching/waitingForHealth/ready/failed`;`AppStateStore.connectionHealthy` 与 `reportConnectionError(_:)` 存在。
`[现状]` `/snapshot.status_code` 只有 `IDLE/BUSY/COOLING`(来自 `S.get_status()`),普通 UI 不直接展示,可用于诊断。

**验证标准:**

- 菜单栏 popover、主窗口顶部状态、相关子页面在 1 秒内显示同一用户状态。
- 状态冲突时按上表优先级处理(错误优先于「正在朗读」)。
- `需要处理` 状态必须有下一步动作:查看日志、打开设置、重试或停止后端。

---

## 4. Mock Backend Contract `[现状]`

> **实现情况(2026-06-24 阶段 3)**:`--mock-backend` 已实现 = 内存 `MockBackend`(`Backend/MockBackend.swift`)经 `BackendAPIClient.mock` 拦截 `getJSON`/`postJSON`/PATCH,`BackendProcessManager.startMockBackend()` 跳过 Python 进程直接走 health→ready→snapshot 轮询。不依赖模型/MLX/ffmpeg/音频设备。fixture 可经启动参数切换:
> - `--mock-state=speaking|paused|idle|cooling`(默认 speaking)
> - `--mock-failure=connection|llm_no_key|youtube_no_transcript|engine_timeout|engine_auth_error`

**目标**:无真实模型/MLX/ffmpeg 时,用一个本地 mock backend 驱动 `/snapshot` 等端点,验证 UI 状态机。

**实现步骤(已完成):**

1. `[已完成]` 识别 `--mock-backend`;命中时 `BackendAPIClient.mock` 应答全部请求,不启动真实 backend。
2. `[已完成]` `/health` + `/snapshot`(健康检查返回 ready;snapshot 反映状态机)。
3. `[已完成]` 可切换状态 fixture(idle/speaking/paused/cooling/connection-failure)。
4. `[已完成]` §4.2 / §4.3 页面与长任务端点 stub + §4.4 失败 fixture。

### 4.1 Core Playback Mock

必须支持:`GET /health`、`GET /snapshot`、`POST /read`、`POST /pause`、`POST /resume`、`POST /stop`、`POST /seek`、`POST /control/shutdown`。

`/snapshot` 最小 fixture(注意真实 `/snapshot` 是 **playback + runtime + podcast + url 多段 dict 合并**,mock 需覆盖各段关键 key):

```json
{
  "main_title": "Mock Article",
  "main_progress": "2/8",
  "main_is_playing": true,
  "is_paused": false,
  "status_code": "BUSY",
  "current_article_chunks": ["第一段", "第二段"],
  "current_article_index": 1,
  "active_podcast_processes": 0,
  "active_url_tasks": [],
  "instance_id": "mock-backend"
}
```

需要的状态 fixture:idle / speaking / paused / cooling(`status_code=COOLING`) / connection failure(`/snapshot` 超时或 500)。

**验证标准:**

- mock 模式不依赖模型、ffmpeg、MLX 或真实音频设备。
- 切换 fixture 后,`AppStateStore`、popover、控制台在 1 秒内更新。
- `/snapshot` 失败触发 `reportConnectionError` 对应 UI,而非只打印 console。

### 4.2 Library / Settings / Engines Mock

端点:`GET/PATCH /settings`、`GET/PATCH /engines`、`POST /engines/check`、`POST /save_for_later`、`GET /saved_items`、`POST /play_saved`、`POST /delete_saved`、`POST /saved_items/clear`。

**验证标准:**设置可加载/修改/保存并提示重启;引擎页能展示 provider、保存 key、显示 `/engines/check` 成功/失败;内容中心能展示 saved items 并对播放/删除/清空有可见反馈。

### 4.3 URL / Podcast / Cache Mock

端点:`POST /read_url`、`GET /url_jobs`、`POST /generate_single_podcast`、`POST /generate_podcast`、`GET /podcasts/list`、`GET /podcasts/jobs`、`GET /podcasts/transcript?filename=`、`POST /podcasts/play`、`POST /podcasts/delete`、`POST /podcasts/toggle_pin`、`POST /podcasts/clear`、`GET /cache/items`、`POST /cache/play`、`POST /cache/export`、`POST /cache/delete`、`POST /cache/clear`、`GET /debug/state`、`GET /debug/events`。

**验证标准:**

- URL job 至少覆盖 `fetching/parsing/dispatching/done/failed`。
- podcast jobs 至少覆盖 `queued/running/done/failed/canceled`。
- cache/podcast 的 play/delete/clear 成功后列表刷新,失败有错误反馈。

### 4.4 文本处理 Job 与失败 Fixture `[现状]`(对应流程 B/D)

为覆盖用户招牌流程,mock 提供:

- `[现状]` **失败 fixture(mock 侧已实现)**,经 `--mock-failure=` 注入:
  - `connection`:`/snapshot` 返回 500 → 触发 `AppStateStore.reportConnectionError`。
  - `llm_no_key`:`/engines/check`、`/url_jobs`、`/podcasts/jobs` 返回"未配置 key,请在「AI 引擎」页配置"。
  - `youtube_no_transcript`:`/url_jobs` 以 `stage=failed` + "该视频没有可用字幕(或地区受限)"明确失败,而非通用 parsing failed。
  - `engine_timeout` / `engine_auth_error`:超时与鉴权失败两种独立文案。
- `[现状]` **前台消费这些失败**(阶段 4 实现):`ConsoleViewController` 提交后用 `watchJobForFailure` 轮询 `/url_jobs`、`/podcasts/jobs`,对本次新失败 job 调 `surfaceActionableError`;引擎/密钥类错误附带「打开 AI 引擎」按钮(经 `Notification.Name.qwenShowEngineSettings` 跳转)。无字幕/无 key/超时/鉴权四种失败已用 `--smoke-drive-read` 实跑验证产生正确 `[ConsoleError]` 文案。
- `[现状]` mock 字段已与真实后端对齐(url_jobs:`status`/`stage`/`error`/`job_id`;job 在 dispatch 后才出现)。
- `[待实现]` **纯文本处理 job 的"进行态"显示**(translate/podcast-discuss 处理中的 spinner):无 key 由预检 `ensureLLMConfigured` 提前拦截,中途失败已由 podcast-job 轮询覆盖;进行态指示留作后续打磨。

**验证标准:**`[已满足]` mock 每个失败 fixture 返回对应错误;前台对每个失败产生**可执行提示**(打开 AI 引擎页 / 重试),不停在静默或纯 console——`--smoke-drive-read` 下四种失败均输出对应 `[ConsoleError]`,无失败时静默。

---

## 5. UI Smoke Test 路径

`[现状]` 已有 `--smoke-test` 和 `--dump-ui`(`AppDelegate.swift`);smoke 路径已分步验**菜单初始化 → 主窗口可见 → 侧边栏导航(内容中心)**,并依次输出 `SMOKE_MENU_READY` / `SMOKE_MAIN_WINDOW_VISIBLE` / `SMOKE_SIDEBAR_NAV_OK` / `SMOKE_TEST_PASSED`;任一步失败输出 `SMOKE_TEST_FAILED: <原因>`,并有 30s 安全网强制退出。侧边栏导航由共用辅助 `smokeNavigateSidebar(to:)` 实现(smoke 与 dump-ui 共享)。
`[现状]` `--mock-backend` 与 `SMOKE_SNAPSHOT_SYNC_OK` 均已实现:mock 驱动 `/snapshot` 后,smoke 轮询确认 snapshot 已流入 `AppStateStore`(`instance_id == "mock-backend"`)才输出该 marker;非 mock 的普通 smoke 跳过此步。speaking/paused/idle/cooling 四种 fixture 实跑均通过。

**目标命令(均可直接运行):**

```bash
cd /Users/funanhe/00_MyCode/TTS/localTTS_macOS/QwenTTS
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer   # 用完整 Xcode 编译
xcodegen generate
xcodebuild -project QwenTTS.xcodeproj -scheme QwenTTS -derivedDataPath ../build/DerivedData CODE_SIGNING_ALLOWED=NO build

APP="../build/DerivedData/Build/Products/Debug/QwenTTS.app/Contents/MacOS/QwenTTS"
"$APP" --smoke-test --mock-backend                       # 完整 4+1 marker 链
"$APP" --smoke-test --mock-backend --mock-state=paused   # 切 fixture
"$APP" --dump-ui --mock-backend                          # mock 下 dump UI 层级
```

**扩展步骤:**

1. `[已完成]` 让 `--smoke-test` 分步打印 marker(见下表),失败即 `SMOKE_TEST_FAILED: <原因>` 并 30 秒内退出。
2. `[已完成]` 加侧边栏导航步骤(当前切到「内容中心」;后续可扩展到设置/AI 引擎/控制台)。
3. `[已完成]` 接入 `--mock-backend`,用 §4 fixture 驱动状态,补 `SMOKE_SNAPSHOT_SYNC_OK`(四种 fixture 实跑通过)。

最小 smoke 流程:

```text
启动 App -> 菜单初始化 -> 打开主窗口 -> split view 可见
-> 侧边栏切到内容中心/设置/AI 引擎/控制台
-> mock /snapshot 驱动 speaking/paused/idle 三态
-> 停止 backend / 退出 App -> stdout 输出明确 marker
```

| 步骤 | 通过 marker | 状态 |
|---|---|---|
| 菜单初始化 | `SMOKE_MENU_READY` | `[现状]` |
| 主窗口可见 | `SMOKE_MAIN_WINDOW_VISIBLE` | `[现状]` |
| 侧边栏切换成功 | `SMOKE_SIDEBAR_NAV_OK` | `[现状]` |
| snapshot 状态同步 | `SMOKE_SNAPSHOT_SYNC_OK` | `[现状]`(`--mock-backend` 下) |
| 退出无崩溃 | `SMOKE_TEST_PASSED` | `[现状]` |

失败必须输出 `SMOKE_TEST_FAILED: <具体原因>` 并 30 秒内退出。

---

## 6. 逐入口验收表

每个高频入口都应按表验证,避免"状态一致""按钮禁用"等主观说法。

| 入口 | 前置状态 | 动作 | 期望 API | 期望 UI | 失败 fallback | 状态 |
|---|---|---|---|---|---|---|
| 菜单栏打开主窗口 | App 已启动 | 点「打开主窗口」 | 无 | `QwenTTS 控制台` 可见 | 输出 window 未创建原因 | `[现状]` |
| 朗读文本(原文) | `ready` | 提交文本 | `POST /read` (original) | 进入朗读;标题/进度来自 `/snapshot` | 显示错误,保留输入 | `[现状]` |
| **翻译/总结(粘贴文本)** | `ready` | 选翻译/总结+提交 | `POST /read` (translate/podcast-*) | 处理进行态→朗读/保存 | **无 key→预检弹窗引导 AI 引擎页(`ensureLLMConfigured`);播客渲染失败→job 轮询提示** | `[现状]` |
| 暂停 | `speaking` | 点暂停 | `POST /pause` | 按钮切换为继续;`is_paused=true` | 显示暂停失败 | `[现状]` |
| 继续 | `paused` | 点继续 | `POST /resume` | 回到正在朗读 | 显示继续失败 | `[现状]` |
| 停止 | `speaking/paused` | 点停止 | `POST /stop` | 回到 ready/idle | 显示停止失败 | `[现状]` |
| URL 朗读 | `ready` | 提交 URL(原文) | `POST /read_url` + `GET /url_jobs` | 显示阶段状态 | 显示失败阶段和原因 | `[现状]` |
| **URL→中文总结** | `ready` | 提交 URL+选总结 | `POST /read_url` (translate) | 阶段状态→中文总结文本 | **无字幕/无 key/超时/鉴权→`watchJobForFailure` 分别明确提示** | `[现状]` |
| **URL→中文 podcast** | `ready` | 提交 URL+选双人/翻译 | `POST /read_url` (podcast-trans/discuss) | 阶段状态→中文双人 podcast | **无字幕/无 key/超时/鉴权→分别明确提示(url+podcast 双 job 监听)** | `[现状]` |
| 保存稍后朗读 | `ready` | 保存文本 | `POST /save_for_later` | saved list 增加/刷新 | 显示保存失败 | `[现状]` |
| 播放 saved item | saved 非空 | 点播放 | `POST /play_saved` | 进入朗读或队列 | 显示播放失败 | `[现状]` |
| 生成播客 | 有文本/保存项 | 点生成 | `POST /generate_*` + `GET /podcasts/jobs` | 显示 queued/running/done | 显示失败原因 | `[现状]` |
| 播放播客 | 有 podcast 文件 | 点播放 | `POST /podcasts/play` | 播放标题/进度更新 | 显示播放失败 | `[现状]` |
| cache 播放/删除 | cache 非空 | 点操作 | `/cache/*` | 列表刷新或播放更新 | 显示操作失败 | `[现状]` |
| 保存设置 | 设置页打开 | 点保存 | `PATCH /settings` | 成功提示;需要时提示重启 | 标出失败字段或错误 | `[现状]` |
| 引擎检查 | AI 引擎页 | 点检查 | `POST /engines/check` | 显示 provider 检查结果 | 显示后端/网络/鉴权错误 | `[现状]` |

**验证标准:**

- 表中每个入口至少有 mock 或人工验证方式。
- 所有失败 fallback 必须是**用户可见反馈**,不能只写日志。
- backend 未 ready 时,会调用 API 的入口必须禁用或显示原因。
- **依赖引擎层的入口(翻译/总结/双人、URL→中文)在无 key 时必须引导到「AI 引擎」页配置。**

---

## 7. 人工验收流程

每次 UI/前端逻辑改动后,至少按步骤执行(每步都要有可见反馈):

1. **首次启动**:缺 Python/runtime、缺模型、缺 ffmpeg 时提示是否清楚。
2. **后端启动**:`launching/waitingForHealth → ready` 或 `failed` 是否可见。
3. **后端失败**:是否显示错误原因、日志入口、重试入口。
4. **短文朗读(流程 A)**:提交后按钮状态、标题、进度是否由 `/snapshot` 更新。
5. **暂停/继续/停止**:状态和按钮互斥是否正确。
6. **翻译/总结(流程 B)**:处理进行态是否可见;**无 LLM key 时是否引导去 AI 引擎页**;结果是否正确保存。
7. **URL→中文(流程 D)**:是否显示 fetching/parsing/dispatching/done/failed;**YouTube 无字幕、LLM 无 key 是否分别明确提示**。
8. **Podcast(流程 C)**:生成、暂停、恢复、完成、失败、播放入口是否清楚;transcript sidecar 是否存在。
9. **设置保存**:是否提示需要重启 backend,是否避免静默失效。
10. **App 退出**:backend 进程组是否被结束,日志是否完整。

**验证标准:**

- 每步都能得到可见反馈;高频操作不需打开终端才能知道结果。
- 后端启动失败不会让 App 卡在「正在启动」超过 30 秒。
- 退出 App 后没有遗留本次启动的 native backend 进程。

---

## 8. UX 改进方向

### 8.1 菜单栏 Popover `[现状]`
只保留高频控制:当前状态、播放标题、进度、上一/下一段、播放/暂停/停止、朗读剪贴板、**打开主窗口、设置**、打开播客目录。
**状态(阶段 5)**:`PlaybackPopoverController` 已补「打开主窗口」「设置」入口(后者经修复后的 `coordinator.openSettings()` 切到活跃 split 的设置页)。`updateUI` 已按状态切换播放/暂停/继续。
**验证标准**:不承载复杂配置;播放按钮按状态切换;状态不打开主窗口也能看懂。

### 8.2 设置页信息架构 `[现状]`
拆成**普通设置**(声音、性能模式、电池策略等)与**高级设置**(`temperature/top_p/rep_penalty/seed` 折叠在 DisclosureGroup;Python/MLX/binaries 单独的 Advanced 配置卡)。
**状态(阶段 5 + 复审修正)**:`SettingsView` 已有 `showAdvanced` 折叠 + Advanced Engine / Advanced 配置卡,普通区不暴露 Python 路径。**保存接线已补**:进入设置页经 `GET /settings` 回填(`loadSettings`),「保存设置」按钮经 `PATCH /settings`(带管理令牌)写回(`saveSettings`),覆盖 voice / performance_profile(fast/balanced/quiet 映射)/ temperature / top_p / repetition_penalty / seed / battery_podcast_policy;保存后提示"可能需重启后端生效"。(此前为纯本地 `@State`,复审指出未接线已修复。)
**验证标准**:普通用户不接触 Python 路径即可完成基础使用;改动经「保存设置」写回后端并提示重启;`xcodebuild` 通过、smoke 回归通过。

### 8.3 首次启动 Setup Wizard `[现状]`
分步检查并在首次启动展示(`SetupWizardViewController`/`SetupWizardWindowController`,coordinator 首启拉起)。
**状态(阶段 5)**:已覆盖 macOS 兼容性 / Apple Silicon / 磁盘空间 / 模型状态检查 + 模型下载入口。
**剩余打磨**:ffmpeg / 参考音频 / 短句试读 步骤未单列。
**验证标准**:每步显示 `通过/需要处理`;失败项提供直接入口(下载模型等);完成后可进入主界面。

### 8.4 错误提示规范
错误信息应含:发生了什么 / 可能原因 / 下一步操作 / 日志入口。示例:

```text
后端启动失败
可能原因:Python 路径无效或依赖缺失。
你可以打开环境设置、查看后端日志,或重新启动后端。
```

**验证标准**:不向普通用户只展示 exception/traceback/HTTP code;错误区域必须有可执行动作;日志入口可一键打开或复制路径。

> **针对流程 B/D 的专项规范**:引擎层失败必须区分并给出对应动作 —
> 「未配置 LLM key」→ 按钮直达「AI 引擎」页;「YouTube 无字幕」→ 说明该视频无可用字幕;「网络/鉴权失败」→ 提示重试或检查 key。

### 8.5 Voice 与 TTS 参数 `[现状]`
普通 UI 优先展示稳定 preset;高级参数(`seed/temperature/top_p/top_k/instruct/ref_audio/ref_text`)默认隐藏在折叠区。
**状态(阶段 5 + 复审修正)**:`SettingsView` voice picker 已含 Serena(女声/新闻)、Ryan(男声/中文)、Vivian(备用),带中文说明;高级参数在 DisclosureGroup 内,seed 默认 "42"。voice/temperature/top_p/seed 等改动现经「保存设置」真正写回后端(见 §8.2,此前不同步)。
**验证标准**:默认 seed 锁定避免声音漂移;切换 voice 经保存后后端使用匹配 instruct/参考音频;普通用户不被随机参数误导。

---

## 9. 实施步骤(分阶段路线图)

按依赖与价值排序,建议如下顺序推进:

**阶段 1 — 现状对齐与标注(无需写码)** ✅ 已完成(2026-06-24)
- 目标:文档与代码一致,执行者不踩"蓝图当现成"的坑。
- 结果:`[现状]/[待实现]` 标注已核对;500ms 轮询已确认为 `startSnapshotPolling()` 真实周期,`[待核实]` 取消(见附录 A)。

**阶段 2 — Smoke 标记细化(纯前端,低风险)** ✅ 已完成(2026-06-24)
- 目标:`--smoke-test` 分步输出 `SMOKE_MENU_READY/…/SMOKE_TEST_PASSED`。
- 结果:`AppDelegate` smoke 路径已补菜单/主窗口/侧边栏三步 marker + 30s 安全网,抽出共用 `smokeNavigateSidebar(to:)`;`xcodebuild` 通过,实跑依序输出全部 marker。
- 剩余:`SMOKE_SNAPSHOT_SYNC_OK` 留待阶段 3(需 mock backend)。

**阶段 3 — Mock Backend(解锁离线验证)** ✅ 已完成(2026-06-24)
- 目标:实现 `--mock-backend`,按 §4 提供 fixture(含 §4.4 失败 fixture)。
- 结果:新增 `Backend/MockBackend.swift`(内存状态机 + 全端点应答 + `--mock-state`/`--mock-failure` 注入);`BackendAPIClient.mock` 拦截网络层;`BackendProcessManager.startMockBackend()` 跳过 Python 直连 health→ready→snapshot;`AppDelegate` 补 `SMOKE_SNAPSHOT_SYNC_OK`。`xcodebuild` 通过,speaking/paused/idle/cooling 四态实跑均输出完整 marker 链。
- 剩余:失败 fixture 的**前台消费**与文本处理 job 阶段归入阶段 4(mock 侧已就绪)。

**阶段 4 — 用户流程 B/D 的前台覆盖(核心价值)** ✅ 已完成(2026-06-24)
- 目标:翻译/总结、URL→中文总结/podcast 成为一等入口,失败可见可恢复。
- 结果:`ConsoleViewController` 加 `surfaceActionableError` + `failedJobIDs`/`watchJobForFailure`(提交后轮询 url/podcast jobs,只对本次新失败告警);引擎/密钥错误经 `Notification.Name.qwenShowEngineSettings` → `SidebarViewController.selectTab(3)` 跳转 AI 引擎;mock job 字段对齐真实 schema 且 dispatch 后才出现;新增 `--smoke-drive-read` 驱动钩子。
- 验证:`xcodebuild` 通过;`--mock-failure=youtube_no_transcript|llm_no_key|engine_timeout|engine_auth_error` + `--smoke-drive-read` 四种失败均实跑输出正确 `[ConsoleError]` 文案,无失败时静默。
- 剩余:纯文本处理"进行态"指示(spinner)留作打磨。

**阶段 5 — UX 信息架构** ✅ 已完成(2026-06-24)
- 目标:菜单栏精简、设置普通/高级分离、(可选)Setup Wizard。
- 结果:popover 补「打开主窗口」「设置」入口并修复 `openSettings()`(原指向已弃用的 `MainTabViewController`,改为 `MainSplitViewController.settingsTabIndex`);新增 `MainSplitViewController.selectTab`/`MainWindowController.selectTab` 转发链;§8.2 普通/高级分离与 §8.3 Setup Wizard 经核查已存在;§8.5 voice picker 补全 Serena/Ryan/Vivian + 中文说明。`xcodebuild` 通过,smoke 回归通过。
- 复审修正(2026-06-24):① `SettingsView` 原为纯本地 `@State`、改动不落库 → 已接 `GET/PATCH /settings`(load 回填 + 「保存设置」写回,带管理令牌);② `MainSplitViewController` 的 block-based 通知观察者未保存 token、`removeObserver(self)` 无法移除 → 已存 `engineObserver` token 并在 deinit 精确移除。
- 剩余打磨:Setup Wizard 的 ffmpeg/参考音频/短句试读步骤、纯文本处理进行态 spinner。

---

## 10. 最终验收标准

完成本计划后,前端应满足:

- 无真实模型时,可用 `--mock-backend` 验证主窗口、菜单栏、播放、设置、saved items、URL、podcast、cache 的主要 UI 流程。
- 有真实环境时,可完成一次"启动 → 朗读 → 暂停 → 继续 → 停止 → 退出"完整流程。
- **用户核心流程 A–D 全部可验证**,其中 B/D 的引擎层失败(无 key/无字幕/超时/鉴权)都有可执行提示。
- `/snapshot` 是播放状态与连接健康度验证的核心 contract。
- 所有 backend 错误都有用户可理解的提示和下一步动作。
- 菜单栏 popover 能独立完成高频播放控制。
- 设置页把普通选项与高级环境配置分开。
- URL job、文本处理、podcast、cache 等长任务都有阶段状态。
- App 退出后没有遗留 backend 进程。
- UI 改动有固定 smoke test 或人工验收清单可执行。

**推荐验证顺序:**

```bash
cd /Users/funanhe/00_MyCode/TTS/localTTS_macOS/backend
python -m pytest core/tests/ -v

cd ../QwenTTS
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
xcodegen generate
xcodebuild -project QwenTTS.xcodeproj -scheme QwenTTS -derivedDataPath ../build/DerivedData CODE_SIGNING_ALLOWED=NO build

# --mock-backend 已实现:离线驱动 UI,无需真实模型/后端
APP="../build/DerivedData/Build/Products/Debug/QwenTTS.app/Contents/MacOS/QwenTTS"
"$APP" --smoke-test --mock-backend
"$APP" --dump-ui --mock-backend
```

人工补充(退出 App 后不应残留本次启动的 backend):

```bash
pgrep -af "backend/core/backend.py|PythonRuntime/bin/python3"
```

若当前机器无完整 Xcode,需在交付记录注明 Swift 编译验证未执行,并补充人工 UI 验收结果。

---

## 附录 A:现状核对(2026-06-24)

对照代码核对结论,作为上文 `[现状]/[待实现]/[待核实]` 标注依据:

**已确认存在(`[现状]`)**
- §1 列出的 14 个 Swift 文件全部存在;`MainTabViewController.swift` 仍在(标 legacy)。
- `BackendState` 枚举含 `launching/waitingForHealth/ready/failed`;`AppStateStore.connectionHealthy`、`reportConnectionError(_:)` 存在。
- `/snapshot` 字段全部真实:`main_title/main_progress/main_is_playing/is_paused/status_code/current_article_chunks/current_article_index/active_url_tasks/instance_id`;`active_podcast_processes` 来自 `podcast_service.snapshot()`,经 `/snapshot` 合并。
- `status_code` 取值只有 `IDLE/BUSY/COOLING`(`S.get_status()`)。
- §4/§6 列出的端点均存在于 `backend/core/backend.py`。
- `POST /read` 与 `POST /read_url` 均带 `mode`(original/translate/podcast-discuss/podcast-trans);YouTube 经 `youtube_transcript_api` 抓字幕;处理经 `reader_service.process_with_llm` → 引擎层。
- Console 顶部有 4 段 `modeSegmentedControl`(原文/翻译/总结/双人)。
- `--smoke-test`、`--dump-ui` 存在;smoke 现已分步输出 `SMOKE_MENU_READY → SMOKE_MAIN_WINDOW_VISIBLE → SMOKE_SIDEBAR_NAV_OK → SMOKE_TEST_PASSED`,失败输出 `SMOKE_TEST_FAILED: <原因>`,带 30s 安全网。**(2026-06-24 阶段 2 实现并实跑验证通过)**
- "前端每 500ms 轮询 `/snapshot`":已确认 = `BackendProcessManager.startSnapshotPolling()`(行 249-255),注释"唯一轮询器:500ms 频率",`Task.sleep(.milliseconds(500))`。**(阶段 1 核实,断言属实)**

**阶段 3 新增(2026-06-24,`[现状]`)**
- `--mock-backend` + `Backend/MockBackend.swift`(全端点 fixture、`--mock-state`/`--mock-failure` 注入)。
- `SMOKE_SNAPSHOT_SYNC_OK`(mock 驱动 `/snapshot` 后 smoke 确认已同步进 `AppStateStore`)。
- §4.4 失败 fixture(connection/llm_no_key/youtube_no_transcript/engine_timeout/engine_auth_error)在 mock 侧已实现。

**阶段 4 新增(2026-06-24,`[现状]`)**
- `ConsoleViewController`:`surfaceActionableError` + `watchJobForFailure`/`failedJobIDs`,提交后轮询 url/podcast jobs 并对本次新失败给出可执行提示。
- 跨页跳转:`Notification.Name.qwenShowEngineSettings` + `SidebarViewController.selectTab(_:)`(MainSplitViewController 观察)。
- `--smoke-drive-read` 驱动钩子;mock job 字段对齐 + dispatch 后才出现。四种失败 fixture 实跑产生正确 `[ConsoleError]`。

**阶段 5 新增/核实(2026-06-24,`[现状]`)**
- popover「打开主窗口」「设置」入口;修复 `openSettings()` 指向活跃 `MainSplitViewController`;`selectTab` 转发链。
- §8.2 设置普通/高级分离、§8.3 Setup Wizard(`SetupWizardViewController`)经核查已存在;§8.5 voice 补 Vivian。

**仅剩纯打磨项(`[待实现]`,非阻塞)**
- 纯文本处理的"进行态"spinner。
- Setup Wizard 的 ffmpeg/参考音频/短句试读步骤单列。
