import AppKit

// Headless 诊断模式：在 NSApplication 注册之前拦截，跑环境检查并按结果退出。
// 供 CI / 故障排查 / 干净用户验证使用（无 GUI 事件循环）：
//   TTS_APP_SUPPORT_PATH=/tmp/clean QwenTTS.app/Contents/MacOS/QwenTTS --diagnose
// 退出码：0 = 诊断通过（Wizard 会放行）；1 = 有阻塞项（Wizard 会拦截）。
if CommandLine.arguments.contains("--diagnose") {
    let code: Int32 = MainActor.assumeIsolated {
        let items = EnvironmentDiagnostics.run()
        for it in items {
            let tag: String
            switch it.status {
            case .ok: tag = "OK  "
            case .warn: tag = "WARN"
            case .fail: tag = "FAIL"
            }
            print("[\(tag)] \(it.name): \(it.detail)")
            if let hint = it.fixHint, it.status != .ok { print("       ↳ \(hint)") }
        }
        let ok = EnvironmentDiagnostics.canProceed(items)
        print(ok ? "\n诊断通过：可进入主界面。" : "\n存在阻塞项（❌）：Wizard 会拦截继续。")
        return ok ? 0 : 1
    }
    exit(code)
}

let application = NSApplication.shared
let applicationDelegate = AppDelegate()
application.delegate = applicationDelegate
application.run()
