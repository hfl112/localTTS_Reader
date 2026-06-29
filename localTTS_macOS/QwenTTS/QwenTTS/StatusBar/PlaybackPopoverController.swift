import AppKit

@MainActor
class PlaybackPopoverController: NSViewController {
    weak var coordinator: ApplicationCoordinator?
    private var statusLabel: NSTextField?
    private var titleLabel: NSTextField?
    private var playButton: NSButton?

    init(coordinator: ApplicationCoordinator?) {
        self.coordinator = coordinator
        super.init(nibName: nil, bundle: nil)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func loadView() {
        // 创建较高容器以容纳多按钮布局
        let containerView = NSView(frame: NSRect(x: 0, y: 0, width: 280, height: 260))
        self.view = containerView

        // 1. 状态文本控件
        let status = NSTextField(labelWithString: "后端状态: 未知")
        status.frame = NSRect(x: 20, y: 220, width: 240, height: 20)
        status.font = NSFont.systemFont(ofSize: 13, weight: .medium)
        containerView.addSubview(status)
        self.statusLabel = status

        // 2. 当前朗读标题与进度控件
        let title = NSTextField(labelWithString: "当前暂无朗读内容")
        title.frame = NSRect(x: 20, y: 195, width: 240, height: 20)
        title.font = NSFont.systemFont(ofSize: 11, weight: .regular)
        title.textColor = NSColor.secondaryLabelColor
        containerView.addSubview(title)
        self.titleLabel = title

        // 3. 上一段按钮
        let prevButton = NSButton(title: "上一段", target: self, action: #selector(clickPrev))
        prevButton.frame = NSRect(x: 20, y: 155, width: 70, height: 24)
        prevButton.bezelStyle = .rounded
        containerView.addSubview(prevButton)

        // 4. 播放/暂停/继续按钮
        let playBtn = NSButton(title: "播放", target: self, action: #selector(clickPlayPause))
        playBtn.frame = NSRect(x: 100, y: 155, width: 80, height: 24)
        playBtn.bezelStyle = .rounded
        containerView.addSubview(playBtn)
        self.playButton = playBtn

        // 5. 下一段按钮
        let nextButton = NSButton(title: "下一段", target: self, action: #selector(clickNext))
        nextButton.frame = NSRect(x: 190, y: 155, width: 70, height: 24)
        nextButton.bezelStyle = .rounded
        containerView.addSubview(nextButton)

        // 6. 停止按钮
        let stopButton = NSButton(title: "停止", target: self, action: #selector(clickStop))
        stopButton.frame = NSRect(x: 20, y: 115, width: 70, height: 24)
        stopButton.bezelStyle = .rounded
        containerView.addSubview(stopButton)

        // 7. 朗读剪贴板按钮
        let clipButton = NSButton(title: "朗读剪贴板", target: self, action: #selector(clickReadClipboard))
        clipButton.frame = NSRect(x: 100, y: 115, width: 160, height: 24)
        clipButton.bezelStyle = .rounded
        containerView.addSubview(clipButton)

        // 8. 打开主窗口按钮（§8.1 高频入口）
        let openMainBtn = NSButton(title: "打开主窗口", target: self, action: #selector(clickOpenMainWindow))
        openMainBtn.frame = NSRect(x: 20, y: 75, width: 130, height: 24)
        openMainBtn.bezelStyle = .rounded
        containerView.addSubview(openMainBtn)

        // 9. 设置按钮（§8.1 高频入口）
        let settingsBtn = NSButton(title: "设置", target: self, action: #selector(clickOpenSettings))
        settingsBtn.frame = NSRect(x: 160, y: 75, width: 100, height: 24)
        settingsBtn.bezelStyle = .rounded
        containerView.addSubview(settingsBtn)

        // 10. 打开播客目录按钮
        let openDirBtn = NSButton(title: "打开播客目录", target: self, action: #selector(clickOpenPodcasts))
        openDirBtn.frame = NSRect(x: 20, y: 35, width: 140, height: 24)
        openDirBtn.bezelStyle = .rounded
        containerView.addSubview(openDirBtn)

        // 11. 关闭弹窗按钮
        let closeButton = NSButton(title: "收起", target: self, action: #selector(closePopover))
        closeButton.frame = NSRect(x: 180, y: 35, width: 80, height: 24)
        closeButton.bezelStyle = .rounded
        containerView.addSubview(closeButton)
    }

    override func viewWillAppear() {
        super.viewWillAppear()
        updateUI()
    }

    func updateUI() {
        guard let stateStore = coordinator?.stateStore else { return }
        
        // 更新状态和标题
        statusLabel?.stringValue = "后端状态: \(stateStore.backendState.rawValue)"
        
        let progSuffix = stateStore.progressText.isEmpty ? "" : " [\(stateStore.progressText)]"
        titleLabel?.stringValue = stateStore.currentTitle.isEmpty ? "当前暂无朗读内容" : "\(stateStore.currentTitle)\(progSuffix)"
        
        // ADR-003: button label from the single reconciled status via the one
        // mapping table (same source as Console — the two buttons can't disagree).
        playButton?.title = PlaybackPresentation(stateStore.playbackStatus).buttonLabel
    }

    @objc private func clickPlayPause() {
        guard let stateStore = coordinator?.stateStore else { return }
        switch PlaybackPresentation(stateStore.playbackStatus).action {
        case .pause: coordinator?.pausePlayback()
        case .resume: coordinator?.resumePlayback()
        case .read: coordinator?.readClipboard()   // popover's idle action = read clipboard
        }
    }

    @objc private func clickStop() {
        coordinator?.stopPlayback()
    }

    @objc private func clickPrev() {
        coordinator?.prevPlayback()
    }

    @objc private func clickNext() {
        coordinator?.nextPlayback()
    }

    @objc private func clickReadClipboard() {
        coordinator?.readClipboard()
    }

    @objc private func clickOpenMainWindow() {
        coordinator?.openMainWindow()
        coordinator?.statusItemController?.popover?.performClose(nil)
    }

    @objc private func clickOpenSettings() {
        coordinator?.openSettings()
        coordinator?.statusItemController?.popover?.performClose(nil)
    }

    @objc private func clickOpenPodcasts() {
        let appSupport = NSHomeDirectory() + "/Library/Application Support/QwenTTS"
        let path = appSupport + "/Podcasts"
        
        // 自动创建以防目录尚不存在导致 Finder 弹窗报错
        try? FileManager.default.createDirectory(atPath: path, withIntermediateDirectories: true, attributes: nil)
        NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: path)
    }

    @objc private func closePopover() {
        coordinator?.statusItemController?.popover?.performClose(nil)
    }
}

