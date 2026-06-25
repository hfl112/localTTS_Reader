import AppKit

@MainActor
class StatusItemController: NSObject {
    weak var coordinator: ApplicationCoordinator?
    var statusItem: NSStatusItem?
    var popover: NSPopover?
    var menu: NSMenu?

    init(coordinator: ApplicationCoordinator) {
        self.coordinator = coordinator
    }

    func setup() {
        // 创建状态栏 Item，分配动态宽度
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        guard let button = statusItem?.button else { return }
        
        button.title = "QwenTTS"
        button.target = self
        button.action = #selector(statusItemClicked(_:))
        // 接受左键和右键释放事件，做针对性的交互分流
        button.sendAction(on: [.leftMouseUp, .rightMouseUp])

        // 创建 Popover 实例并挂载
        popover = NSPopover()
        popover?.behavior = .transient
        popover?.contentViewController = PlaybackPopoverController(coordinator: coordinator)

        setupMenu()
    }

    private func setupMenu() {
        let newMenu = NSMenu()
        newMenu.addItem(NSMenuItem(title: "朗读剪贴板", action: #selector(readClipboard), keyEquivalent: "c"))
        newMenu.addItem(NSMenuItem.separator())
        newMenu.addItem(NSMenuItem(title: "打开主窗口", action: #selector(openMainWindow), keyEquivalent: "m"))
        newMenu.addItem(NSMenuItem(title: "设置", action: #selector(openSettings), keyEquivalent: ","))
        newMenu.addItem(NSMenuItem(title: "诊断", action: #selector(openDiagnostics), keyEquivalent: "d"))
        newMenu.addItem(NSMenuItem.separator())
        newMenu.addItem(NSMenuItem(title: "退出", action: #selector(quitApp), keyEquivalent: "q"))
        
        for item in newMenu.items {
            item.target = self
        }
        self.menu = newMenu
    }

    @objc func statusItemClicked(_ sender: NSStatusBarButton) {
        let event = NSApp.currentEvent
        if event?.type == .rightMouseUp {
            // 右键：呼出快捷 NSMenu 菜单
            if let menu = self.menu {
                // 使用标准 popUp 方法，避免直接挂载 menu 导致的 action 丢失或点击冲突
                menu.popUp(positioning: nil, at: NSPoint(x: 0, y: sender.bounds.height + 8), in: sender)
            }
        } else {
            // 左键：展示大面板 Popover
            togglePopover(sender)
        }
    }

    func togglePopover(_ sender: AnyObject?) {
        guard let button = statusItem?.button else { return }
        if popover?.isShown == true {
            popover?.performClose(sender)
        } else {
            popover?.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        }
    }

    func updateStatus(state: BackendState) {
        statusItem?.button?.title = "QwenTTS (\(state.rawValue))"
    }

    @objc func readClipboard(_ sender: Any?) {
        coordinator?.readClipboard()
    }
    @objc func openMainWindow(_ sender: Any?) {
        coordinator?.openMainWindow()
    }
    @objc func openSettings(_ sender: Any?) {
        coordinator?.openSettings()
    }
    @objc func openDiagnostics(_ sender: Any?) {
        coordinator?.openDiagnostics()
    }
    @objc func quitApp(_ sender: Any?) {
        NSApp.terminate(nil)
    }
}
