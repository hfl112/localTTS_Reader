import Foundation

@MainActor
class BackendProcessManager {
    private let launcher = BackendLauncher()
    private(set) var apiClient: BackendAPIClient?

    /// 集中状态源。由 ApplicationCoordinator 注入，单一轮询器把 snapshot 写入这里并通知订阅者。
    weak var stateStore: AppStateStore?
    
    private(set) var state: BackendState = .stopped
    private var stateCallback: ((BackendState) -> Void)?
    
    private lazy var port: Int = {
        for p in 8002...8100 {
            if isPortAvailable(port: p) { return p }
        }
        return 8002 // Fallback
    }()
    
    private func isPortAvailable(port: Int) -> Bool {
        let socketFD = socket(AF_INET, SOCK_STREAM, 0)
        guard socketFD >= 0 else { return false }
        defer { close(socketFD) }
        
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(port).bigEndian
        addr.sin_addr.s_addr = INADDR_ANY.bigEndian
        
        let addrLen = socklen_t(MemoryLayout.size(ofValue: addr))
        let bindResult = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(socketFD, $0, addrLen)
            }
        }
        return bindResult == 0
    }
    
    private var pythonPath: String {
        if EnvironmentConfigManager.shared.mode == .custom {
            let path = EnvironmentConfigManager.shared.customConfig.pythonPath
            if !path.isEmpty { return path }
        }
        if let resourcePath = Bundle.main.resourcePath {
            let bundledPy = resourcePath + "/PythonRuntime/bin/python3"
            if FileManager.default.fileExists(atPath: bundledPy) {
                return bundledPy
            }
        }
        return ProcessInfo.processInfo.environment["TTS_DEV_PYTHON"] ?? "/usr/bin/python3"
    }
    
    private var scriptPath: String {
        if EnvironmentConfigManager.shared.mode == .custom {
            let path = EnvironmentConfigManager.shared.customConfig.backendPath
            if !path.isEmpty { return path }
        }
        if let resourcePath = Bundle.main.resourcePath {
            let bundledScript = resourcePath + "/Backend/core/backend.py"
            if FileManager.default.fileExists(atPath: bundledScript) {
                return bundledScript
            }
        }
        return ProcessInfo.processInfo.environment["TTS_DEV_BACKEND"] ?? ""
    }
    
    #if DEBUG
    /// When running unpackaged from Xcode, auto-detect the sibling repo's conda
    /// backend + model so the app launches a working backend without manual
    /// environment configuration. Derives paths from this file's compile-time
    /// location, so it follows the repo wherever it is checked out. Skipped once
    /// a valid custom config (user- or seed-provided) is present, and skipped
    /// entirely in packaged builds that ship a bundled runtime.
    /// 返回 true 表示已有/已配好可用的 dev 自定义环境（conda 后端 + 模型齐全），
    /// 调用方据此可在 DEBUG 下跳过模型向导直接启动后端。
    @discardableResult
    func seedDevEnvironmentIfNeeded() -> Bool {
        let bundledScript = (Bundle.main.resourcePath ?? "") + "/Backend/core/backend.py"
        if FileManager.default.fileExists(atPath: bundledScript) { return false }

        let cfg = EnvironmentConfigManager.shared.customConfig
        if EnvironmentConfigManager.shared.mode == .custom,
           !cfg.backendPath.isEmpty,
           FileManager.default.fileExists(atPath: cfg.backendPath) {
            return true
        }

        // #filePath = <repo>/QwenTTS/QwenTTS/Backend/BackendProcessManager.swift
        let repoRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // Backend/
            .deletingLastPathComponent()   // QwenTTS/ (sources)
            .deletingLastPathComponent()   // QwenTTS/ (project dir)
            .deletingLastPathComponent()   // localTTS_macOS/ (repo root)
        let workspaceRoot = repoRoot.deletingLastPathComponent()

        let backendScript = repoRoot.appendingPathComponent("backend/core/backend.py").path
        guard FileManager.default.fileExists(atPath: backendScript) else {
            print("[ProcessManager] Dev seed skipped: backend.py not found at \(backendScript)")
            return false
        }

        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let pythonCandidates = [
            ProcessInfo.processInfo.environment["TTS_DEV_PYTHON"],
            "\(home)/miniconda3/envs/gemini/bin/python",
            "\(home)/anaconda3/envs/gemini/bin/python",
            "\(home)/miniforge3/envs/gemini/bin/python",
        ].compactMap { $0 }
        guard let python = pythonCandidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else {
            print("[ProcessManager] Dev seed skipped: no conda python found (set TTS_DEV_PYTHON).")
            return false
        }

        var config = cfg
        config.pythonPath = python
        config.backendPath = backendScript
        config.mlxAudioPath = workspaceRoot.appendingPathComponent("mlx_audio").path
        config.modelsPath = workspaceRoot.appendingPathComponent("mlx_audio/models").path
        config.referenceAudioPath = repoRoot.appendingPathComponent("backend/reference").path
        EnvironmentConfigManager.shared.customConfig = config
        EnvironmentConfigManager.shared.mode = .custom
        print("[ProcessManager] Dev environment auto-seeded. python=\(python) backend=\(backendScript)")
        return true
    }
    #endif

    private let healthMonitor = BackendHealthMonitor()

    private var snapshotTask: Task<Void, Never>?
    var onPlaybackUpdate: ((_ title: String, _ progress: String, _ playing: Bool, _ paused: Bool) -> Void)?

    /// `--mock-backend`:用内存 mock 替代真实 Python 后端(UI 验证用,见 §4)。
    private var isMockMode: Bool {
        ProcessInfo.processInfo.arguments.contains("--mock-backend")
    }

    func startBackend(onStateChange: @escaping (BackendState) -> Void) {
        self.stateCallback = onStateChange

        if isMockMode {
            startMockBackend()
            return
        }

        #if DEBUG
        seedDevEnvironmentIfNeeded()
        #endif
        updateState(.launching)

        self.apiClient = BackendAPIClient(port: port)

        let success = launcher.launch(
            pythonPath: pythonPath,
            scriptPath: scriptPath,
            port: port
        ) { [weak self] in
            // 子进程退出的回调 (onExit)
            DispatchQueue.main.async {
                self?.handleProcessExit()
            }
        }

        if !success {
            updateState(.failed)
            return
        }

        updateState(.waitingForHealth)
        startHealthCheck()
    }

    /// Mock 启动路径:不 spawn Python,挂上内存 mock,走正常 health → ready → snapshot 轮询。
    private func startMockBackend() {
        print("[ProcessManager] Mock backend mode active (--mock-backend).")
        updateState(.launching)
        let client = BackendAPIClient(port: port)
        client.mock = MockBackend.fromProcessArgs()
        self.apiClient = client
        updateState(.waitingForHealth)
        startHealthCheck()
    }

    func stopBackend() {
        guard state != .stopped && state != .stopping else { return }
        healthMonitor.cancel()
        updateState(.stopping)
        
        launcher.closeWatchdogPipe()
        
        // 尝试优雅关闭并启动强杀定时
        Task {
            let token = launcher.managementToken
            let shutdownSent = await apiClient?.requestShutdown(token: token) ?? false
            if shutdownSent {
                print("[ProcessManager] Shutdown command sent successfully.")
            } else {
                print("[ProcessManager] Failed to send shutdown command. Proceeding to force kill.")
                launcher.terminateProcessGroup()
            }
        }
        
        // 兜底：若 3 秒内主进程监控未将状态变更为 stopped，则强制杀进程组
        DispatchQueue.main.asyncAfter(deadline: .now() + 3.0) { [weak self] in
            if self?.state == .stopping {
                print("[ProcessManager] Graceful stop timeout. Sending SIGKILL to process group.")
                self?.launcher.terminateProcessGroup()
            }
        }
    }

    func readClipboard(text: String) {
        Task {
            _ = await apiClient?.readText(text: text, voice: nil, performanceProfile: nil)
        }
    }

    func triggerAction(_ action: String) {
        Task {
            switch action {
            case "stop": _ = await apiClient?.stopPlayback()
            case "pause": _ = await apiClient?.pausePlayback()
            case "resume": _ = await apiClient?.resumePlayback()
            case "next": _ = await apiClient?.seekPlayback(direction: 1)
            case "prev": _ = await apiClient?.seekPlayback(direction: -1)
            default: break
            }
        }
    }

    private func updateState(_ newState: BackendState) {
        self.state = newState
        stateCallback?(newState)
        
        if newState == .ready {
            lastReadyTime = Date()
            startSnapshotPolling()
        } else {
            snapshotTask?.cancel()
            snapshotTask = nil
        }
    }

    private func startHealthCheck() {
        healthMonitor.start(
            apiClient: apiClient,
            token: { [weak self] in self?.launcher.managementToken ?? "" },
            onReady: { [weak self] in self?.updateState(.ready) },
            onTimeout: { [weak self] in
                print("[ProcessManager] Health check timeout. Stopping backend.")
                self?.stateStore?.reportConnectionError("后端健康检查超时（30s 内未就绪）")
                self?.stopBackend()
            }
        )
    }

    private func startSnapshotPolling() {
        snapshotTask?.cancel()
        // 唯一轮询器：500ms 频率，保证 ConsoleViewController 歌词滚动手感
        // （ConsoleViewController 自己的轮询已删除，改为订阅 AppStateStore）。
        snapshotTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(500))
                guard let self, !Task.isCancelled else { return }
                if let snap = await self.apiClient?.fetchSnapshot() {
                    // 1) 写入集中状态源并通知所有订阅者（含派生字段、连接健康度）
                    self.stateStore?.updateSnapshot(snap)
                    // 2) 保留既有回调，避免破坏 ApplicationCoordinator / popover 调用方
                    let title = snap.main_title ?? ""
                    let progress = snap.main_progress ?? ""
                    let playing = snap.main_is_playing ?? false
                    let paused = snap.is_paused ?? false
                    self.onPlaybackUpdate?(title, progress, playing, paused)
                } else {
                    // 拉取失败：把错误冒泡到集中状态源（不再静默）
                    let detail = self.apiClient?.lastTransportError ?? "snapshot 请求失败"
                    self.stateStore?.reportConnectionError(detail)
                }
            }
        }
    }

    private var restartCount = 0
    /// 上次后端真正进入 .ready 的时刻（即可用 uptime 起点）。重启退避计数据此重置：
    /// 只有"曾健康存活超过 60s"才算稳定运行、清零计数；而非以"上次重启尝试时刻"衡量。
    private var lastReadyTime: Date?

    private func handleProcessExit() {
        healthMonitor.cancel()

        if state == .stopping {
            updateState(.stopped)
            restartCount = 0
        } else {
            // Abnormal exit
            let now = Date()
            if let ready = lastReadyTime, now.timeIntervalSince(ready) > 60 {
                // 后端曾稳定就绪超过 60s，视为一次正常运行，清零崩溃重启计数
                restartCount = 0
            }

            if restartCount < 5 {
                restartCount += 1
                let backoffDelay = pow(2.0, Double(restartCount)) // 2, 4, 8, 16, 32 seconds
                updateState(.failed)
                print("[ProcessManager] Backend crashed. Restarting in \(backoffDelay)s (Attempt \(restartCount)/5).")

                DispatchQueue.main.asyncAfter(deadline: .now() + backoffDelay) { [weak self] in
                    guard let self = self, self.state == .failed else { return }
                    self.startBackend(onStateChange: self.stateCallback ?? { _ in })
                }
            } else {
                print("[ProcessManager] Backend crashed too many times. Giving up.")
                updateState(.failed)
            }
        }
    }
}
