# localTTS macOS AppKit 第 2 周设计与实施方案

你好，LALALA。根据第 2 周的 Timeline 规划，我们将在 Python 后端与 macOS AppKit（Swift）侧共同建立双向死亡监听（Watchdog Pipe）、独立进程组强杀保障以及管理令牌（Token）认证。以下是具体技术方案：

---

## 1. 核心设计细节

### 1.1 Watchdog Pipe (FD 继承与 EOF 监听)
为了保证 App 崩溃或异常退出时，Python 后端能迅速自我了断，设计如下：
1. **Swift 侧**：
   * 使用 `pipe()` 系统调用创建匿名管道，得到读端 `fd_read` 和写端 `fd_write`。
   * 设置 `fd_write` 的 `FD_CLOEXEC` 标志（防止其被子进程继承泄露）。
   * 清除 `fd_read` 的 `FD_CLOEXEC` 标志，或在 `posix_spawn` 的 file actions 中将其复制为特定 FD（例如 `3`）。
   * 启动子进程时传入环境变量 `TTS_WATCHDOG_FD=3`。
2. **Python 侧**：
   * `RuntimeSupervisor` 启动一个 `watchdog-thread` 线程。
   * 对 `fd_read` 设置 `FD_CLOEXEC`，以防 Python 后端拉起推理及播客子进程时，该 FD 泄露给它们，导致引用计数不为 0。
   * 线程在 `os.read(fd_read, 1)` 上阻塞等待。
   * 一旦 AppKit 崩溃，写端被 OS 自动关闭，`os.read` 立即返回空字节（EOF），触发 `asyncio.run_coroutine_threadsafe(supervisor.shutdown(), loop)` 执行优雅退出。

### 1.2 独立进程组隔离与 posix_spawn
为了实现精确定向强杀，防止残留推理和播客进程，也防止扫描命令行误杀无关进程：
1. **Swift 侧**：
   * 在使用 `posix_spawn` 启动 Python 时，配置 `posix_spawnattr_t`，开启 `POSIX_SPAWN_SETPGROUP` 标志。
   * 这样 Python 主进程及其派生进程都会处于同一个以 Python 主 PID 为 PGID 的**独立进程组**中。
   * 优雅退出超时或强制关闭时，Swift 侧调用 `killpg(pgid, SIGKILL)` 即可一网打尽所有残留子进程。

### 1.3 管理令牌认证 (Token API Security)
为防止恶意程序调用敏感控制 API：
1. **Swift 侧**：每次启动 Python 后端前，生成随机 UUID 令牌，并通过环境变量 `TTS_MANAGEMENT_TOKEN` 传入 Python。
2. **Python 侧**：核心控制 API（心跳、强杀、设置更新）拦截请求，校验 HTTP header `X-Management-Token`，不匹配返回 `401 Unauthorized`。

---

## 2. 实施计划 (Timeline & Steps)

- **第一阶段 (Python 后端集成)**：
  * 修改 `localTTS_macOS/backend/core/services/runtime_supervisor.py`，增加 `watchdog_thread` 监听和管理令牌校验支持。
  * 修改 `localTTS_macOS/backend/core/backend.py`，在 `lifespan` 启动时调用 `start_watchdog`，并为控制类 API 增加令牌中间件。
  * 编写 `localTTS_macOS/backend/core/tests/test_watchdog_token.py` 验证机制。

- **第二阶段 (Swift Spike 验证)**：
  * 在 [/Users/funanhe/00_MyCode/TTS/localTTS_macOS/](file:///Users/funanhe/00_MyCode/TTS/localTTS_macOS/) 下编写一个轻量级 Swift 原生测试程序 `ProcessSupervisorSpike.swift`。
  * 编译并运行该程序，验证以独立进程组启动 Python、通过 Pipe 维持心跳、App 崩溃后 Python 自动退出的完整全链路流程。
