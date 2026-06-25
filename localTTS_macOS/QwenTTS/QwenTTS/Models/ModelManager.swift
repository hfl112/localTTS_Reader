import Foundation

enum ModelStatus {
    case installed
    case missing
    case downloading(progress: Double)
    case paused
}

final class ModelManager: NSObject {
    static let shared = ModelManager()

    private var downloadProcess: Process?
    private var completionCallback: ((Bool) -> Void)?
    private(set) var currentDownloadingModel: String?
    private var pausedModel: String?

    var modelsDirectory: URL {
        let appSupport = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first!
        let models = appSupport.appendingPathComponent("QwenTTS/Models", isDirectory: true)
        try? FileManager.default.createDirectory(at: models, withIntermediateDirectories: true)
        return models
    }

    func checkModelStatus(name: String) -> ModelStatus {
        let model = modelsDirectory.appendingPathComponent(name)
        let weights = model.appendingPathComponent("model.safetensors")
        let config = model.appendingPathComponent("config.json")
        if FileManager.default.fileExists(atPath: weights.path),
           FileManager.default.fileExists(atPath: config.path),
           let size = (try? weights.resourceValues(forKeys: [.fileSizeKey]).fileSize),
           size > 100_000_000 {
            return .installed
        }
        if currentDownloadingModel == name, downloadProcess?.isRunning == true {
            // huggingface_hub owns transfer progress and resumable partial files.
            return .downloading(progress: 0)
        }
        if pausedModel == name { return .paused }
        return .missing
    }

    func startDownload(
        name: String,
        repoID: String,
        progress: @escaping (Double) -> Void,
        completion: @escaping (Bool) -> Void
    ) {
        guard downloadProcess?.isRunning != true else {
            completion(false)
            return
        }
        guard let python = pythonPath else {
            completion(false)
            return
        }

        currentDownloadingModel = name
        pausedModel = nil
        completionCallback = completion
        progress(0.01)

        let destination = modelsDirectory.appendingPathComponent(name, isDirectory: true)
        let script = """
        import sys
        from huggingface_hub import snapshot_download
        snapshot_download(repo_id=sys.argv[1], local_dir=sys.argv[2])
        """
        let process = Process()
        process.executableURL = python
        process.arguments = ["-c", script, repoID, destination.path]
        process.environment = pythonEnvironment

        let logs = modelsDirectory.deletingLastPathComponent().appendingPathComponent("Logs")
        try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        let logURL = logs.appendingPathComponent("model-download.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        let logHandle = try? FileHandle(forWritingTo: logURL)
        logHandle?.seekToEndOfFile()
        process.standardOutput = logHandle
        process.standardError = logHandle
        process.terminationHandler = { [weak self] finished in
            logHandle?.closeFile()
            DispatchQueue.main.async {
                guard let self else { return }
                let wasPaused = self.pausedModel == name
                self.downloadProcess = nil
                self.currentDownloadingModel = nil
                if !wasPaused {
                    let success = finished.terminationStatus == 0
                        && self.isInstalled(name: name)
                    self.completionCallback?(success)
                }
            }
        }

        do {
            try process.run()
            downloadProcess = process
        } catch {
            currentDownloadingModel = nil
            downloadProcess = nil
            completion(false)
        }
    }

    func pauseDownload() {
        guard let name = currentDownloadingModel else { return }
        pausedModel = name
        downloadProcess?.terminate()
    }

    private func isInstalled(name: String) -> Bool {
        if case .installed = checkModelStatus(name: name) { return true }
        return false
    }

    private var pythonPath: URL? {
        if EnvironmentConfigManager.shared.mode == .custom {
            let path = EnvironmentConfigManager.shared.customConfig.pythonPath
            if !path.isEmpty && FileManager.default.isExecutableFile(atPath: path) {
                return URL(fileURLWithPath: path)
            }
        }
        if let resources = Bundle.main.resourceURL {
            let bundled = resources.appendingPathComponent("PythonRuntime/bin/python3")
            if FileManager.default.isExecutableFile(atPath: bundled.path) { return bundled }
        }
        if let path = ProcessInfo.processInfo.environment["TTS_DEV_PYTHON"],
           FileManager.default.isExecutableFile(atPath: path) {
            return URL(fileURLWithPath: path)
        }
        return nil
    }

    private var pythonEnvironment: [String: String] {
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        if EnvironmentConfigManager.shared.mode == .builtin {
            if let resources = Bundle.main.resourceURL {
                let runtime = resources.appendingPathComponent("PythonRuntime")
                environment["PYTHONHOME"] = runtime.path
                environment["PYTHONPATH"] = runtime
                    .appendingPathComponent("lib/python3.11/site-packages").path
            }
        }
        return environment
    }
}
