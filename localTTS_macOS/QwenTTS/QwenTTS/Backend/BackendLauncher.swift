import Foundation

#if os(macOS)
import Darwin
#endif

class BackendLauncher {
    /// 串行化对 pid / fdWrite 的跨线程访问（主线程、监控线程、终止线程）。
    private let lock = NSLock()
    private var pid: pid_t = 0
    private var fdWrite: Int32 = -1
    private(set) var managementToken: String = ""

    func launch(
        pythonPath: String,
        scriptPath: String,
        port: Int,
        onExit: @escaping () -> Void
    ) -> Bool {
        guard FileManager.default.isExecutableFile(atPath: pythonPath),
              FileManager.default.fileExists(atPath: scriptPath) else {
            print("[Launcher] Bundled backend runtime is incomplete: \(pythonPath), \(scriptPath)")
            return false
        }

        // 1. 创建匿名管道
        var fds: [Int32] = [0, 0]
        guard pipe(&fds) == 0 else {
            print("[Launcher] Failed to create watchdog pipe.")
            return false
        }
        let fdRead = fds[0]
        self.fdWrite = fds[1]
        
        // 设置 CLOEXEC 防止写端管道被其他派生进程继承
        guard fcntl(fdWrite, F_SETFD, FD_CLOEXEC) == 0 else {
            close(fdRead)
            close(fdWrite)
            self.fdWrite = -1
            return false
        }
        
        // 2. 配置 posix_spawn file actions 复制读端管道到 FD 3
        var fileActions: posix_spawn_file_actions_t?
        posix_spawn_file_actions_init(&fileActions)
        defer { posix_spawn_file_actions_destroy(&fileActions) }
        posix_spawn_file_actions_adddup2(&fileActions, fdRead, 3)

        // GUI applications inherit /dev/null for stdout/stderr. Persist Python
        // startup errors so a failed backend is diagnosable after packaging.
        var logFD: Int32 = -1
        if let appSupport = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first?.appendingPathComponent("QwenTTS/Logs", isDirectory: true) {
            try? FileManager.default.createDirectory(at: appSupport, withIntermediateDirectories: true)
            let logPath = appSupport.appendingPathComponent("backend.log").path
            logFD = open(logPath, O_WRONLY | O_CREAT | O_APPEND, 0o644)
            if logFD >= 0 {
                posix_spawn_file_actions_adddup2(&fileActions, logFD, STDOUT_FILENO)
                posix_spawn_file_actions_adddup2(&fileActions, logFD, STDERR_FILENO)
                posix_spawn_file_actions_addclose(&fileActions, logFD)
            }
        }
        
        // 3. 配置 Attributes 使子进程成为独立进程组 Leader
        var attr: posix_spawnattr_t?
        posix_spawnattr_init(&attr)
        defer { posix_spawnattr_destroy(&attr) }
        posix_spawnattr_setflags(&attr, Int16(POSIX_SPAWN_SETPGROUP))
        posix_spawnattr_setpgroup(&attr, 0)
        
        // 4. 构建注入的环境变量 (包含 Watchdog FD, Token, 端口，强制 localhost 绑定)
        self.managementToken = UUID().uuidString
        var envs = ProcessInfo.processInfo.environment
        envs["TTS_WATCHDOG_FD"] = "3"
        envs["TTS_WATCHDOG_EXIT_PROCESS"] = "1"
        envs["TTS_MANAGEMENT_TOKEN"] = self.managementToken
        envs["TTS_BACKEND_PORT"] = String(port)
        envs["TTS_BACKEND_HOST"] = "127.0.0.1"
        envs["PYTHONUNBUFFERED"] = "1"
        envs["PYTHONDONTWRITEBYTECODE"] = "1"
        
        if EnvironmentConfigManager.shared.mode == .custom {
            let config = EnvironmentConfigManager.shared.customConfig
            if !config.mlxAudioPath.isEmpty { envs["MLX_AUDIO_PATH"] = config.mlxAudioPath }
            if !config.modelsPath.isEmpty { envs["TTS_MODELS_PATH"] = config.modelsPath }
            if !config.referenceAudioPath.isEmpty { envs["TTS_REFERENCE_PATH"] = config.referenceAudioPath }
            if !config.ffmpegPath.isEmpty { envs["TTS_FFMPEG_PATH"] = config.ffmpegPath }
        } else {
            if let resourcePath = Bundle.main.resourcePath {
                let runtimePath = resourcePath + "/PythonRuntime"
                let backendPath = resourcePath + "/Backend"
                envs["PYTHONHOME"] = runtimePath
                envs["PYTHONPATH"] = [
                    backendPath,
                    runtimePath + "/lib/python3.11/site-packages"
                ].joined(separator: ":")
                // The native app owns an independent MLX-Audio source snapshot
                // at Resources/Backend/mlx_audio.
                envs["MLX_AUDIO_PATH"] = backendPath

                let bundledFfmpeg = resourcePath + "/Tools/ffmpeg"
                if FileManager.default.fileExists(atPath: bundledFfmpeg) {
                    envs["TTS_FFMPEG_PATH"] = bundledFfmpeg
                    let toolsPath = resourcePath + "/Tools"
                    envs["PATH"] = toolsPath + ":" + (envs["PATH"] ?? "/usr/bin:/bin")
                }
                
                let bundledRef = backendPath + "/reference"
                if FileManager.default.fileExists(atPath: bundledRef) {
                    envs["TTS_REFERENCE_PATH"] = bundledRef
                }
            }
        }

        if let appSupport = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first?.appendingPathComponent("QwenTTS", isDirectory: true) {
            envs["TTS_APP_SUPPORT_PATH"] = appSupport.path
            envs["TTS_DATA_PATH"] = appSupport.appendingPathComponent("Data").path
            envs["TTS_CACHE_PATH"] = appSupport.appendingPathComponent("Cache").path
            envs["TTS_PODCASTS_PATH"] = appSupport.appendingPathComponent("Podcasts").path
            // 不要覆盖自定义环境（dev 探测 / 用户配置）已设的模型路径
            if envs["TTS_MODELS_PATH"] == nil {
                envs["TTS_MODELS_PATH"] = appSupport.appendingPathComponent("Models").path
            }
            envs["TTS_LOGS_PATH"] = appSupport.appendingPathComponent("Logs").path
        }
        
        let envStrings = envs.map { "\($0.key)=\($0.value)" }
        var cEnv = envStrings.map { strdup($0) }
        cEnv.append(nil)
        defer {
            for ptr in cEnv {
                if let p = ptr { free(p) }
            }
        }
        
        // 5. 准备参数
        let args = [pythonPath, scriptPath]
        var cArgs = args.map { strdup($0) }
        cArgs.append(nil)
        defer {
            for ptr in cArgs {
                if let p = ptr { free(p) }
            }
        }
        
        // 6. 执行 posix_spawn 启动
        var childPid: pid_t = 0
        let spawnStatus = posix_spawn(
            &childPid,
            pythonPath,
            &fileActions,
            &attr,
            cArgs,
            cEnv
        )
        
        guard spawnStatus == 0 else {
            print("[Launcher] posix_spawn backend failed with code: \(spawnStatus)")
            close(fdRead)
            close(fdWrite)
            self.fdWrite = -1
            if logFD >= 0 { close(logFD) }
            return false
        }
        
        self.pid = childPid
        close(fdRead) // 父进程中可以关闭不需要的读端
        if logFD >= 0 { close(logFD) }
        
        print("[Launcher] Spawned backend process. PID: \(self.pid), PGID: \(getpgid(self.pid))")
        
        // 7. 监控线程是该 PID 的【唯一】收割者（reaper）：阻塞 waitpid 直到子进程
        //    退出后清理防僵尸并回调 onExit。terminateProcessGroup 只发信号、绝不
        //    waitpid，避免两个线程对同一 PID 并发 reap 产生竞态 / ECHILD。
        DispatchQueue.global(qos: .background).async { [weak self] in
            var status: Int32 = 0
            let waitResult = waitpid(childPid, &status, 0)
            print("[Launcher] Backend PID \(waitResult) exited with status code \(status).")
            self?.cleanup()
            onExit()
        }
        
        return true
    }

    /// 只发信号，不 waitpid、不 cleanup——收割与清理由监控线程独占完成。
    func terminateProcessGroup() {
        lock.lock(); let target = pid; lock.unlock()
        guard target > 0 else { return }
        print("[Launcher] Sending SIGTERM to process group: -\(target)")
        kill(-target, SIGTERM)

        DispatchQueue.global(qos: .background).async { [weak self] in
            guard let self = self else { return }
            Thread.sleep(forTimeInterval: 2.0)
            // 仅当监控线程尚未收割（pid 仍为同一 target）才升级 SIGKILL；
            // 否则进程已退出/已被替换，发信号到 -target 可能误伤无关进程组。
            self.lock.lock(); let stillCurrent = (self.pid == target); self.lock.unlock()
            guard stillCurrent, kill(target, 0) == 0 else { return }
            print("[Launcher] Process group -\(target) still active after 2s. Sending SIGKILL.")
            kill(-target, SIGKILL)
            // 监控线程的阻塞 waitpid 会随之返回并执行 cleanup()。
        }
    }

    func closeWatchdogPipe() {
        lock.lock()
        let fd = fdWrite
        fdWrite = -1
        lock.unlock()
        if fd >= 0 {
            print("[Launcher] Closing watchdog pipe write-end.")
            close(fd)
        }
    }

    private func cleanup() {
        lock.lock()
        pid = 0
        let fd = fdWrite
        fdWrite = -1
        lock.unlock()
        if fd >= 0 { close(fd) }
    }
}
