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
            // 安全网：若任一步骤悬挂（窗口/侧边栏未就绪），30s 内强制失败退出，
            // 避免 smoke 进程永久挂起阻塞 CI。
            DispatchQueue.main.asyncAfter(deadline: .now() + 30.0) {
                print("SMOKE_TEST_FAILED: timeout (30s) before completion")
                exit(1)
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
                print("[SmokeTest] Verifying menu actions...")
                guard let menu = self.coordinator?.statusItemController?.menu else {
                    print("SMOKE_TEST_FAILED: Menu not initialized")
                    exit(1)
                }
                print("SMOKE_MENU_READY")

                // Simulate clicking "打开主窗口"
                if let item = menu.items.first(where: { $0.title == "打开主窗口" }), let action = item.action {
                    NSApp.sendAction(action, to: item.target, from: item)
                }

                DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
                    let isWindowVisible = NSApp.windows.contains { window in
                        return window.title == "QwenTTS 控制台" && window.isVisible
                    }
                    guard isWindowVisible else {
                        print("SMOKE_TEST_FAILED: Main window did not appear")
                        exit(1)
                    }
                    print("SMOKE_MAIN_WINDOW_VISIBLE")

                    // 侧边栏导航到「内容中心」——smoke 与 dump-ui 两条路径共用。
                    guard self.smokeNavigateSidebar(to: "内容中心") else {
                        print("SMOKE_TEST_FAILED: Sidebar navigation failed (内容中心)")
                        exit(1)
                    }
                    print("SMOKE_SIDEBAR_NAV_OK")

                    let finish = {
                        if isDumpUI {
                            UITester.dumpViewHierarchy()
                        } else {
                            print("SMOKE_TEST_PASSED: Main window is visible")
                            exit(0)
                        }
                    }

                    // --mock-backend 下额外校验 snapshot 已同步进 AppStateStore
                    // （SMOKE_SNAPSHOT_SYNC_OK 依赖 mock 驱动 /snapshot）。
                    if args.contains("--mock-backend") {
                        self.smokeWaitForSnapshot(attempts: 16) { synced in
                            guard synced else {
                                print("SMOKE_TEST_FAILED: snapshot not synced from mock backend")
                                exit(1)
                            }
                            print("SMOKE_SNAPSHOT_SYNC_OK")
                            // --smoke-drive-read:驱动一次 URL 朗读,验证失败呈现(流程 B/D)。
                            // surfaceActionableError 会打印 [ConsoleError] 供外部 grep。
                            if args.contains("--smoke-drive-read") {
                                self.smokeDriveConsoleRead("https://youtu.be/mock")
                                DispatchQueue.main.asyncAfter(deadline: .now() + 5.0) {
                                    print("SMOKE_DRIVE_DONE")
                                    finish()
                                }
                            } else {
                                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5, execute: finish)
                            }
                        }
                    } else {
                        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0, execute: finish)
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

    /// Smoke 辅助：定位主窗口里的 ConsoleViewController（tab 0）并驱动一次即时朗读。
    private func smokeDriveConsoleRead(_ text: String) {
        guard let mainWin = NSApp.windows.first(where: { $0.title == "QwenTTS 控制台" }),
              let rootVC = mainWin.contentViewController,
              let splitVC = rootVC.children.first as? NSSplitViewController else {
            print("SMOKE_TEST_FAILED: cannot locate split view for drive-read")
            exit(1)
        }
        let consoleVC = splitVC.splitViewItems
            .compactMap { ($0.viewController as? NSTabViewController) }
            .first?
            .tabViewItems.first?.viewController as? ConsoleViewController
        guard let consoleVC = consoleVC else {
            print("SMOKE_TEST_FAILED: cannot locate ConsoleViewController for drive-read")
            exit(1)
        }
        print("[SmokeTest] Driving Console instant-read with: \(text)")
        MainActor.assumeIsolated { consoleVC.smokeDriveInstantRead(text) }
    }

    /// Smoke 辅助：轮询等待 AppStateStore 收到 mock 驱动的 snapshot（每 250ms，最多 attempts 次）。
    /// 命中条件：lastSnapshot 来自 mock-backend 且处于播放态，确认快照链路打通。
    private func smokeWaitForSnapshot(attempts: Int, _ done: @escaping (Bool) -> Void) {
        guard attempts > 0 else { done(false); return }
        // 本方法仅由主线程上的 asyncAfter 闭包调用,可安全断言主 actor 隔离以读取
        // AppStateStore(@MainActor)的 lastSnapshot。
        // sync 判定只看 snapshot 是否成功解码并流入 AppStateStore(instance_id 命中 mock),
        // 不绑定具体播放态——idle/speaking/paused 等 fixture 都应视为「已同步」。
        let snap = MainActor.assumeIsolated { self.coordinator?.stateStore.lastSnapshot }
        if snap?.instance_id == "mock-backend" {
            done(true)
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in
            self?.smokeWaitForSnapshot(attempts: attempts - 1, done)
        }
    }

    /// Smoke / dump-ui 辅助：在主窗口侧边栏中按标题查找导航按钮并触发其动作。
    /// 返回是否成功找到并点击，供 smoke 路径据此输出 SMOKE_SIDEBAR_NAV_OK 或失败标记。
    @discardableResult
    private func smokeNavigateSidebar(to title: String) -> Bool {
        guard let mainWin = NSApp.windows.first(where: { $0.title == "QwenTTS 控制台" }),
              let rootVC = mainWin.contentViewController,
              let splitVC = rootVC.children.first as? NSSplitViewController,
              let sidebarItem = splitVC.splitViewItems.first else {
            return false
        }
        let sidebarVC = sidebarItem.viewController
        var foundBtn: NSButton?
        func search(view: NSView) {
            if foundBtn != nil { return }
            if let btn = view as? NSButton, btn.title == title {
                foundBtn = btn
                return
            }
            for child in view.subviews { search(view: child) }
        }
        search(view: sidebarVC.view)
        guard let btn = foundBtn, let action = btn.action else { return false }
        print("[SmokeTest] Navigating sidebar -> \(title)")
        NSApp.sendAction(action, to: btn.target, from: btn)
        return true
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
