# QwenTTS 改进计划（Step-by-Step）

> 本文档由一次跨子系统的代码审查（Swift AppKit / Python backend core / 服务&引擎层 / 构建打包）汇总而成。
> 每个步骤包含：**问题**（含 `file:line`）、**改进方式**、**验收标准**。
> file:line 来自静态审查，动手改前请先 Read 实际代码二次确认（行号可能因后续改动漂移）。

## 实施进度总览（截至本轮 /loop）

全程后端 pytest 由 18 → **34 通过**；每步改后自检。

| 阶段 | 状态 | 备注 |
|------|------|------|
| **P0 发布阻断**（5/5） | ✅ 全完成 | entitlements、依赖锁、签名去 `--deep`、Python 下载校验、ffmpeg 校验。⚠️ codesign/公证/干净机启动需实机门禁 |
| **P1A/P1B/P1C** | ✅ 完成 | 状态单一真相源、async 边界、Swift `@MainActor` 隔离。**已用 Xcode 26.5（`DEVELOPER_DIR` 覆盖）编译通过，无 actor/隔离报错**；运行 Debug 构建实测 `/health` 公开(P2.2)、`/seek` 401(P2.1) |
| **P1D** | 🟡 部分 | 2/3/4（单一 reaper+锁、重启时间戳）✅ 已编译通过；issue1 port-0 的 **Python 侧 ✅**、**Swift 发现侧仍待实施**（端口发现重排尚未写） |
| **P2 安全**（6/6） | ✅ 全完成 | 默认拒绝鉴权、/health 公开、SSRF、testclient 收紧、HTTP 状态码、探活省 token |
| **P3.1 死代码** | ✅ 完成 | 删 gemini_engine/worker/build_runtime + 别名；swiftc 回退分支(构建路径)延后 |
| **P3.2 DRY** | 🟡 部分 | schema 三处合一 ✅；ProviderRouter/REF_VOICES 需 key/模型验证，延后 |
| **P3.3 分层** | ✅ Python 完成 | legacy 路径移除、paths 惰性迁移；BackendProcessManager 拆分(Swift)延后 |
| **P4.1–4.7** | ✅ 完成 | load_state 抗损坏、SQLite WAL、job 防清空、缓存 DB 权威、profile 命名、裸 except、pmset/日志优化 |
| **P4.8 测试** | ✅ 本环境部分 | 修电池测试 + /engines 鉴权 + paths 覆盖；跨进程看门狗/打包 CI 需真实环境 |

**剩余项原因**：需真实 provider API key（ProviderRouter 验证）、TTS 模型文件（REF_VOICES）、或可启动后端的 CI（打包/签名/公证/跨进程看门狗）。**Swift 编译已可用**（Xcode 26.5 在 `/Applications/Xcode.app`，用 `DEVELOPER_DIR` 覆盖即可，见 memory `build-app-with-xcode`）；P1D 的 Swift 端口发现重排是"尚未写实现"而非"无法编译"。

## 总体判断

架构意图正确（AppKit ↔ FastAPI 经 HTTP + 独立进程组 + 看门狗管道 + 管理 token 解耦），但有三类系统性问题：

1. **并发契约靠"巧合"成立** —— Swift 侧 `@MainActor` 隔离缺失、Python 侧 async 边界被阻塞 I/O 击穿。
2. **持久化状态有两个真相源** —— `state.json` 被 GET 请求、TTS 线程、WAV 线程同时读改写。
3. **发布管线有两个必崩缺陷** —— 缺 entitlements（公证必失败）+ 依赖锁过期（漏掉 anthropic/openai）。

**当前结论：不可发布。** 多数问题改动量小，无需重构架构。

## 执行顺序总览

| 阶段 | 主题 | 为什么先做 |
|------|------|-----------|
| **P0** | 发布阻断项（打包/签名/依赖） | 不修则无法分发，且彼此独立可快速清掉 |
| **P1** | 正确性与并发（数据竞争/丢更新/崩溃） | 收益最高，消除整类竞态，为后续打基础 |
| **P2** | 安全加固 | 一次中间件重写覆盖大半 |
| **P3** | 架构清理与死代码 | 降低长期维护成本 |
| **P4** | 数据安全 / 持久化 / 代码质量 / 测试 | 收尾健壮性 |

建议顺序：P0 → P1A + P1B → P1C/D → P2 → P3 + P4。

---

# P0 — 发布阻断项

## P0.1 补 Hardened Runtime entitlements ✅ 已完成

> 已建 `QwenTTS/QwenTTS/QwenTTS.entitlements`（4 键）；`package_release.py` 的 `sign_app` 对所有 Mach-O（含 `PythonRuntime/bin/python3`）带 `--entitlements` 签名；`project.yml` 加 `CODE_SIGN_ENTITLEMENTS` + `ENABLE_HARDENED_RUNTIME` 并已 `xcodegen generate`。本地已验 plist 合法 + xcodeproj 设置生效；`codesign -d`/公证/干净机器启动需实机构建+证书，留作发布前手动门禁。

- **问题**：`sign_app`（`package_release.py:225-238`）用 `--options runtime` 签名却从不传 `--entitlements`，仓库内无任何 `.entitlements` 文件。内嵌 Python + MLX 使用 JIT/动态代码，Apple 公证会拒绝，或在非构建机首启崩溃。
- **改进方式**：
  1. 新建 `QwenTTS.entitlements`，至少包含：
     - `com.apple.security.cs.allow-jit`
     - `com.apple.security.cs.allow-unsigned-executable-memory`
     - `com.apple.security.cs.disable-library-validation`
     - `com.apple.security.cs.allow-dyld-environment-variables`（launcher 注入 `PYTHONHOME`/`DYLD`，见 `BackendLauncher.swift:92`）
  2. 外层 bundle 签名步骤加 `--entitlements QwenTTS.entitlements`。
  3. `QwenTTS/project.yml` 设置 `CODE_SIGN_ENTITLEMENTS`。
- **验收标准**：
  - `codesign -d --entitlements - QwenTTS.app` 列出上述 4 个键。
  - `codesign --verify --deep --strict QwenTTS.app` 通过。
  - 在一台**未参与构建**的 Apple Silicon Mac 上启动 app，后端进程正常拉起、`/health` 返回 200。

## P0.2 重新生成并校验依赖锁 ✅ 已完成

> 已 `uv pip compile` 重生成 `requirements.prod.lock`：`anthropic==0.112.0`、`openai==2.43.0`（+ `distro`/`jiter`/`sniffio`）已入 lock，共 62 包；header 改相对路径消除绝对路径泄漏。CI drift 检查命令（就地 recompile 后比 git diff，prefer-existing 下为 no-op）：
> ```bash
> uv pip compile requirements.prod.txt -o requirements.prod.lock && git diff --exit-code requirements.prod.lock
> ```
> ⚠️「从 lock 全量安装后 import」留待实际打包（`create_python_runtime`）时验证。

- **问题**：`requirements.prod.txt:16-17` 声明 `anthropic>=0.111.0`、`openai>=2.36.0`，但 `requirements.prod.lock` 不含它们（及 `distro`/`jiter`/`sniffio`/`httpx-sse`）。发布版 runtime 从 lock 安装（`package_release.py:180`），将**静默缺失 Claude/OpenAI provider**。
- **改进方式**：
  1. `uv pip compile requirements.prod.txt -o requirements.prod.lock` 并提交。
  2. 加 CI 检查：重新 compile 后 `git diff --exit-code requirements.prod.lock`，有差异即失败。
  3. （可选）去掉 lock 头部的绝对机器路径 `/Users/funanhe/...`。
- **验收标准**：
  - `grep -E '^(anthropic|openai)' requirements.prod.lock` 均命中。
  - 从 lock 安装的 runtime 中 `python -c "import anthropic, openai"` 成功。
  - CI lock 漂移检查通过。

## P0.3 修正签名方式（去掉最外层 `--deep`）✅ 已完成

> `sign_app` 最外层 bundle 改为不带 `--deep` 签名（内→外逐个签已覆盖嵌套 Mach-O），保留 `--verify --deep --strict` 校验。本地已过语法检查；`codesign --verify` 实机校验留作发布前门禁。

- **问题**：`package_release.py:237` 对 bundle 用 `codesign --deep`，对内嵌 Python runtime 不可靠且被 Apple 弃用；在已逐个签内层后再 `--deep` 重签会破坏封缄。
- **改进方式**：保留内→外逐个签名循环（`:230-236`），最外层 bundle 改为**不带 `--deep`**、带 `--entitlements` 单独签；保留 `--verify --deep --strict` 校验。
- **验收标准**：`codesign --verify --deep --strict --verbose=2 QwenTTS.app` 无 warning/error。

## P0.4 加固 standalone-Python 下载 ✅ 已完成

> 版本/URL/SHA256 提为顶部常量（`PYTHON_BUILD_TAG`/`PYTHON_VERSION`/`PYTHON_RUNTIME_SHA256`）；下载后比对 pinned SHA256（不符则删文件并报错），`_safe_extractall` 用 `filter='data'`（带旧版本手动校验回退）防路径穿越；顺带消除两处硬编码 `lib/python3.11`。已单测：hash 比对 / 篡改检测 / 路径穿越拦截全过。

- **问题**：`package_release.py:142-150` 硬编码旧版 python-build-standalone URL（`20240107`, cpython 3.11.7），下载无 SHA256 校验，`tar.extractall` 无成员过滤（路径穿越风险）。
- **改进方式**：
  1. 版本/URL 提为顶部常量并注释。
  2. 下载后对照官方 `.sha256` 校验。
  3. `tar.extractall(filter='data')`（Py 3.12+）或手动 member 校验。
- **验收标准**：篡改/截断 tarball 时校验失败并中止；正常路径解包后 runtime 可运行。

## P0.5 校验并打包 ffmpeg 依赖 ✅ 已完成

> 新增 `bundle_ffmpeg`：`lipo -archs` 验 arm64；`otool -L` 检出任何非 `/usr/lib`、`/System` 的依赖即**报错中止**并提示用 `TTS_FFMPEG_PATH` 指向静态构建。已验证 Homebrew ffmpeg（55 个非可移植 dylib）被正确拒绝、系统二进制无误报。决策：不递归打包 Homebrew dylib 树（对构建脚本过于脆弱），改为强制使用静态 ffmpeg。⚠️ 干净机 `ffmpeg -version` 需实机验证。

- **问题**：`package_release.py:130-138` 仅从 host PATH 拷贝单个 `ffmpeg` 二进制，未验架构、未打包其动态依赖 dylib（Homebrew 版链接 `/opt/homebrew/...`，用户机不存在）。
- **改进方式**：`lipo -archs` 确认 `arm64`；优先使用静态 ffmpeg 构建，或 `otool -L` 找出依赖 dylib 一并打包并 `install_name_tool` 修 rpath；拷贝后重签。
- **验收标准**：在无 Homebrew 的干净机上 `Tools/ffmpeg -version` 正常输出；`otool -L` 无指向 `/opt/homebrew` 的绝对路径。

---

# P1 — 正确性与并发

## P1A 统一持久化状态的单一真相源 ✅ 已完成（含一次回归修复）⭐

> **⚠️ 回归修复（运行真机抓到）**：初版把 snapshot 的 `current_article_index`/`main_progress` 改为读 `RuntimeState.main_index`，但只有保存-WAV 路径更新 main_index，**流式 `/read` 路径不更新**——导致 live-read 时 karaoke 索引卡 0 且进度显示上次播放残留的 stale 值。已改回读 `player.currently_playing_index`（音频回调更新，统一覆盖流式与 WAV 两条路径）并实时覆盖进度串，**同时保留「GET 只读不写 state.json」这一核心修复**。真机实测：3 块文本索引 0→1、进度 1/3→2/3 正常推进，stale 值消失。教训：变更读取源时要核对所有写入路径都覆盖到。
>
> `RuntimeState` 新增 `main_index`/`main_total`（内存权威，锁保护），`set_main` 支持 `index/total` 并派生进度串；`get_snapshot` 改为只读——删除 GET 写盘与 `player.currently_playing_index` 覆盖，实时索引读自 `RuntimeState`；`_play_wav_thread` 每块更新内存索引、新增 `_persist_current_index` 辅助做**节流（≥1.5s）+ 收尾最终持久化**（仅播放线程写盘，供 RESUME）。验收：3 文件语法 OK；`save_state` 在 backend.py 仅剩 `/read`(597) + `/seek`(730)；smoke 17 通过。
> ⚠️ **预存在缺陷（非本步引入）**：`test_podcast_pause_state_allows_long_paused_frontend` 在电池供电时失败——`_pause_state` 走到 `is_on_battery_power()` 分支返回 `(True,"battery")`，测试未 mock 电源。留待 **P4.7/P4.8** 修（mock 电源 + 缓存 pmset）。

- **问题**：`/snapshot`（GET）在 `backend.py:1106-1118` 检测到索引推进就 `storage.save_state(state)`；同时 `_play_wav_thread`（`playback_service.py:379-382`）和 `_shared_task_loop` 也写同一 `state.json`。三方无锁读改写 → karaoke 索引回退/丢更新/文件损坏。GET 端点不应改持久状态。
- **改进方式**：
  1. 当前播放索引/进度只存内存 `RuntimeState`（已有锁）作唯一权威。
  2. 仅在播放线程持久化，并**节流/合并**写盘（当前每个音频块都 `load_state()+save_state()`，见 `playback_service.py:296-406`）。
  3. `/snapshot` 改为纯读 `RuntimeState`，不触发任何写。
- **验收标准**：
  - 全局搜索确认 GET 路由内无 `save_state` 调用。
  - 播放一篇长文时，并发轮询 `/snapshot` 不再出现 `current_index` 回退（加临时断言/日志验证）。
  - `state.json` 写入频率从"每块"降到"每会话/每 N 秒"。

## P1B 修复 Python async 边界（阻塞 I/O 移出事件循环）✅ 已完成 ⭐

> 用 AST 判定出 33 个不含 `await` 的路由 handler，全部由 `async def` 改为 `def`——FastAPI/Starlette 会自动在线程池执行同步路由，一次性把所有被轮询的热路径（`/snapshot`、`/status`、`/health`、`/saved_items`、`/cache/items`、`/podcasts/*` 等）的阻塞 I/O（storage JSON 读写、`os.listdir`、`podcast_service.snapshot()` 里的 `pmset` 子进程）移出事件循环。被 URL 异步任务内部 `await` 的 2 个 handler（`save_for_later`/`generate_single_podcast`）保持 `async def`，其阻塞 storage 调用改用 `run_in_threadpool`；`read_text` 的重活原本已 offload。验收：28 测试通过（含经 TestClient 调路由的 token/week3 用例），1 失败为预存在电池测试。余下少数 async 路由（`read_url`/`play_saved`/`play_cache`/`export_cache`）仅用户动作频率、阻塞极小，未改。

- **问题**：async 路由内直接做同步阻塞调用，单 uvicorn 事件循环在扩展轮询 `/snapshot` + AppKit 轮询 `/status` 时被堵：
  - `storage.load_config/load_state/save_*`、`os.listdir(PODCASTS_DIR)`：`backend.py:524,1099,1143,1190,1204` 等。
  - `processor.parse_dialogue_or_text`（重正则 CPU）：`backend.py:538-610`。
  - `reader_service` 同步 `urllib.request.urlopen`（8-10s 超时）：`reader_service.py:79-100`。
- **改进方式**：阻塞调用统一 `await run_in_threadpool(...)`；或把纯读路由改为 `def`（Starlette 自动丢线程池）。确认 URL 抓取/defuddle 子进程运行在线程池/worker 而非事件循环。
- **验收标准**：
  - 压测：一边触发 `/read` 长文本，一边以 200ms 间隔轮询 `/health`，p99 延迟应 < 100ms（修复前会随分块时长尖刺）。
  - 代码审查确认 async 路由内无裸 `storage.*` / `os.listdir` / `urlopen`。

## P1C Swift 共享可变状态加隔离 ✅ 已完成

> `AppStateStore` 加 `@MainActor`（编译器强制主线程契约）；监听器派发改 `for ... in Array(snapshotListeners.values)` 防重入崩溃；`BackendAPIClient` 加 `@MainActor` 消除 `managementToken` 并发写竞争（采用计划的 actor 隔离方案而非不可变 token，副作用赋值保留但已主 actor 串行化）。已核对全部调用方（`BackendProcessManager` + Console/EngineSettings/Settings VC）均 `@MainActor`，隔离一致。⚠️ 完整编译验证需 `xcodebuild`（建议与 P1D 一并跑 compile-only 构建确认）；SourceKit 单文件报的跨文件类型缺失为误报（类型在 `APIModels.swift` 同 target）。

- **问题**：
  1. `AppStateStore`（`AppStateStore.swift:16`）是普通 class，文档称"始终主线程"但不强制；只因调用方恰好继承 `@MainActor` 才未崩。
  2. 监听器派发迭代 `snapshotListeners.values`，监听器（Console 视图）可重入增删 → 迭代中改字典崩溃。`:80-82`
  3. `BackendAPIClient.managementToken`（`:5,86-88`）在多个请求方法里作副作用改写，多并发 Task 无同步。
- **改进方式**：
  - `AppStateStore` 加 `@MainActor`，让编译器强制契约。
  - 派发前 `Array(snapshotListeners.values)` 快照再遍历。
  - token 改为 init 注入的不可变值，或把 `BackendAPIClient` 改为 actor。
- **验收标准**：
  - 加 `@MainActor` 后编译无新增 actor-isolation 警告（说明原本就在主线程）。
  - Console 视图频繁切换 tab（注册/注销监听）时无崩溃。
  - `grep "self.managementToken ="` 不再出现在请求方法体内。

## P1D 修复进程生命周期竞态（Swift）🚧 2/3/4 已做 · issue1 Python已做/Swift待实启测试

> **已完成 issue 2/4（双重 waitpid + pid/fdWrite 无锁）**：`BackendLauncher` 加 `NSLock` 串行化 `pid`/`fdWrite`；监控线程成为该 PID 唯一 reaper（在本地 `childPid` 上 `waitpid`）；`terminateProcessGroup` 只发信号、不 waitpid/cleanup，SIGKILL 升级前用 `self.pid == target` + `kill(target,0)` 双守卫防误伤；`cleanup`/`closeWatchdogPipe` 均 lock 内交换 fd 再 close（防双关）。
> **已完成 issue 3（重启退避时间戳）**：`lastRestartTime` → `lastReadyTime`，进入 `.ready` 时记录，崩溃退出时以"曾就绪 >60s"清零 `restartCount`。
> **issue 1（端口 TOCTOU → port-0 + runtime.json 发现）— Python 侧已做、Swift 侧设计待实施**：
>   - ✅ **Python 侧（已做、29 测试通过）**：`backend.py` 新增 `BOUND_PORT` 全局；`__main__` 当 `TTS_BACKEND_PORT==0` 时自建 socket `bind((host,0))`、`getsockname` 取真实端口存入 `BOUND_PORT`，再 `uvicorn.Server(Config(app)).run(sockets=[sock])` 服务该预绑 socket；`write_runtime_descriptor` 优先发布 `BOUND_PORT`。固定端口路径原样保留（零风险）。这是消除 TOCTOU 的地基——仅当 app 传 `TTS_BACKEND_PORT=0` 时启用。
>   - ⏳ **Swift 侧（设计就绪，需 Xcode 实启测试后实施）**：`BackendLauncher` 改传 `TTS_BACKEND_PORT="0"`；`BackendProcessManager` 删除 `isPortAvailable` 端口扫描与预选 `port`；`startBackend` 后改为轮询 `~/Library/Application Support/QwenTTS/runtime.json`（校验其中 `pid` == 本次 spawn 的 pid、`instance_id` 非空，避免读到上次残留的描述符），读出真实 `port` 后再 `BackendAPIClient(port:)` 并启动健康检查。因改动 app↔后端握手关键路径且本机无 Xcode 无法编译/实启，未盲改。
> **✅ 编译已验证**：用 `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild ... -configuration Debug CODE_SIGNING_ALLOWED=NO build` 编译 **BUILD SUCCEEDED**，无 actor/Sendable/隔离 error 或 warning——P1C/P1D 的 Swift 并发改动（`@MainActor`、`NSLock`、单一 reaper）确认编译通过。Debug 构建启动后端口 8002、`/health` 公开、`/seek` 无 token 返回 401，P2 鉴权改动对运行中后端实测通过。

- **问题**：
  1. **端口 TOCTOU**：`isPortAvailable`（`BackendProcessManager.swift:14-19`）探测后关闭再让 backend 绑，期间可被抢；且 launcher 传预选端口而非读 backend 写的 `runtime.json`（违背发现契约）。
  2. **双重 `waitpid`**：监控线程（`BackendLauncher.swift:176`）与 `terminateProcessGroup`（`:198/201`）在两线程对同一 PID 各 `waitpid`，`cleanup()` 从两处无锁改 `pid/fdWrite`。
  3. 重启退避用错时间戳：`lastRestartTime`（`BackendProcessManager.swift:287-301`）是"上次重启尝试"而非"上次健康存活"。
- **改进方式**：
  - backend 绑 `port 0` 由 OS 分配并写入 `runtime.json`，app 读该文件做端口发现，移除预选逻辑。
  - PID 只由监控线程 reap；`terminateProcessGroup` 只发信号（SIGTERM→SIGKILL），由现有 waitpid 线程统一 reap + `cleanup()`。
  - 记录"上次到达 `.ready` 的时刻"作为 uptime，据此重置 `restartCount`。
  - `pid`/`fdWrite` 收敛到串行队列或原子访问。
- **验收标准**：
  - 连续快速启停 10 次无僵尸进程（`pgrep -f backend.py` 为空）、无 `ECHILD` 报错。
  - 杀掉 backend 后正常按 uptime 重置重启计数（构造"运行 5 分钟后崩溃一次"场景，计数应归零）。
  - 两次同时启动不再端口冲突。

## P1E Podcast 任务集合加锁 + 按 job_id 键

- **问题**：`active_tasks/active_procs/active_job_ids`（`podcast_service.py:402-431`）被并发 FastAPI 请求读写无锁；按 `md5` 键（`:488`）导致同文本两次生成互相覆盖进程句柄 → 孤儿进程永不回收。
- **改进方式**：三个集合用同一 `threading.Lock` 保护；键从 `md5` 改为 `job_id`。
- **验收标准**：对同一文本并发发起两个 podcast 任务，两个进程都被正确跟踪并在完成/取消时回收（`active_procs` 最终清空，无残留进程）。

## P1F 修复关停可能挂死

- **问题**：`audio_feeder_thread` 的 `audio_q.get()`（`backend.py:395`）无 timeout 永久阻塞；`_close_queues` 的 `join_thread()`（`runtime_supervisor.py:273`）在 feeder 卡于 pickle 大 numpy 时可无限挂。
- **改进方式**：`audio_q.get(timeout=1)` 循环检查 `shutdown_event`；关停时对 mp 队列先 `cancel_join_thread()` 再 `close()`，或给 join 设上界。
- **验收标准**：在播放中途调 `/control/shutdown`，进程在超时窗口内退出（不挂起）；`test_runtime_supervisor.py` 新增"播放中关停"用例通过。

## P1G 生成异常不再静默吞掉

- **问题**：`tts_engine.generate_stream`（`tts_engine.py:235`）`except Exception: print(); return`，worker 仍照常发 `CHUNK_DONE`，用户得到静音且 UI 无错误。
- **改进方式**：往 `audio_q` 发送错误哨兵，播放层据此标记该块失败并把错误冒泡到 UI/快照。
- **验收标准**：注入一个会抛异常的合成输入，UI/快照能看到明确错误状态而非静默静音；日志记录原始异常。

---

# P2 — 安全加固

> 建议一次性把鉴权中间件从"零散 allowlist + 单独 token 集合"重写为**默认拒绝、显式放行只读 GET**。

## P2.1 补全状态变更端点的鉴权 ✅ 已完成

> 中间件重写为**默认拒绝**：放行公开只读 GET（`/health`/`/snapshot`/`/status`）→ 管理独占（`/control/*`、`/stop`、`/settings`、`/engines*`）需管理令牌 → 其余 POST/PUT/PATCH/DELETE 一律需管理或扩展令牌、未匹配即 401。AppKit 的 pause/seek 经 `BackendAPIClient` 自动带令牌不受影响。新增 `test_state_changing_endpoints_require_token_by_default`，全 30 测试通过。

- **问题**：`/seek` `/pause` `/resume` `/restart_audio` 既不在 `write_endpoints` 也不在管理 token 集合（`backend.py:512-516`），任意可达端口的客户端可无 token 劫持播放/重启音频设备。
- **改进方式**：中间件默认拒绝，显式放行只读 GET（`/health` `/snapshot` `/status` 等），其余 mutating 端点一律需 token。
- **验收标准**：无 token 调 `/seek` `/pause` `/resume` `/restart_audio` 返回 401；带 token 返回正常。新增对应测试用例。

## P2.2 `/health` 移出 token 门禁 ✅ 已完成（随 P2.1 一并修复）

> `/health` 现为公开只读 GET（无需令牌），扩展/动态端口发现可正常读取；测试断言无 token GET `/health` 返回 200。

- **问题**：`is_health`（`backend.py:502`）被纳入 token 门禁，扩展/动态端口发现客户端无法读取。
- **改进方式**：将 `is_health` 从 token-gated 集合移除，保持公开。
- **验收标准**：无 token GET `/health` 返回 200 且含 `instance_id/pid/status`。

## P2.3 `/read_url` 防 SSRF ✅ 已完成

> 新增 `validate_fetch_url`：scheme 限 http/https；`getaddrinfo` 解析主机所有地址，任一落入 private/loopback/link-local/reserved/multicast/unspecified 即拒绝（拦截 `127.0.0.1`/`localhost`/`::1`/`169.254.169.254`/`192.168.*`/`10.*` 等）；在 `/read_url` 入口经 `run_in_threadpool` 调用（DNS 阻塞）。残留 DNS-rebinding TOCTOU 已在 docstring 标注。新增 `test_read_url_ssrf_guard`，全 31 测试通过。

- **问题**：`/read_url` 仅校验非空，`process_url_job` 会抓取任意 URL（可达 `169.254.169.254`、`file://`、内网）。
- **改进方式**：限定 scheme ∈ {http, https}；解析目标 host，拦截私网/链路本地/回环 IP（含解析后的 IP，防 DNS rebinding）。
- **验收标准**：`file://`、`http://169.254.169.254/...`、`http://127.0.0.1` 等被拒绝并返回明确错误；正常公网 URL 通过。

## P2.4 收紧 loopback 鉴权旁路 ✅ 已完成

> legacy bypass 的放行主机集改为基础 `{127.0.0.1, ::1, localhost}`，仅当 `"pytest" in sys.modules` 时才加入 `"testclient"`（FastAPI TestClient 伪主机）。生产运行无 pytest，伪造 `testclient` host 不再绕过鉴权；测试仍通过。该开关的安全代价已在注释中显著标注。

- **问题**：`TTS_LEGACY_LOOPBACK_CLIENTS=1`（`backend.py:489-495`）对任意 loopback 全绕过鉴权（含 `/control/*` `/settings`），且白名单了字面量 host `"testclient"`。
- **改进方式**：`"testclient"` 仅在 `"pytest" in sys.modules` 时放行；在文档/启动日志中显著标注该开关的安全代价。
- **验收标准**：生产运行（无 pytest）时 `"testclient"` host 不再被放行；测试仍可正常跑。

## P2.5 错误响应使用正确 HTTP 状态码 ✅ 已完成

> 导入 `HTTPException`，将约 17 处真错误 `return {"error":...}` 改为 `raise HTTPException`：503（storage/player 未初始化、backend not ready）、400（空/非法参数、SSRF 拒绝）、404（item/file/cache not found）、500（mode 处理/seek 异常）。**保留信息态 200**（"正在后台解析中"、`{"status":"generating"/"saved"}`）以免扩展降级为通用错误。已核验客户端：扩展 `!response.ok` 统一处理（`background.ts:131`）、AppKit `status==200` 判断，均把非 200 视为失败——契约翻转安全。全 31 测试通过。

- **问题**：多数失败路径返回 HTTP 200 + `{"error":...}`（`backend.py:561,705,720,873,902,1059` 等），客户端无法靠状态码区分成功/失败。
- **改进方式**：失败改 `raise HTTPException(status_code=...)`，4xx 表客户端错误，5xx 表服务端错误。
- **验收标准**：典型失败场景（存储未初始化、无效参数）返回对应 4xx/5xx；AppKit/扩展不再靠字符串匹配判错。

## P2.6 探活改用廉价端点 ✅ 已完成

> 给所有 LLM provider 的 `generate()` 增加可选 `max_tokens`（默认 None→保持 8192/4096，真实生成路径不变）；`probe_provider` 改为 `generate(..., max_tokens=8)`，将探活输出上限压到几 token——即便模型忽略「只回一个字」的 prompt 约束也几乎不计费。翻译探活仅译 "hello"（按字符、成本极小）未改。采用 min-token 上限而非 SDK 特定 `models.list()`（后者无真实 key 不可测、有误报"不可用"风险，计划已接受此 fallback）。31 测试通过。

- **问题**：`/engines/check` 的 `probe_provider`（`llm_engine.py:267`、`translation_engine.py:233`）发真实计费生成/翻译调用，每次点击烧 token/配额。
- **改进方式**：尽量改用 models-list / auth 校验等廉价端点；无此能力的 provider 用最小 token 上限并标注成本。
- **验收标准**：连点 `/engines/check` 多次，付费 provider 无可观计费（或降至最小）。

---

# P3 — 架构清理与死代码

## P3.1 删除确认的死代码 ✅ 已完成（swiftc 回退分支除外）

> 删除 `gemini_engine.py`、`core/worker.py`、`build_runtime.py`（grep 确认零代码引用 + 无子进程调用）及 `reader_service.process_with_gemini` 别名；更新 CLAUDE.md（移除 worker.py/build_runtime 引用并加说明）与 `URL-Reader/README.md` 链接。引擎层导入正常、31 测试通过。⚠️ 注意 `localTTS_macOS/` 为 git 未跟踪目录，删除不可经 git 恢复。⏳ `package_release.py:79-92` 的 swiftc 无-Xcode 回退分支属构建路径变更，价值较低，未删。

- **问题 / 改进**：
  - `backend/URL-Reader/gemini_engine.py`：全仓 grep 无引用，`GeminiProvider` 已内联于 `llm_engine.py:46`。**删除**，并更新 `URL-Reader/README.md:104`。
  - `backend/core/worker.py`：独立 CLI，硬编码 `models/Qwen3-TTS-1.7B-8bit`、`../../mlx_audio`、`data/`，import `smart_split`（活路径用 `parse_dialogue_or_text`），违背 `runtime_paths` 契约。**删除**或按 `runtime_paths` 重写。
  - `build_runtime.py`：用 `uv venv` 产出不可重定位 venv，对打包无用且与 `package_release.py` 的 standalone-python 路径分叉。**删除**或重定向至 `download_standalone_python`。
  - `package_release.py:79-92` swiftc 回退分支手搓 Info.plist（缺 `LSUIElement`，与真 plist 分叉）。**删除**或从 canonical plist 生成。
  - `reader_service.py:294` `process_with_gemini` 兼容别名：确认无活调用后删除。
- **验收标准**：删除后 `pytest core/tests/ -v` 全绿；`package_release.py` 正常产出可运行 bundle；全仓 grep 无对已删符号的引用。

## P3.2 消除重复（DRY）🚧 schema 去重已做 · ProviderRouter/REF_VOICES 延后

> ✅ **默认 engines schema 三处→一处（最高价值，High）**：删除 `llm_engine._DEFAULT_MODELS`，`_model()` 改读 `engine_config.DEFAULT_ENGINES`；`backend.py._default_engines()` 改为 `deepcopy(engine_config.DEFAULT_ENGINES)`（惰性把 URL-Reader 加入 sys.path）。验证 `_model` 默认值一致、/engines 路由测试通过、31 测试通过。
> ⏳ **ProviderRouter 抽取**（call_llm/translate_text 共用调度）与 **REF_VOICES 表**（tts_engine 80 行重复）：均为纯质量重构，需真实 provider key / 模型文件才能 runtime 验证，盲改 working 代码有回归风险，延后到可实测时做。

- **问题 / 改进**：
  - 默认 engines schema **三处**复制：`backend.py:1161`、`engine_config.py:29`、`llm_engine.py:21`（`_DEFAULT_MODELS`）→ 统一从 `engine_config` 导入，删除其余副本。
  - LLM router(`call_llm`, `llm_engine.py:273`) 与翻译 router(`translate_text`, `translation_engine.py:239`) 结构近乎相同 → 抽 `ProviderRouter`（order 解析 + selected-first + fallback + tried/last_err），约省 60 行。
  - ICL 参考音频 Serena/Ryan 常量在 `tts_engine.py:121-128` 与 `166-185` 重复约 80 行 → 单一 `REF_VOICES` 表 `{(voice, lang): (audio, text)}` + 一个查找 helper。
- **验收标准**：schema/模型默认值只剩一处定义；provider 调度逻辑单一实现；`pytest` + `/engines/check` 行为不变。

## P3.3 修正路径/分层违规 ✅ Python 部分已完成（Swift 拆分延后）

> ✅ `podcast_service.search_dirs`/`clear_unpinned` 删除 legacy `QwenTTS-App/data/*` 路径（消除 `os.path.dirname(podcasts_dir)` 上推的 repo-depth 反模式，遵守分离约束；旧数据由启动迁移处理）。✅ `paths.py`：`migrate_legacy_data` 从 `__init__` 移到显式 `init()`（保留幂等的 `ensure_directories`），`init_runtime_services` 启动时调用一次——import 不再有文件迁移副作用。31 测试通过。⏳ `BackendProcessManager` 上帝对象拆分属 Swift、本机不可编译，延后。

- **问题 / 改进**：
  - `podcast_service.py:599-606,705` 硬编码 legacy `QwenTTS-App/data/podcasts`，违反 CLAUDE.md 分离约束 → 改走 `runtime_paths`，移除 legacy 路径。
  - `paths.py:123` 在 **import 时**实例化并执行 `ensure_directories()+migrate_legacy_data()`（文件拷贝副作用）→ 改惰性 `init()` 或显式初始化调用。
  - `BackendProcessManager` 为部分上帝对象（端口分配 + socket 探测 + 路径解析 + dev 环境注入 + 健康轮询 + 快照轮询 + 重启策略 + 播放派发）→ 抽出 `BackendPaths`/`EnvironmentConfig` 与重启策略为独立类型。
- **验收标准**：
  - 全仓 grep 无 `QwenTTS-App` 路径引用。
  - `import paths` 不再产生文件系统副作用（测试/CLI 上下文可安全导入）。
  - `BackendProcessManager` 行数显著下降，路径/重启逻辑可独立单测。

---

# P4 — 数据安全 / 持久化 / 代码质量 / 测试

## P4.1 `storage.load_state` 健壮化 ✅ 已完成

> 新增 `_load_json_or_default(path, default)`：缺文件/损坏均返回 `copy.deepcopy(default)`（杜绝污染共享 `self.default_*`），损坏文件 `os.replace` 备份为 `.corrupt.<ts>` 并打日志（不静默覆盖、不抛异常）；`load_state`/`load_config` 共用之。新增 `test_storage_load_is_corruption_safe_and_isolated`，32 测试通过。

- **问题**：`storage.py:80-84` 裸 `json.load` 无 try/except；缺文件路径返回**共享可变** `self.default_state`，调用方一改（如 `playback_service.py:197`）即污染整进程默认值。
- **改进方式**：包 try/except，损坏时备份后返回 `copy.deepcopy(self.default_state)`；缺文件也返回深拷贝。
- **验收标准**：写入损坏 `state.json` 后启动不崩溃、加载到干净默认值且原损坏文件被备份；连续两次 `load_state()` 互不影响。

## P4.2 SQLite 并发健壮化 ✅ 已完成

> 新增 `Storage._connect()`（`timeout=10.0`，锁竞争重试）；`_init_db` 启用 `PRAGMA journal_mode=WAL`；5 处 `sqlite3.connect` 统一走 `_connect()`。新增 `Storage.clear_cache()`（抛错而非吞），`cache_service.clear()` 改用之、不再开裸连接，删除其无用 `sqlite3` 导入。已验证 journal_mode=wal、clear_cache 往返；32 测试通过。

- **问题**：`storage.py` 每方法新建连接，无 `check_same_thread`/timeout/WAL；`cache_service.py:38-52` 绕过抽象开裸连接做 DELETE 且静默吞错。并发（worker 写 + API 读 + clear）会 `database is locked`。
- **改进方式**：init 时 `PRAGMA journal_mode=WAL`；连接传 `timeout=`；新增 `Storage.clear_cache()` 替代裸连接并冒泡失败；考虑单连接 + 锁。
- **验收标准**：并发写缓存 + 读 + clear 的压力测试无 `database is locked`；clear 失败能被调用方感知。

## P4.3 JSON job store 防历史清空 ✅ 已完成

> `PodcastJobStore`/`UrlJobStore` 的 `_load_unlocked` 增加 `_backup_corrupt`：解析失败或非列表时 `os.replace` 备份为 `.corrupt.<ts>` 再返回 `[]`，避免随后 `_write_unlocked` 用空列表覆盖、清空整个任务历史（历史可从备份恢复）。已验证两 store 均备份+返回空；32 测试通过。

- **问题**：`podcast_jobs.py:107`、`url_jobs.py:91` 的 `_load_unlocked` 吞所有异常返回 `[]`，配合截断写入会在下次写时**静默清空全部历史**。
- **改进方式**：解析失败时备份损坏文件并跳过破坏性重写，不把空当权威。
- **验收标准**：人为损坏 job 文件后，下次写不清空历史且损坏文件被备份保留。

## P4.4 缓存淘汰以 DB 为准 ✅ 已完成

> `manage_cache_limit` 重写为 DB 驱动：取 `get_all_cache()`（`ORDER BY created_at DESC`），淘汰 `rows[max_items:]`——按行 `file_path` 删文件、按 `md5` 删行，DB 与磁盘一致（消除 mtime/文件名推断导致的孤儿行）。`max_items` 统一为 `CACHE_MAX_ITEMS` 常量（去掉两处硬编码 10）；裸 except 收窄并加日志。已验证保留最新项、删除最旧项文件+行；32 测试通过。（孤儿 .npy 文件——有文件无行——不主动清扫以避免与写入竞争。）

- **问题**：`backend.py:100-112` 淘汰按文件 mtime，DB 按 `created_at`（`storage.py:95` `INSERT OR REPLACE` 用新 `created_at`），两者漂移导致 DB 孤儿行；`max_items=10` 硬编码两处。
- **改进方式**：以 DB `ORDER BY created_at` 为权威选淘汰对象，按 row 的 `file_path` 删文件；`max_items` 提为配置。
- **验收标准**：淘汰后 DB 行数 = 文件数 = 配置上限，无孤儿行/孤儿文件。

## P4.5 统一 performance profile 命名 ✅ 已完成（文档对齐代码）

> 经核查：代码（`performance.py`/`processor.py`/`podcast_service`）与 AppKit Settings 选择器（`SettingsViewController` 发送 `["balanced","fast","quiet"]`）**已一致使用 `fast/balanced/quiet`，无任何调用方传 "performance"**，所谓"静默回落"实际不发生。漂移仅在 CLAUDE.md 文档（写成 `quiet/balanced/performance` 且字段描述有误）。修法：对齐文档到真实键名 + 修正字段列表，并注明三处调用方（重命名键需同步改）。未改代码（重命名会破坏发送 "fast" 的 Swift UI）。

- **问题**：`performance.py` 键为 `fast/balanced/quiet`，但 CLAUDE.md 与调用方用 `performance`，`get_performance_profile("performance")` 静默回落 `balanced`（`performance.py:33`）；`processor.py:118` 同样只认 `fast/balanced/quiet`。
- **改进方式**：统一为 `quiet/balanced/performance`（与文档一致），全链路同步改名。
- **验收标准**：传 `"performance"` 真正命中高性能 profile（chunk/workers/thermal 与 balanced 不同）；无静默回落。

## P4.6 收窄异常处理 ✅ 已完成（裸 except）

> core 内全部 9 处裸 `except:` → `except Exception:`（不再吞 `KeyboardInterrupt`/`SystemExit`）：player.py 7 处（热/清理路径，不加日志避免刷屏）、backend.py 2 处（缓存重放、pending 文件扫描，加 debug 日志）。core 已无裸 `except:`，32 测试通过。散布的 `except Exception: pass`（不吞中断信号、危害较小、多为有意 best-effort）未逐一加日志，作为更低优先级。

- **问题**：散布 `except: pass` / `except Exception: pass`（`backend.py:111-112,231,654,919`；`player.py:127,296,302,326,414,444`；podcast/cache/runtime_log/paths 等），吞掉 `KeyboardInterrupt`、隐藏缓存损坏/listdir 失败。
- **改进方式**：捕获具体异常类型并至少 log；裸 `except:` 一律改为 `except Exception:` 或更具体。
- **验收标准**：全仓无裸 `except:`；关键清理/缓存路径失败时有日志可查。

## P4.7 性能小优化 ✅ 已完成

> `is_on_battery_power` 加 5s 模块级缓存（`_BATTERY_CACHE`），避免管理循环/snapshot 频繁 spawn `pmset`。`runtime_log` 改为按 `_trim_interval = max(50, max_events//5)` 计数触发 trim（而非每条 O(n) 全量重写），文件有界于 `max_events + interval`；`recent()` 读取上限取 `min(limit, max_events)` 使逻辑保留量与延迟 trim 解耦（修正了相关测试）。32 测试通过。

- **问题**：`is_on_battery_power`（`podcast_service.py:25`）每次 shell `pmset`（1s 超时），manager 循环每 2s + `snapshot()` 多次调用 → 频繁子进程。`runtime_log._trim_locked`（`:44-55`）每次 `record()` 全量读写文件。
- **改进方式**：电源状态缓存几秒；日志 trim 改为按计数/间隔触发。
- **验收标准**：稳态运行时 `pmset` 调用频率显著下降；高频写日志不再每条全量重写。

## P4.8 补关键测试覆盖 ✅ 本环境可做部分已完成

> ✅ 修复 `test_podcast_pause_state_allows_long_paused_frontend` 的电源环境依赖（注入 `get_battery_policy=lambda:"allow"`，与是否插电解耦）。✅ 新增 `test_engines_endpoints_require_management_token`（GET/PATCH `/engines`、POST `/engines/check` 的 401/200）。✅ 新增 `test_runtime_paths_bundled_resource_env_overrides_win`（`TTS_REFERENCE_PATH`/`MLX_AUDIO_PATH`/`TTS_FFMPEG_PATH` 覆盖优先，不回退 repo-depth）。共 34 测试通过。
> ⏳ **需真实启动/CI 环境**：跨进程看门狗 FD3 继承链测试、`package_release.py`→`run_diagnostics.py`→spawn 命中 `/health` 的打包-启动契约 CI lane、ad-hoc 签名 + `spctl`/公证预检——均需可启动后端/带 Xcode 的 CI，无法在此可靠（非 flaky）实现，留作 CI 建设。

- **问题**：打包→启动→签名整条契约零自动化覆盖；看门狗跨进程 FD3 继承链未测；token 鉴权仅测 `/stop` `/settings`，`/engines*` 未测，`/control/shutdown` 当前无 token 返回 200（需确认是否应门禁）；`paths.py` 的 bundled-resource 解析与 repo-depth 回退未测。
- **改进方式**：
  - CI 在 `package_release.py` 后跑 `run_diagnostics.py` + spawn backend 命中 `/health`。
  - 加跨进程看门狗集成测试（继承管道读端，关写端，断言子+孙进程退出）。
  - 补 `/engines` `/engines/check` 的 401/200 用例；明确 `/control/shutdown` 是否需 token 并测试。
  - 补 `paths.py` 测试：env-var override 优先、env 设定时不走 repo-depth 回退。
- **验收标准**：上述测试纳入 CI 并通过；CI 含一条 ad-hoc 签名 + `codesign --verify --strict` + `spctl` 评估的发布预检 lane。

---

## 附：审查覆盖范围

| 子系统 | 关键发现 |
|--------|---------|
| Swift AppKit | 端口 TOCTOU、双重 waitpid、AppStateStore 未隔离、重启退避时间戳错误 |
| Python backend core | 事件循环阻塞 I/O、state.json 多写者、鉴权缺口、SSRF、worker.py 死代码、关停挂死 |
| 服务 / 引擎层 | podcast 集合无锁 + md5 键、SQLite 无 WAL、job store 清空风险、schema 三处重复、gemini_engine.py 死代码 |
| 构建 / 打包 / 测试 | 缺 entitlements（公证必失败）、依赖锁过期、standalone-python 无校验、ffmpeg dylib、打包契约零测试 |

> 以上为静态审查结论。如需对每条高危发现做对抗式验证后再实施，可启动多 agent workflow（需明确授权，会消耗较多 token）。
