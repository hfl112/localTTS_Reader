import AppKit

class CrashReporter {
    static let shared = CrashReporter()
    private let crashKey = "app_was_running"
    
    private init() {}
    
    func checkPreviousCrash() {
        let defaults = UserDefaults.standard
        if defaults.bool(forKey: crashKey) {
            print("[CrashReporter] Detected abnormal termination on previous run.")
            // Log this to the diagnostics log
            logCrash()
        }
        
        // Set flag for current run
        defaults.set(true, forKey: crashKey)
        defaults.synchronize()
        
        // Handle normal termination
        NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification,
            object: nil,
            queue: .main
        ) { _ in
            defaults.set(false, forKey: self.crashKey)
            defaults.synchronize()
        }
        
        // Catch Swift uncaught exceptions
        NSSetUncaughtExceptionHandler { exception in
            let crashLog = """
            Uncaught Exception: \(exception.name.rawValue)
            Reason: \(exception.reason ?? "Unknown")
            Stack Trace: \(exception.callStackSymbols.joined(separator: "\n"))
            """
            CrashReporter.shared.writeCrashLog(crashLog)
        }
    }
    
    private func logCrash() {
        let msg = "App was not terminated normally on the last run."
        writeCrashLog(msg)
    }
    
    private func writeCrashLog(_ text: String) {
        let date = ISO8601DateFormatter().string(from: Date())
        let logEntry = "[\(date)] CRASH: \(text)\n"
        
        if let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?.appendingPathComponent("QwenTTS", isDirectory: true) {
            let logFile = appSupport.appendingPathComponent("Data/runtime_events.jsonl")
            if let data = logEntry.data(using: .utf8) {
                if let fileHandle = try? FileHandle(forWritingTo: logFile) {
                    fileHandle.seekToEndOfFile()
                    fileHandle.write(data)
                    fileHandle.closeFile()
                }
            }
        }
    }
}
