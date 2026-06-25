import AppKit

class AppDelegate: NSObject, NSApplicationDelegate {
    var coordinator: ApplicationCoordinator?

    func applicationDidFinishLaunching(_ notification: Notification) {
        UserDefaults.standard.set(300, forKey: "NSInitialToolTipDelay")
        let logPath = "/tmp/qwentts_debug.log"
        let msg = "App started!\n"
        if let data = msg.data(using: .utf8) {
            if FileManager.default.fileExists(atPath: logPath) {
                if let fileHandle = try? FileHandle(forWritingTo: URL(fileURLWithPath: logPath)) {
                    fileHandle.seekToEndOfFile()
                    fileHandle.write(data)
                    fileHandle.closeFile()
                }
            } else {
                try? data.write(to: URL(fileURLWithPath: logPath))
            }
        }
        
        // 设置主菜单：菜单栏 App 默认无主菜单，会导致文本框内 Cmd+C/V/X/A/Z 失效，
        // 因此必须提供标准“编辑”菜单把这些快捷键接入响应链。
        setupMainMenu()

        coordinator = ApplicationCoordinator()
        coordinator?.start()
        let args = ProcessInfo.processInfo.arguments
        let isSmokeTest = args.contains("--smoke-test")
        let isDumpUI = args.contains("--dump-ui")
        
        if isSmokeTest || isDumpUI {
            print("[SmokeTest] Starting UI smoke test...")
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
                print("[SmokeTest] Verifying menu actions...")
                guard let menu = self.coordinator?.statusItemController?.menu else {
                    print("SMOKE_TEST_FAILED: Menu not initialized")
                    exit(1)
                }
                
                // Simulate clicking "打开主窗口"
                if let item = menu.items.first(where: { $0.title == "打开主窗口" }), let action = item.action {
                    NSApp.sendAction(action, to: item.target, from: item)
                }
                
                DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
                    let isWindowVisible = NSApp.windows.contains { window in
                        return window.title == "QwenTTS 控制台" && window.isVisible
                    }
                    if isWindowVisible {
                        if isDumpUI {
                            // Try to find the SidebarViewController and click "内容中心"
                            if let mainWin = NSApp.windows.first(where: { $0.title == "QwenTTS 控制台" }),
                               let rootVC = mainWin.contentViewController,
                               let splitVC = rootVC.children.first as? NSSplitViewController,
                               let sidebarItem = splitVC.splitViewItems.first,
                               let sidebarVC = sidebarItem.viewController as? NSViewController {
                                
                                // Find button with tag 1 in sidebarVC's view
                                var foundBtn: NSButton?
                                func search(view: NSView) {
                                    if let btn = view as? NSButton, btn.tag == 1, btn.title == "内容中心" {
                                        foundBtn = btn
                                        return
                                    }
                                    for child in view.subviews {
                                        search(view: child)
                                    }
                                }
                                search(view: sidebarVC.view)
                                
                                if let btn = foundBtn {
                                    print("[SmokeTest] Found sidebar button, clicking...")
                                    if let action = btn.action {
                                        NSApp.sendAction(action, to: btn.target, from: btn)
                                    }
                                } else {
                                    print("[SmokeTest] Could not find sidebar button")
                                }
                                
                                DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
                                    UITester.dumpViewHierarchy()
                                }
                            } else {
                                UITester.dumpViewHierarchy()
                            }
                        } else {
                            print("SMOKE_TEST_PASSED: Main window is visible")
                            exit(0)
                        }
                    } else {
                        print("SMOKE_TEST_FAILED: Main window did not appear")
                        exit(1)
                    }
                }
            }
        } else {
            // 自动展开 Popover 以提示用户已启动
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                self.coordinator?.statusItemController?.togglePopover(nil)
            }
            coordinator?.openMainWindow()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        coordinator?.stop()
    }

    /// 构建标准主菜单。重点是“编辑”菜单——它把 cut:/copy:/paste:/selectAll:/undo:/redo:
    /// 这些标准动作及其快捷键接入第一响应者（文本框的 field editor），否则在菜单栏 App
    /// 里这些常见快捷键不会生效。
    private func setupMainMenu() {
        let mainMenu = NSMenu()

        // 应用菜单
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        appMenuItem.submenu = appMenu
        appMenu.addItem(withTitle: "隐藏 QwenTTS", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "退出 QwenTTS", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        // 编辑菜单
        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "编辑")
        editMenuItem.submenu = editMenu
        editMenu.addItem(withTitle: "撤销", action: Selector(("undo:")), keyEquivalent: "z")
        let redoItem = editMenu.addItem(withTitle: "重做", action: Selector(("redo:")), keyEquivalent: "Z")
        redoItem.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "拷贝", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

        NSApp.mainMenu = mainMenu
    }
}
