import AppKit

let app = NSApplication.shared
class DummyTarget: NSObject {
    @objc func click() {
        print("Clicked!")
    }
}
let target = DummyTarget()
let item = NSMenuItem(title: "Test", action: #selector(target.click), keyEquivalent: "")
item.target = target
if let action = item.action {
    NSApp.sendAction(action, to: item.target, from: item)
}
