import AppKit

@MainActor
class ApplicationCoordinator {
    let stateStore = AppStateStore()
    let processManager = BackendProcessManager()
    var statusItemController: StatusItemController?
    var mainWindowController: MainWindowController?

    var setupWizardController: SetupWizardWindowController?

    func start() {
        print("[Coordinator] ApplicationCoordinator start hook.")
        CrashReporter.shared.checkPreviousCrash()
        
        // 0. 注入集中状态源，作为快照唯一数据源
        processManager.stateStore = stateStore

        // 1. 初始化 StatusItem 并渲染
        statusItemController = StatusItemController(coordinator: self)
        statusItemController?.setup()

        // 2. 挂载播放状态轮询回调
        processManager.onPlaybackUpdate = { [weak self] title, progress, playing, paused in
            DispatchQueue.main.async {
                self?.stateStore.updatePlayback(title: title, progress: progress, playing: playing, paused: paused)
                if let popoverVC = self?.statusItemController?.popover?.contentViewController as? PlaybackPopoverController {
                    popoverVC.updateUI()
                }
            }
        }

        // DEBUG: 若能自动探测到 dev 环境（conda 后端 + 模型齐全），跳过模型向导直接启动
        #if DEBUG
        if processManager.seedDevEnvironmentIfNeeded() {
            print("[Coordinator] Dev environment detected — skipping setup wizard.")
            startBackend()
            openMainWindow()
            return
        }
        #endif

        // 判断是否需要启动向导
        let hasCompletedWizard = UserDefaults.standard.bool(forKey: "hasCompletedWizard")
        let modelStatus = ModelManager.shared.checkModelStatus(name: "Qwen3-TTS-0.6B")
        var needsWizard = !hasCompletedWizard
        if case .missing = modelStatus { needsWizard = true }
        
        if needsWizard {
            setupWizardController = SetupWizardWindowController { [weak self] in
                UserDefaults.standard.set(true, forKey: "hasCompletedWizard")
                self?.setupWizardController?.close()
                self?.setupWizardController = nil
                self?.startBackend()
                self?.openMainWindow()
            }
            setupWizardController?.showWindow(nil)
            NSApp.activate(ignoringOtherApps: true)
        } else {
            startBackend()
        }
    }

    private func startBackend() {
        // 3. 拉起并监控 Python 后端
        processManager.startBackend { [weak self] state in
            DispatchQueue.main.async {
                self?.stateStore.updateBackendState(state)
                self?.statusItemController?.updateStatus(state: state)
                
                // 若 popover 目前是展开的，通知其刷新 UI 数据
                if let popoverVC = self?.statusItemController?.popover?.contentViewController as? PlaybackPopoverController {
                    popoverVC.updateUI()
                }
            }
        }
    }

    func stop() {
        print("[Coordinator] ApplicationCoordinator stop hook.")
        processManager.stopBackend()
    }

    func readClipboard() {
        if let text = NSPasteboard.general.string(forType: .string) {
            print("[Coordinator] Reading clipboard text: \(text.prefix(20))...")
            processManager.readClipboard(text: text)
        } else {
            print("[Coordinator] Clipboard empty or contains non-text data.")
        }
    }

    func stopPlayback() {
        processManager.triggerAction("stop")
    }

    func pausePlayback() {
        processManager.triggerAction("pause")
    }

    func resumePlayback() {
        processManager.triggerAction("resume")
    }

    func nextPlayback() {
        processManager.triggerAction("next")
    }

    func prevPlayback() {
        processManager.triggerAction("prev")
    }
    
    func openMainWindow() {
        if mainWindowController == nil {
            mainWindowController = MainWindowController(coordinator: self)
        }
        NSApp.setActivationPolicy(.regular)
        mainWindowController?.window?.setContentSize(NSSize(width: 850, height: 550))
        mainWindowController?.window?.center()
        mainWindowController?.showWindow(nil)
        mainWindowController?.window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func openSettings() {
        openMainWindow()
        if let tabVC = mainWindowController?.window?.contentViewController as? MainTabViewController {
            tabVC.selectedTabViewItemIndex = 4
        }
    }

    func openDiagnostics() {
        print("[Coordinator] Action: Open Diagnostics.")
        if let window = NSApp.keyWindow ?? NSApp.mainWindow {
            DiagnosticsManager.shared.exportDiagnostics(window: window)
        } else {
            // Fallback if no window is open
            openMainWindow()
            if let window = mainWindowController?.window {
                DiagnosticsManager.shared.exportDiagnostics(window: window)
            }
        }
    }
}

