import Foundation
import AppKit

class DiagnosticsManager {
    static let shared = DiagnosticsManager()
    
    private init() {}
    
    func exportDiagnostics(window: NSWindow) {
        let savePanel = NSSavePanel()
        savePanel.allowedContentTypes = [.zip]
        savePanel.nameFieldStringValue = "QwenTTS_Diagnostics_\(Date().timeIntervalSince1970).zip"
        savePanel.title = "导出诊断包"
        
        savePanel.beginSheetModal(for: window) { [weak self] response in
            if response == .OK, let url = savePanel.url {
                self?.generateZip(to: url)
            }
        }
    }
    
    private func generateZip(to url: URL) {
        // Collect logs, config, and system info
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try? FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        
        defer { try? FileManager.default.removeItem(at: tempDir) }
        
        // Write system info
        let sysInfo = """
        macOS Version: \(ProcessInfo.processInfo.operatingSystemVersionString)
        Processor: \(ProcessInfo.processInfo.processorCount) cores
        Physical Memory: \(ProcessInfo.processInfo.physicalMemory / (1024 * 1024 * 1024)) GB
        """
        try? sysInfo.write(to: tempDir.appendingPathComponent("system_info.txt"), atomically: true, encoding: .utf8)
        
        // Copy App Support data selectively to avoid exposing user text and tokens
        if let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?.appendingPathComponent("QwenTTS", isDirectory: true) {
            let dataDir = appSupport.appendingPathComponent("Data")
            let snapshotDataDir = tempDir.appendingPathComponent("data_snapshot")
            try? FileManager.default.createDirectory(at: snapshotDataDir, withIntermediateDirectories: true)
            
            // 1. Copy runtime events (contains only system/crash logs)
            let eventsFile = dataDir.appendingPathComponent("runtime_events.jsonl")
            if FileManager.default.fileExists(atPath: eventsFile.path) {
                try? FileManager.default.copyItem(at: eventsFile, to: snapshotDataDir.appendingPathComponent("runtime_events.jsonl"))
            }
            
            // 2. Read config.json, remove pairing token, and write to snapshot
            let configFile = dataDir.appendingPathComponent("config.json")
            if let data = try? Data(contentsOf: configFile),
               var configDict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                configDict.removeValue(forKey: "extension_pairing_token")
                if let scrubbedData = try? JSONSerialization.data(withJSONObject: configDict, options: .prettyPrinted) {
                    try? scrubbedData.write(to: snapshotDataDir.appendingPathComponent("config.json"))
                }
            }
            // Do NOT copy saved_for_later.json, url_jobs.json, podcast_jobs.json as they may contain user text
        }
        
        // Use ditto to zip
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
        process.arguments = ["-c", "-k", "--sequesterRsrc", "--keepParent", tempDir.path, url.path]
        try? process.run()
        process.waitUntilExit()
    }
}
