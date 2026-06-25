import Foundation

/// 首启自助 / 故障排查用的本地环境诊断。检查项与 `BackendProcessManager` 实际使用的
/// 路径保持一致（dev 自定义配置优先，否则 app bundle 内的 Resources），这样"诊断通过"
/// 就意味着后端真能拉起。纯本地、无网络。
@MainActor
enum EnvironmentDiagnostics {

    enum Status { case ok, warn, fail }

    struct Item {
        let name: String
        let status: Status
        let detail: String
        /// 失败/警告时给用户的可执行建议（nil 表示无需操作）。
        let fixHint: String?
    }

    /// 解析与 BackendProcessManager 同源的路径：custom 模式且字段非空用之，否则用 bundle。
    private static func resolved(custom: String, bundleSubpath: String) -> String {
        let cfg = EnvironmentConfigManager.shared
        if cfg.mode == .custom, !custom.isEmpty { return custom }
        if let rp = Bundle.main.resourcePath { return rp + bundleSubpath }
        return ""
    }

    /// 运行全部 6 项检查，按 Wizard 顺序返回。
    static func run() -> [Item] {
        let cfg = EnvironmentConfigManager.shared.customConfig
        var items: [Item] = []

        // 1. 系统：macOS 14+ 且 Apple Silicon
        let os = ProcessInfo.processInfo.operatingSystemVersion
        if os.majorVersion < 14 {
            items.append(Item(name: "系统", status: .fail,
                              detail: "需要 macOS 14+（当前 \(os.majorVersion).\(os.minorVersion)）",
                              fixHint: "请升级到 macOS 14 (Sonoma) 或更高版本。"))
        } else if !isAppleSilicon() {
            items.append(Item(name: "系统", status: .warn,
                              detail: "macOS \(os.majorVersion).\(os.minorVersion)，但非 Apple Silicon",
                              fixHint: "建议在 M 系列芯片上运行以获得可用性能。"))
        } else {
            items.append(Item(name: "系统", status: .ok,
                              detail: "macOS \(os.majorVersion).\(os.minorVersion) · Apple Silicon", fixHint: nil))
        }

        // 2. Python 运行时
        let py = resolved(custom: cfg.pythonPath, bundleSubpath: "/PythonRuntime/bin/python3")
        if !py.isEmpty, FileManager.default.isExecutableFile(atPath: py) {
            items.append(Item(name: "Python 运行时", status: .ok, detail: py, fixHint: nil))
        } else {
            items.append(Item(name: "Python 运行时", status: .fail,
                              detail: py.isEmpty ? "未配置" : "不存在或不可执行：\(py)",
                              fixHint: "安装包可能缺少内置 Python，请重新下载完整 DMG；或在「设置 → 运行环境」指定 python。"))
        }

        // 3. 后端脚本 backend.py
        let backend = resolved(custom: cfg.backendPath, bundleSubpath: "/Backend/core/backend.py")
        if !backend.isEmpty, FileManager.default.fileExists(atPath: backend) {
            items.append(Item(name: "后端脚本", status: .ok, detail: backend, fixHint: nil))
        } else {
            items.append(Item(name: "后端脚本", status: .fail,
                              detail: backend.isEmpty ? "未配置" : "缺失：\(backend)",
                              fixHint: "安装包可能已损坏（backend.py 缺失），请重新下载完整 DMG。"))
        }

        // 4. ffmpeg
        let ffmpeg = resolved(custom: cfg.ffmpegPath, bundleSubpath: "/Tools/ffmpeg")
        if !ffmpeg.isEmpty, FileManager.default.isExecutableFile(atPath: ffmpeg) {
            items.append(Item(name: "ffmpeg", status: .ok, detail: ffmpeg, fixHint: nil))
        } else {
            items.append(Item(name: "ffmpeg", status: .fail,
                              detail: ffmpeg.isEmpty ? "未配置" : "不存在或不可执行：\(ffmpeg)",
                              fixHint: "当前包缺少 ffmpeg，请重新下载完整 DMG；或在「设置 → 运行环境」指定 ffmpeg。"))
        }

        // 5. 模型（0.6B 或 1.7B 任一即可）
        func modelInstalled(_ name: String) -> Bool {
            if case .installed = ModelManager.shared.checkModelStatus(name: name) { return true }
            return false
        }
        if modelInstalled("Qwen3-TTS-0.6B") {
            items.append(Item(name: "模型", status: .ok, detail: "Qwen3-TTS-0.6B 已安装", fixHint: nil))
        } else if modelInstalled("Qwen3-TTS-1.7B-8bit") {
            items.append(Item(name: "模型", status: .ok, detail: "Qwen3-TTS-1.7B-8bit 已安装", fixHint: nil))
        } else {
            items.append(Item(name: "模型", status: .fail,
                              detail: "未安装任何 TTS 模型",
                              fixHint: "下载 Qwen3-TTS-0.6B（推荐），或选择本机已有的模型目录。"))
        }

        // 6. 参考音频（ICL 锁音所需）
        let refDir = resolved(custom: cfg.referenceAudioPath, bundleSubpath: "/Backend/reference")
        let required = ["bbc_news.wav", "ref_ryan.wav", "ref_serena_zh.wav"]
        let missing = required.filter { !FileManager.default.fileExists(atPath: (refDir as NSString).appendingPathComponent($0)) }
        if !refDir.isEmpty, missing.isEmpty {
            items.append(Item(name: "参考音频", status: .ok, detail: refDir, fixHint: nil))
        } else {
            items.append(Item(name: "参考音频", status: .warn,
                              detail: refDir.isEmpty ? "未配置" : "缺少 \(missing.joined(separator: ", "))",
                              fixHint: "参考音频用于 Serena/Ryan 音色锁定，缺失会影响音色；可重新下载完整 DMG。"))
        }

        return items
    }

    /// 是否可进入主界面：无任何 .fail（.warn 允许继续）。
    static func canProceed(_ items: [Item]) -> Bool {
        !items.contains { $0.status == .fail }
    }

    private static func isAppleSilicon() -> Bool {
        var size = 0
        sysctlbyname("hw.optional.arm64", nil, &size, nil, 0)
        var value: Int32 = 0
        sysctlbyname("hw.optional.arm64", &value, &size, nil, 0)
        return value == 1
    }
}
