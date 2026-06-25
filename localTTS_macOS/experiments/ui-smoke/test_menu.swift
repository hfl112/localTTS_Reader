import AppKit

class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var statusItem: NSStatusItem!
    var menu: NSMenu!
    
    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "Test"
        statusItem.button?.target = self
        statusItem.button?.action = #selector(click)
        statusItem.button?.sendAction(on: [.leftMouseUp, .rightMouseUp])
        
        menu = NSMenu()
        menu.delegate = self
        menu.addItem(NSMenuItem(title: "Hello", action: #selector(hello), keyEquivalent: ""))
        for item in menu.items { item.target = self }
    }
    
    @objc func click() {
        print("Click!")
        statusItem.menu = menu
        statusItem.button?.performClick(nil)
        statusItem.menu = nil
    }
    
    @objc func hello() {
        print("Hello clicked!")
        exit(0)
    }

    func menuDidClose(_ menu: NSMenu) {
        print("Menu closed")
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
    if let btn = delegate.statusItem.button {
        delegate.click()
    }
}
app.run()
