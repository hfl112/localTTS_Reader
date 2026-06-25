import AppKit

struct UITester {
    static func dumpViewHierarchy() {
        print("[UITester] Dumping UI Hierarchy...")
        
        guard let window = NSApp.windows.first(where: { $0.title == "QwenTTS 控制台" && $0.isVisible }) else {
            print("ERROR: Main window not found")
            return
        }
        
        guard let contentView = window.contentView else {
            print("ERROR: No content view")
            return
        }
        
        var output = ""
        dumpNode(view: contentView, indent: "", output: &output)
        
        let path = "/tmp/qwentts_ui_dump.txt"
        do {
            try output.write(toFile: path, atomically: true, encoding: .utf8)
            print("SMOKE_TEST_PASSED: UI dump written to \(path)")
            exit(0)
        } catch {
            print("ERROR: Failed to write dump: \(error)")
            exit(1)
        }
    }
    
    private static func dumpNode(view: NSView, indent: String, output: inout String) {
        let frame = view.frame
        let className = String(describing: type(of: view))
        
        var extra = ""
        if let label = view as? NSTextField {
            extra += " text='\(label.stringValue.prefix(20))'"
        } else if let btn = view as? NSButton {
            extra += " title='\(btn.title)'"
        } else if let effect = view as? NSVisualEffectView {
            extra += " material=\(effect.material.rawValue)"
        }
        
        output += "\(indent)- \(className) (x:\(frame.origin.x), y:\(frame.origin.y), w:\(frame.width), h:\(frame.height))\(extra)\n"
        
        for child in view.subviews {
            dumpNode(view: child, indent: indent + "  ", output: &output)
        }
    }
}
