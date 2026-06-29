import AppKit

@MainActor
class ConsoleViewController: NSViewController {
    weak var coordinator: ApplicationCoordinator?
    
    // Top: Input Composer
    private let topComposerCard = NSVisualEffectView()
    private let inputTextField = NSTextField()
    private let modeSegmentedControl = NSSegmentedControl()
    private let instantReadBtn = NSButton()
    private let saveBtn = NSButton()
    private let podcastBtn = NSButton()
    
    // Center: Live Reading Card
    private let centerReadingCard = NSVisualEffectView()
    private let statusIndicator = NSView()
    private let statusLabel = NSTextField(labelWithString: "LIVE READING")
    
    private var currentChunks: [String] = []
    private var currentSentenceIndex = 0
    private var transcriptLabels: [NSTextField] = []
    // ADR-003: no local snapshot cache — the button/render read the single
    // reconciled truth from AppStateStore (coordinator.stateStore.playbackStatus).
    /// AppStateStore 订阅 token；不再自己轮询 /snapshot，改为订阅集中状态源。
    private var snapshotListenerToken: Int?
    

    
    // Bottom: Playback Control Bar
    private let bottomControlBar = NSView()
    private let timeLabel = NSTextField(labelWithString: "1:14 / 2:30")
    
    private let rewind15Btn = NSButton()
    private let prevBtn = NSButton()
    private let playBtn = NSButton()
    private let pauseBtn = NSButton()
    private let stopBtn = NSButton()
    private let nextBtn = NSButton()
    private let forward15Btn = NSButton()
    
    private let voicePopUp = NSPopUpButton()
    private let speedPopUp = NSPopUpButton()
    
    init(coordinator: ApplicationCoordinator?) {
        self.coordinator = coordinator
        super.init(nibName: nil, bundle: nil)
    }
    
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
    
    override func loadView() {
        self.view = NSView(frame: NSRect(x: 0, y: 0, width: 800, height: 700))
    }
    
    override func viewDidLoad() {
        super.viewDidLoad()
        setupUI()
    }
    
    override func viewDidAppear() {
        super.viewDidAppear()
        updateTranscriptState(animated: false)
    }
    
    override func viewDidLayout() {
        super.viewDidLayout()
        // Keep gradient mask in sync with scroll view size
        sentencesScrollView.layer?.mask?.frame = sentencesScrollView.bounds
    }
    
    override func viewWillAppear() {
        super.viewWillAppear()
        subscribeToStateStore()
    }

    override func viewWillDisappear() {
        super.viewWillDisappear()
        unsubscribeFromStateStore()
    }
    
    private func setupUI() {
        let mainStack = NSStackView()
        mainStack.orientation = .vertical
        mainStack.alignment = .centerX
        mainStack.spacing = 24
        mainStack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(mainStack)
        
        let widthConstraint = mainStack.widthAnchor.constraint(equalTo: view.widthAnchor, constant: -80)
        widthConstraint.priority = .defaultHigh
        
        NSLayoutConstraint.activate([
            mainStack.topAnchor.constraint(equalTo: view.topAnchor, constant: 32),
            mainStack.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            mainStack.leadingAnchor.constraint(greaterThanOrEqualTo: view.leadingAnchor, constant: 40),
            mainStack.trailingAnchor.constraint(lessThanOrEqualTo: view.trailingAnchor, constant: -40),
            widthConstraint,
            mainStack.widthAnchor.constraint(lessThanOrEqualToConstant: 1000),
            mainStack.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -24)
        ])
        
        setupTopComposer(in: mainStack)
        setupCenterReadingCard(in: mainStack)
        setupBottomControlBar(in: mainStack)
    }
    
    // MARK: - Top Composer
    private func setupTopComposer(in mainStack: NSStackView) {
        let shadowContainer = NSView()
        shadowContainer.translatesAutoresizingMaskIntoConstraints = false
        shadowContainer.wantsLayer = true
        
        let shadow = NSShadow()
        shadow.shadowColor = NSColor.black.withAlphaComponent(0.06)
        shadow.shadowOffset = NSSize(width: 0, height: -2)
        shadow.shadowBlurRadius = 8
        shadowContainer.shadow = shadow
        
        topComposerCard.material = .popover
        topComposerCard.blendingMode = .behindWindow
        topComposerCard.state = .active
        topComposerCard.wantsLayer = true
        topComposerCard.layer?.cornerRadius = 12
        if #available(macOS 10.15, *) {
            topComposerCard.layer?.cornerCurve = .continuous
        }
        topComposerCard.layer?.masksToBounds = true
        topComposerCard.layer?.borderWidth = 1.0
        topComposerCard.layer?.borderColor = NSColor.white.withAlphaComponent(0.15).cgColor
        topComposerCard.translatesAutoresizingMaskIntoConstraints = false
        
        shadowContainer.addSubview(topComposerCard)
        NSLayoutConstraint.activate([
            topComposerCard.topAnchor.constraint(equalTo: shadowContainer.topAnchor),
            topComposerCard.bottomAnchor.constraint(equalTo: shadowContainer.bottomAnchor),
            topComposerCard.leadingAnchor.constraint(equalTo: shadowContainer.leadingAnchor),
            topComposerCard.trailingAnchor.constraint(equalTo: shadowContainer.trailingAnchor)
        ])
        
        let composerStack = NSStackView()
        composerStack.orientation = .vertical
        composerStack.spacing = 12
        composerStack.translatesAutoresizingMaskIntoConstraints = false
        
        // Input Area
        inputTextField.placeholderAttributedString = NSAttributedString(
            string: "Paste text or URL here to read...",
            attributes: [.foregroundColor: NSColor.secondaryLabelColor]
        )
        inputTextField.textColor = .labelColor
        inputTextField.font = NSFont.systemFont(ofSize: 15)
        inputTextField.focusRingType = .none
        inputTextField.isBordered = false
        inputTextField.drawsBackground = false
        inputTextField.heightAnchor.constraint(equalToConstant: 24).isActive = true
        
        let divider = NSBox()
        divider.boxType = .separator
        divider.alphaValue = 0.5
        
        // Toolbar Area
        let toolbarStack = NSStackView()
        toolbarStack.orientation = .horizontal
        toolbarStack.alignment = .centerY
        
        // Modes
        modeSegmentedControl.segmentCount = 4
        modeSegmentedControl.setLabel("原文", forSegment: 0)
        modeSegmentedControl.setImage(NSImage(systemSymbolName: "doc.text", accessibilityDescription: nil), forSegment: 0)
        modeSegmentedControl.setToolTip("原文朗读", forSegment: 0)
        
        modeSegmentedControl.setLabel("翻译", forSegment: 1)
        modeSegmentedControl.setImage(NSImage(systemSymbolName: "globe", accessibilityDescription: nil), forSegment: 1)
        modeSegmentedControl.setToolTip("翻译后朗读", forSegment: 1)
        
        modeSegmentedControl.setLabel("总结", forSegment: 2)
        modeSegmentedControl.setImage(NSImage(systemSymbolName: "person.2.wave.2", accessibilityDescription: nil), forSegment: 2)
        modeSegmentedControl.setToolTip("双人翻译总结", forSegment: 2)
        
        modeSegmentedControl.setLabel("双人", forSegment: 3)
        modeSegmentedControl.setImage(NSImage(systemSymbolName: "person.2.badge.gearshape", accessibilityDescription: nil), forSegment: 3)
        modeSegmentedControl.setToolTip("双人翻译播客", forSegment: 3)
        
        modeSegmentedControl.selectedSegment = 0
        modeSegmentedControl.segmentStyle = .capsule
        modeSegmentedControl.font = NSFont.systemFont(ofSize: 12)
        if #available(macOS 10.14, *) {
            modeSegmentedControl.selectedSegmentBezelColor = .controlAccentColor
        }
        modeSegmentedControl.target = self
        modeSegmentedControl.action = #selector(handleModeChange(_:))
        
        // Actions
        func styleActionBtn(_ btn: NSButton, title: String, icon: String, tooltip: String, isPrimary: Bool = false) {
            btn.title = title
            let config = NSImage.SymbolConfiguration(pointSize: 14, weight: .medium)
            btn.image = NSImage(systemSymbolName: icon, accessibilityDescription: nil)?.withSymbolConfiguration(config)
            btn.isBordered = false
            btn.contentTintColor = isPrimary ? .controlAccentColor : .labelColor
            btn.toolTip = tooltip
        }
        
        styleActionBtn(instantReadBtn, title: "即时阅读", icon: "bolt.fill", tooltip: "即时阅读", isPrimary: true)
        instantReadBtn.target = self
        instantReadBtn.action = #selector(handleInstantRead)
        
        styleActionBtn(saveBtn, title: "稍后", icon: "bookmark.fill", tooltip: "保存稍后阅读")
        saveBtn.target = self
        saveBtn.action = #selector(handleSaveBtn)
        
        styleActionBtn(podcastBtn, title: "播客", icon: "mic.fill", tooltip: "生成播客")
        podcastBtn.target = self
        podcastBtn.action = #selector(handlePodcastBtn)
        
        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        
        toolbarStack.addArrangedSubview(modeSegmentedControl)
        toolbarStack.addArrangedSubview(spacer)
        toolbarStack.addArrangedSubview(instantReadBtn)
        toolbarStack.addArrangedSubview(saveBtn)
        toolbarStack.addArrangedSubview(podcastBtn)
        
        composerStack.addArrangedSubview(inputTextField)
        composerStack.addArrangedSubview(divider)
        composerStack.addArrangedSubview(toolbarStack)
        
        inputTextField.widthAnchor.constraint(equalTo: composerStack.widthAnchor).isActive = true
        divider.widthAnchor.constraint(equalTo: composerStack.widthAnchor).isActive = true
        toolbarStack.widthAnchor.constraint(equalTo: composerStack.widthAnchor).isActive = true
        
        topComposerCard.addSubview(composerStack)
        NSLayoutConstraint.activate([
            composerStack.topAnchor.constraint(equalTo: topComposerCard.topAnchor, constant: 14),
            composerStack.leadingAnchor.constraint(equalTo: topComposerCard.leadingAnchor, constant: 16),
            composerStack.trailingAnchor.constraint(equalTo: topComposerCard.trailingAnchor, constant: -16),
            composerStack.bottomAnchor.constraint(equalTo: topComposerCard.bottomAnchor, constant: -14)
        ])
        
        mainStack.addArrangedSubview(shadowContainer)
        shadowContainer.widthAnchor.constraint(equalTo: mainStack.widthAnchor).isActive = true
    }
    
    // MARK: - Center Reading Card
    private let sentencesScrollView = NSScrollView()
    private let sentencesStack = NSStackView()
    private let emptyStateLabel = NSTextField(labelWithString: "Ready to read.\nPaste text or URL above to begin.")
    private let loadingIndicator = NSProgressIndicator()
    
    private func setupCenterReadingCard(in mainStack: NSStackView) {
        let shadowContainer = NSView()
        shadowContainer.translatesAutoresizingMaskIntoConstraints = false
        shadowContainer.wantsLayer = true
        
        let shadow = NSShadow()
        shadow.shadowColor = NSColor.black.withAlphaComponent(0.10)
        shadow.shadowOffset = NSSize(width: 0, height: -8)
        shadow.shadowBlurRadius = 24
        shadowContainer.shadow = shadow
        
        centerReadingCard.material = .popover
        centerReadingCard.blendingMode = .behindWindow
        centerReadingCard.state = .active
        centerReadingCard.wantsLayer = true
        centerReadingCard.layer?.cornerRadius = 12
        if #available(macOS 10.15, *) {
            centerReadingCard.layer?.cornerCurve = .continuous
        }
        centerReadingCard.layer?.masksToBounds = true
        centerReadingCard.layer?.borderWidth = 1.0
        centerReadingCard.layer?.borderColor = NSColor.white.withAlphaComponent(0.15).cgColor
        centerReadingCard.translatesAutoresizingMaskIntoConstraints = false
        
        shadowContainer.addSubview(centerReadingCard)
        NSLayoutConstraint.activate([
            centerReadingCard.topAnchor.constraint(equalTo: shadowContainer.topAnchor),
            centerReadingCard.bottomAnchor.constraint(equalTo: shadowContainer.bottomAnchor),
            centerReadingCard.leadingAnchor.constraint(equalTo: shadowContainer.leadingAnchor),
            centerReadingCard.trailingAnchor.constraint(equalTo: shadowContainer.trailingAnchor)
        ])
        
        // Status Badge — pinned to top-left, NOT part of centered content
        let statusStack = NSStackView()
        statusStack.orientation = .horizontal
        statusStack.spacing = 6
        statusStack.alignment = .centerY
        statusStack.translatesAutoresizingMaskIntoConstraints = false
        
        statusIndicator.wantsLayer = true
        statusIndicator.layer?.backgroundColor = NSColor.systemGreen.cgColor
        statusIndicator.layer?.cornerRadius = 4
        statusIndicator.translatesAutoresizingMaskIntoConstraints = false
        statusIndicator.widthAnchor.constraint(equalToConstant: 8).isActive = true
        statusIndicator.heightAnchor.constraint(equalToConstant: 8).isActive = true
        statusIndicator.toolTip = "后端状态"
        
        statusLabel.font = NSFont.systemFont(ofSize: 11, weight: .bold)
        statusLabel.textColor = .secondaryLabelColor
        statusLabel.toolTip = "正在朗读"
        
        statusStack.addArrangedSubview(statusIndicator)
        statusStack.addArrangedSubview(statusLabel)
        
        centerReadingCard.addSubview(statusStack)
        
        // Content stack — transcript + progress bar, vertically centered below status
        let contentStack = NSStackView()
        contentStack.orientation = .vertical
        contentStack.alignment = .centerX
        contentStack.spacing = 12
        contentStack.translatesAutoresizingMaskIntoConstraints = false
        
        // Empty State (hidden by default)
        emptyStateLabel.font = NSFont.systemFont(ofSize: 16, weight: .medium)
        emptyStateLabel.textColor = .tertiaryLabelColor
        emptyStateLabel.alignment = .center
        emptyStateLabel.isHidden = true
        
        // Loading State (hidden by default)
        loadingIndicator.style = .spinning
        loadingIndicator.controlSize = .regular
        loadingIndicator.isHidden = true
        
        sentencesScrollView.hasVerticalScroller = false
        sentencesScrollView.drawsBackground = false
        sentencesScrollView.translatesAutoresizingMaskIntoConstraints = false
        sentencesScrollView.wantsLayer = true
        
        sentencesStack.orientation = .vertical
        sentencesStack.alignment = .centerX
        sentencesStack.spacing = 16
        sentencesStack.edgeInsets = NSEdgeInsets(top: 90, left: 0, bottom: 90, right: 0)
        sentencesStack.translatesAutoresizingMaskIntoConstraints = false
        
        sentencesScrollView.documentView = sentencesStack
        
        // Fixed viewport height for rolling lyrics
        sentencesScrollView.heightAnchor.constraint(equalToConstant: 180).isActive = true
        sentencesStack.widthAnchor.constraint(equalTo: sentencesScrollView.widthAnchor).isActive = true
        
        // Gradient fade mask — top and bottom edges fade to transparent
        let gradientMask = CAGradientLayer()
        gradientMask.colors = [
            NSColor.clear.cgColor,
            NSColor.black.cgColor,
            NSColor.black.cgColor,
            NSColor.clear.cgColor
        ]
        gradientMask.locations = [0.0, 0.15, 0.85, 1.0]
        gradientMask.startPoint = CGPoint(x: 0.5, y: 0)
        gradientMask.endPoint = CGPoint(x: 0.5, y: 1)
        gradientMask.frame = CGRect(x: 0, y: 0, width: 1000, height: 180)
        sentencesScrollView.layer?.mask = gradientMask
        
        // Dynamic chunks will be loaded by updateArticleChunks
        
        contentStack.addArrangedSubview(emptyStateLabel)
        contentStack.addArrangedSubview(loadingIndicator)
        contentStack.addArrangedSubview(sentencesScrollView)
        
        sentencesScrollView.widthAnchor.constraint(equalTo: contentStack.widthAnchor).isActive = true
        
        centerReadingCard.addSubview(contentStack)
        
        // Status badge: pinned to top-left of card
        NSLayoutConstraint.activate([
            statusStack.topAnchor.constraint(equalTo: centerReadingCard.topAnchor, constant: 14),
            statusStack.leadingAnchor.constraint(equalTo: centerReadingCard.leadingAnchor, constant: 20)
        ])
        
        // Content stack: vertically centered in the space below the status badge
        NSLayoutConstraint.activate([
            contentStack.topAnchor.constraint(greaterThanOrEqualTo: statusStack.bottomAnchor, constant: 4),
            contentStack.centerYAnchor.constraint(equalTo: centerReadingCard.centerYAnchor, constant: 8),
            contentStack.bottomAnchor.constraint(lessThanOrEqualTo: centerReadingCard.bottomAnchor, constant: -12),
            contentStack.leadingAnchor.constraint(equalTo: centerReadingCard.leadingAnchor, constant: 20),
            contentStack.trailingAnchor.constraint(equalTo: centerReadingCard.trailingAnchor, constant: -20)
        ])
        
        mainStack.addArrangedSubview(shadowContainer)
        shadowContainer.widthAnchor.constraint(equalTo: mainStack.widthAnchor).isActive = true
        
        // Minimum height so the card feels like a dashboard panel
        shadowContainer.heightAnchor.constraint(greaterThanOrEqualToConstant: 240).isActive = true
        shadowContainer.setContentHuggingPriority(.defaultHigh, for: .vertical)
    }
    
    // MARK: - State Management (Mock)
    private func updateTranscriptState(animated: Bool) {
        let duration: TimeInterval = animated ? 0.45 : 0.0
        
        NSAnimationContext.runAnimationGroup({ context in
            context.duration = duration
            context.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
            context.allowsImplicitAnimation = true
            
            for (i, label) in transcriptLabels.enumerated() {
                let dist = abs(i - currentSentenceIndex)
                
                // Unified font: SF Pro Text (en) / PingFang SC (zh) via .systemFont
                if i == currentSentenceIndex {
                    label.font = NSFont.systemFont(ofSize: 15, weight: .semibold)
                    label.animator().textColor = .labelColor
                    label.animator().alphaValue = 1.0
                } else if dist == 1 {
                    label.font = NSFont.systemFont(ofSize: 13, weight: .regular)
                    label.animator().textColor = .secondaryLabelColor
                    label.animator().alphaValue = 0.55
                } else {
                    // 其它所有上下文句子（dist >= 2）不再被完全隐藏，而是以 0.25 不透明度常亮显示，呈现出类似 Apple Music 动态歌词的淡显背景效果
                    label.font = NSFont.systemFont(ofSize: 12, weight: .regular)
                    label.animator().textColor = .tertiaryLabelColor
                    label.animator().alphaValue = 0.25
                }
            }
            
            self.sentencesStack.layoutSubtreeIfNeeded()
            
            if self.currentSentenceIndex >= 0 && self.currentSentenceIndex < self.transcriptLabels.count {
                let targetLabel = self.transcriptLabels[self.currentSentenceIndex]
                let labelFrame = targetLabel.frame
                let scrollViewHeight = self.sentencesScrollView.bounds.height > 0 ? self.sentencesScrollView.bounds.height : 160
                
                var targetY = labelFrame.midY - (scrollViewHeight / 2)
                let maxScrollY = max(0, self.sentencesStack.bounds.height - scrollViewHeight)
                targetY = max(0, min(targetY, maxScrollY))
                
                let targetOrigin = NSPoint(x: 0, y: targetY)
                
                if animated {
                    self.sentencesScrollView.contentView.animator().setBoundsOrigin(targetOrigin)
                } else {
                    self.sentencesScrollView.contentView.bounds.origin = targetOrigin
                }
            }
        }, completionHandler: nil)
    }

    /// ADR-003: one seek path that applies the returned status optimistically.
    private func seek(_ direction: Int) {
        Task {
            if let s = await coordinator?.processManager.apiClient?.seekPlayback(direction: direction) {
                coordinator?.stateStore.applyCommandResult(s)
            }
        }
    }

    @objc private func handleNextSentence() { seek(1) }

    @objc private func handlePrevSentence() { seek(-1) }

    @objc private func handleRewind15() { seek(-3) }

    @objc private func handleForward15() {
        seek(3)
    }
    
    // 三独立键（放弃播放/暂停合一）：
    // ▶ 播放 = 从头播放（输入框有字→读输入；否则当前文章 RESTART_MODE 从头读）。
    // ⏸ 暂停 = 暂停⇄续读切换（续读归此键）。 ⏹ 停止 = 停止。
    // ▶ 播放键：之前暂停→继续；否则→从头播放当前文章。(读新输入是「即时阅读」键的事。)
    @objc private func handlePlayBtn() {
        guard let coordinator = coordinator else { return }
        switch playButtonIntent(for: coordinator.stateStore.playbackStatus) {
        case .resume:
            coordinator.resumePlayback()
        case .restartFromBeginning:
            Task {
                _ = await coordinator.processManager.apiClient?.readText(
                    text: "RESTART_MODE", voice: nil, performanceProfile: nil
                )
            }
        }
    }

    // ⏸ 暂停键：只暂停。 ⏹ 停止键：只停止。
    @objc private func handlePauseBtn() {
        coordinator?.pausePlayback()
    }

    @objc private func handleStopBtn() {
        coordinator?.stopPlayback()
    }
    
    @objc private func handleInstantRead() {
        triggerInstantRead()
    }
    
    @objc private func handleSaveBtn() {
        let text = inputTextField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, let client = coordinator?.processManager.apiClient else { return }
        let isUrl = text.lowercased().hasPrefix("http://") || text.lowercased().hasPrefix("https://")
        let mode = selectedModeString()
        Task {
            guard await ensureLLMConfigured(forMode: mode) else { return }
            // URL：走 /read_url 抓取并按模式处理后保存（双人总结/翻译等）；纯文本：直接保存原文
            let success: Bool
            if isUrl {
                let baseline = await failedJobIDs(isPodcast: false)
                success = await client.readUrl(url: text, mode: mode, save: true)
                if success { watchJobForFailure(isPodcast: false, baseline: baseline) }
            } else {
                success = await client.saveForLater(text: text, source: "web", voice: nil, title: nil)
            }
            if success { inputTextField.stringValue = "" }
            else { surfaceActionableError(message: "保存请求未被后端接受，请确认后端已就绪后重试。", offerEngine: false) }
        }
    }

    @objc private func handlePodcastBtn() {
        let text = inputTextField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, let client = coordinator?.processManager.apiClient else { return }
        let isUrl = text.lowercased().hasPrefix("http://") || text.lowercased().hasPrefix("https://")
        let mode = selectedModeString()
        Task {
            guard await ensureLLMConfigured(forMode: mode) else { return }
            // URL：走 /read_url 抓取并按模式处理后生成播客；纯文本：直接对文本生成播客
            let success: Bool
            if isUrl {
                let urlBaseline = await failedJobIDs(isPodcast: false)
                let podBaseline = await failedJobIDs(isPodcast: true)
                success = await client.readUrl(url: text, mode: mode, podcast: true)
                if success {
                    // URL→播客两段都可能失败：抓取/处理(url_jobs)与播客渲染(podcast jobs)。
                    watchJobForFailure(isPodcast: false, baseline: urlBaseline)
                    watchJobForFailure(isPodcast: true, baseline: podBaseline)
                }
            } else {
                let baseline = await failedJobIDs(isPodcast: true)
                success = await client.generateSinglePodcast(text: text, source: "web", voice: nil, title: nil)
                if success { watchJobForFailure(isPodcast: true, baseline: baseline) }
            }
            if success { inputTextField.stringValue = "" }
            else { surfaceActionableError(message: "生成播客请求未被后端接受，请确认后端已就绪后重试。", offerEngine: false) }
        }
    }

    /// AI 模式（双人总结 / 双人翻译）需要 LLM；未配置 key 时弹窗提示并返回 false。
    /// 普通翻译（Google）和原文不需要，直接放行。
    private func ensureLLMConfigured(forMode mode: String) async -> Bool {
        guard mode == "podcast-discuss" || mode == "podcast-trans" else { return true }
        guard let client = coordinator?.processManager.apiClient else { return false }
        let cfg = await client.fetchEngines()
        let llm = cfg?.llm
        let selected = llm?.selected ?? "gemini"
        let configured: Bool
        if selected == "local" {
            configured = !((llm?.local_model_path ?? "").isEmpty)
        } else {
            configured = !((llm?.keys?[selected] ?? "").isEmpty)
        }
        if !configured {
            let alert = NSAlert()
            alert.messageText = "需要先配置 AI 接口"
            alert.informativeText = "AI 总结 / 双人翻译 需要一个大模型 API。请到「AI 引擎」页填写并检测一个 Key（Gemini / Claude / OpenAI / DeepSeek），然后再试。"
            alert.alertStyle = .informational
            alert.addButton(withTitle: "好的")
            alert.runModal()
            return false
        }
        return true
    }
    
    /// 统一的可执行错误提示(对应文档 §8.4)。offerEngine=true 时提供「打开 AI 引擎」
    /// 按钮跳转配置页。smoke/headless 下不弹模态(避免阻塞),但始终打印 marker 供观测。
    private func surfaceActionableError(message: String, offerEngine: Bool) {
        print("[ConsoleError] \(message)")
        if ProcessInfo.processInfo.arguments.contains("--smoke-test") { return }
        let alert = NSAlert()
        alert.messageText = offerEngine ? "需要配置 AI 引擎" : "操作失败"
        alert.informativeText = message
        alert.alertStyle = .warning
        if offerEngine {
            alert.addButton(withTitle: "打开 AI 引擎")
            alert.addButton(withTitle: "取消")
            if alert.runModal() == .alertFirstButtonReturn {
                NotificationCenter.default.post(name: .qwenShowEngineSettings, object: nil)
            }
        } else {
            alert.addButton(withTitle: "好的")
            alert.runModal()
        }
    }

    /// 错误文案是否指向引擎/密钥问题(决定是否给出「打开 AI 引擎」)。
    private func isEngineError(_ message: String) -> Bool {
        let m = message.lowercased()
        return m.contains("key") || m.contains("api") || message.contains("引擎")
            || message.contains("鉴权") || message.contains("配置")
    }

    /// 提交前记录现有失败 job 的 id,用于之后只对“本次新产生”的失败告警。
    private func failedJobIDs(isPodcast: Bool) async -> Set<String> {
        guard let client = coordinator?.processManager.apiClient else { return [] }
        let jobs = (isPodcast ? await client.fetchPodcastJobs() : await client.fetchUrlJobs()) ?? []
        var ids = Set<String>()
        for job in jobs where (job["status"] as? String ?? "").lowercased() == "failed" {
            if let id = job["job_id"] as? String ?? job["id"] as? String { ids.insert(id) }
        }
        return ids
    }

    /// 提交后短时轮询 job 列表,捕捉本次“新”失败并在前台给出可执行提示
    /// (覆盖流程 D 的无字幕 / 无 key / 超时 / 鉴权等后端处理失败)。
    private func watchJobForFailure(isPodcast: Bool, baseline: Set<String>) {
        guard let client = coordinator?.processManager.apiClient else { return }
        Task {
            for _ in 0..<16 {   // ~8s
                let jobs = (isPodcast ? await client.fetchPodcastJobs() : await client.fetchUrlJobs()) ?? []
                for job in jobs {
                    let status = (job["status"] as? String ?? "").lowercased()
                    let id = job["job_id"] as? String ?? job["id"] as? String ?? ""
                    if status == "failed", !baseline.contains(id) {
                        let err = job["error"] as? String ?? "处理失败"
                        surfaceActionableError(message: err, offerEngine: isEngineError(err))
                        return
                    }
                }
                try? await Task.sleep(for: .milliseconds(500))
            }
        }
    }

    private func selectedModeString() -> String {
        // 必须与后端 reader_service 的 mode 字符串一致
        switch modeSegmentedControl.selectedSegment {
        case 0: return "original"
        case 1: return "translate"
        case 2: return "podcast-discuss"
        case 3: return "podcast-trans"
        default: return "original"
        }
    }
    
    private func selectedVoice() -> String? {
        return nil // Uses backend default
    }
    
    private func selectedPerformanceProfile() -> String? {
        return nil // Uses backend default
    }
    
    /// Smoke 驱动入口:程序化填入文本/URL 并触发即时朗读,用于离线验证流程 B/D
    /// 的失败呈现(配合 --mock-failure)。仅供 --smoke-drive-read 调用。
    func smokeDriveInstantRead(_ text: String) {
        inputTextField.stringValue = text
        triggerInstantRead()
    }

    private func triggerInstantRead() {
        let text = inputTextField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        
        let isUrl = text.lowercased().hasPrefix("http://") || text.lowercased().hasPrefix("https://")
        
        let mode = selectedModeString()
        Task {
            guard let client = coordinator?.processManager.apiClient else { return }
            guard await ensureLLMConfigured(forMode: mode) else { return }
            let success: Bool
            if isUrl {
                let baseline = await failedJobIDs(isPodcast: false)
                success = await client.readUrl(
                    url: text, html: "", translate: false,
                    mode: mode, save: false, podcast: false
                )
                if success { watchJobForFailure(isPodcast: false, baseline: baseline) }
            } else {
                success = await client.readText(
                    text: text,
                    voice: selectedVoice(),
                    performanceProfile: selectedPerformanceProfile(),
                    mode: mode
                )
            }
            if !success {
                surfaceActionableError(message: "朗读请求未被后端接受，请确认后端已就绪后重试。", offerEngine: false)
            }
        }
    }
    
    // MARK: - 订阅集中状态源（替代自身轮询）
    private func subscribeToStateStore() {
        guard let store = coordinator?.stateStore, snapshotListenerToken == nil else { return }
        snapshotListenerToken = store.addSnapshotListener { [weak self] snapshot in
            // AppStateStore 在主线程触发；这里仅做 UI 渲染
            self?.render(snapshot: snapshot)
        }
        // 立即用当前已有快照渲染一次，避免出现等待首个轮询的空窗
        if let snap = store.lastSnapshot {
            render(snapshot: snap)
        } else {
            render(snapshot: nil)
        }
    }

    private func unsubscribeFromStateStore() {
        if let token = snapshotListenerToken {
            coordinator?.stateStore.removeSnapshotListener(token)
            snapshotListenerToken = nil
        }
    }
    
    private func updateArticleChunks(newChunks: [String]) {
        guard newChunks != currentChunks else { return }
        currentChunks = newChunks
        
        // Remove existing labels
        for label in transcriptLabels {
            label.removeFromSuperview()
        }
        transcriptLabels.removeAll()
        
        if newChunks.isEmpty {
            return
        }
        
        for text in newChunks {
            let label = NSTextField(wrappingLabelWithString: text)
            label.alignment = .center
            label.maximumNumberOfLines = 0
            label.lineBreakMode = .byWordWrapping
            label.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
            label.setContentHuggingPriority(.defaultLow, for: .horizontal)
            label.translatesAutoresizingMaskIntoConstraints = false
            
            sentencesStack.addArrangedSubview(label)
            transcriptLabels.append(label)
            
            label.widthAnchor.constraint(equalTo: sentencesStack.widthAnchor, constant: -40).isActive = true
        }
        
        sentencesStack.layoutSubtreeIfNeeded()
    }
    
    /// 用集中状态源推送的 Snapshot 驱动 UI（替代旧的自身轮询 pollStatus）。
    /// snapshot 为 nil 表示 backend ready 但暂无快照（连接中）。
    /// 歌词滚动、句子高亮等逻辑保持不变，只是数据来源改为订阅。
    private func render(snapshot: Snapshot?) {
        guard let coordinator = coordinator else { return }
        let backendState = coordinator.processManager.state

        var statusText = "IDLE"
        var statusColor = NSColor.systemGray
        var currentIdx = 0
        var chunks: [String] = []
        var mainProgress = ""
        var isPlaying = false
        var isPaused = false
        var isGenerating = false

        if backendState == .ready {
            if let snap = snapshot {
                // ADR-003: status badge/icon come from the single reconciled
                // status (set by the store from this same poll), not re-derived
                // from raw snapshot booleans. isPlaying stays true while paused so
                // the existing badge/time logic below is preserved.
                let status = coordinator.stateStore.playbackStatus
                isPlaying = (status == .playing || status == .paused)
                isPaused = (status == .paused)
                isGenerating = (status == .generating)

                chunks = snap.current_article_chunks ?? []
                currentIdx = snap.current_article_index ?? 0
                mainProgress = snap.main_progress ?? ""

                if isPlaying {
                    if isPaused {
                        statusText = "PAUSED"
                        statusColor = .systemYellow
                    } else {
                        statusText = "PLAYING"
                        statusColor = .systemGreen
                    }
                } else if isGenerating {
                    statusText = "GENERATING"
                    statusColor = .systemBlue
                } else if (snap.active_podcast_processes ?? 0) > 0 {
                    // 朗读空闲，但后台正在生成播客——让用户感知到播客在跑。
                    statusText = "🎙️ 播客生成中"
                    statusColor = .systemPurple
                } else {
                    statusText = "IDLE"
                    statusColor = .systemGray
                }
            } else {
                statusText = "CONNECTING..."
                statusColor = .systemOrange
            }
        } else {
            switch backendState {
            case .stopped, .stopping:
                statusText = "BACKEND OFFLINE"
                statusColor = .systemGray
            case .launching, .waitingForHealth:
                statusText = "BACKEND NOT READY"
                statusColor = .systemOrange
            case .failed:
                statusText = "BACKEND ERROR"
                statusColor = .systemRed
            case .ready:
                statusText = "IDLE"
                statusColor = .systemGray
            }
        }
        
        // Update Status Badge UI
        statusLabel.stringValue = statusText
        statusIndicator.layer?.backgroundColor = statusColor.cgColor
        
        // Update Chunks
        updateArticleChunks(newChunks: chunks)
        
        // Update Highlight index
        if self.currentSentenceIndex != currentIdx || self.transcriptLabels.count != chunks.count {
            self.currentSentenceIndex = currentIdx
            updateTranscriptState(animated: true)
        }
        
        // 三独立键图标固定（▶ / ⏸ / ⏹，在 setup 里设好）：播放键管播放/继续，
        // 暂停键只暂停，停止键只停止。不禁用、不切换图标。
        
        // Update progress label
        if !mainProgress.isEmpty {
            timeLabel.stringValue = "句段 \(mainProgress)"
        } else {
            timeLabel.stringValue = isPlaying ? "准备中..." : "未在播放"
        }
        
        // Update loading spinner
        if isGenerating && chunks.isEmpty {
            loadingIndicator.isHidden = false
            loadingIndicator.startAnimation(nil)
            emptyStateLabel.isHidden = true
            sentencesScrollView.isHidden = true
        } else {
            loadingIndicator.isHidden = true
            loadingIndicator.stopAnimation(nil)
            if chunks.isEmpty {
                emptyStateLabel.isHidden = false
                sentencesScrollView.isHidden = true
            } else {
                emptyStateLabel.isHidden = true
                sentencesScrollView.isHidden = false
            }
        }
    }
    
    @objc private func handleModeChange(_ sender: NSSegmentedControl) {
        // 四个模式（原文/翻译/总结/双人）均已接通后端，选择即记录，
        // 由 selectedModeString() 在朗读时读取，无需在此拦截。
    }

    // MARK: - Bottom Control Bar
    private func setupBottomControlBar(in mainStack: NSStackView) {
        bottomControlBar.translatesAutoresizingMaskIntoConstraints = false
        
        // --- Left: Time & Speed ---
        let leftControls = NSStackView()
        leftControls.translatesAutoresizingMaskIntoConstraints = false
        leftControls.orientation = .horizontal
        leftControls.alignment = .centerY
        leftControls.spacing = 8
        
        timeLabel.font = NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .medium)
        timeLabel.textColor = .secondaryLabelColor
        
        speedPopUp.pullsDown = true
        speedPopUp.isBordered = false
        speedPopUp.imagePosition = .imageOnly
        if let cell = speedPopUp.cell as? NSPopUpButtonCell {
            cell.arrowPosition = .noArrow
        }
        
        let speedConfig = NSImage.SymbolConfiguration(pointSize: 13, weight: .regular)
            .applying(.init(hierarchicalColor: .secondaryLabelColor))
        
        let speedMenu = NSMenu()
        let speedTitleItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
        speedTitleItem.image = NSImage(systemSymbolName: "speedometer", accessibilityDescription: nil)?.withSymbolConfiguration(speedConfig)
        speedMenu.addItem(speedTitleItem)
        
        let speeds = ["0.75x", "1.0x", "1.25x", "1.5x", "2.0x"]
        for speed in speeds {
            let item = NSMenuItem(title: speed, action: #selector(handleSpeedChange(_:)), keyEquivalent: "")
            item.target = self
            if speed == "1.0x" {
                item.state = .on
            }
            speedMenu.addItem(item)
        }
        speedPopUp.menu = speedMenu
        speedPopUp.toolTip = "播放速度"
        
        leftControls.addArrangedSubview(timeLabel)
        leftControls.addArrangedSubview(speedPopUp)
        
        // --- Center: Transport Cluster ---
        let centerControls = NSStackView()
        centerControls.translatesAutoresizingMaskIntoConstraints = false
        centerControls.orientation = .horizontal
        centerControls.spacing = 16
        centerControls.alignment = .centerY
        
        func makeTransportBtn(icon: String, size: CGFloat = 18, tooltip: String) -> NSButton {
            let btn = NSButton()
            let config = NSImage.SymbolConfiguration(pointSize: size, weight: .regular)
            btn.image = NSImage(systemSymbolName: icon, accessibilityDescription: nil)?.withSymbolConfiguration(config)
            btn.isBordered = false
            btn.contentTintColor = .labelColor
            btn.toolTip = tooltip
            return btn
        }
        
        rewind15Btn.image = makeTransportBtn(icon: "gobackward.15", tooltip: "后退 15 秒").image
        rewind15Btn.isBordered = false
        rewind15Btn.toolTip = "后退 15 秒"
        rewind15Btn.target = self
        rewind15Btn.action = #selector(handleRewind15)
        
        prevBtn.image = makeTransportBtn(icon: "backward.end.fill", tooltip: "上一句").image
        prevBtn.isBordered = false
        prevBtn.toolTip = "上一句"
        prevBtn.target = self
        prevBtn.action = #selector(handlePrevSentence)
        
        playBtn.image = makeTransportBtn(icon: "play.fill", size: 36, tooltip: "播放（从头）").image
        playBtn.isBordered = false
        playBtn.contentTintColor = .controlAccentColor
        playBtn.toolTip = "播放（从头）"
        playBtn.target = self
        playBtn.action = #selector(handlePlayBtn)

        pauseBtn.image = makeTransportBtn(icon: "pause.fill", size: 30, tooltip: "暂停 / 继续").image
        pauseBtn.isBordered = false
        pauseBtn.toolTip = "暂停 / 继续"
        pauseBtn.target = self
        pauseBtn.action = #selector(handlePauseBtn)

        stopBtn.image = makeTransportBtn(icon: "stop.fill", tooltip: "停止").image
        stopBtn.isBordered = false
        stopBtn.toolTip = "停止"
        stopBtn.target = self
        stopBtn.action = #selector(handleStopBtn)
        
        nextBtn.image = makeTransportBtn(icon: "forward.end.fill", tooltip: "下一句").image
        nextBtn.isBordered = false
        nextBtn.toolTip = "下一句"
        nextBtn.target = self
        nextBtn.action = #selector(handleNextSentence)
        
        forward15Btn.image = makeTransportBtn(icon: "goforward.15", tooltip: "前进 15 秒").image
        forward15Btn.isBordered = false
        forward15Btn.toolTip = "前进 15 秒"
        forward15Btn.target = self
        forward15Btn.action = #selector(handleForward15)
        
        centerControls.spacing = 20
        centerControls.alignment = .centerY
        
        centerControls.addArrangedSubview(rewind15Btn)
        centerControls.addArrangedSubview(prevBtn)
        centerControls.addArrangedSubview(playBtn)
        centerControls.addArrangedSubview(pauseBtn)
        centerControls.addArrangedSubview(stopBtn)
        centerControls.addArrangedSubview(nextBtn)
        centerControls.addArrangedSubview(forward15Btn)
        
        bottomControlBar.addSubview(leftControls)
        bottomControlBar.addSubview(centerControls)
        
        let flexibleSpacer = NSView()
        flexibleSpacer.setContentHuggingPriority(.defaultLow, for: .vertical)
        mainStack.addArrangedSubview(flexibleSpacer)
        mainStack.addArrangedSubview(bottomControlBar)
        
        NSLayoutConstraint.activate([
            bottomControlBar.widthAnchor.constraint(equalTo: mainStack.widthAnchor),
            bottomControlBar.heightAnchor.constraint(equalToConstant: 50),
            
            leftControls.leadingAnchor.constraint(equalTo: bottomControlBar.leadingAnchor, constant: 12),
            leftControls.centerYAnchor.constraint(equalTo: bottomControlBar.centerYAnchor),
            leftControls.trailingAnchor.constraint(lessThanOrEqualTo: centerControls.leadingAnchor, constant: -24),
            
            playBtn.centerXAnchor.constraint(equalTo: bottomControlBar.centerXAnchor),
            centerControls.centerYAnchor.constraint(equalTo: bottomControlBar.centerYAnchor)
        ])
    }
    
    @objc private func handleSpeedChange(_ sender: NSMenuItem) {
        guard let menu = speedPopUp.menu else { return }
        for item in menu.items {
            item.state = .off
        }
        sender.state = .on
        print("Speed changed to: \(sender.title)")
        
        let title = sender.title.replacingOccurrences(of: "x", with: "")
        if let speedValue = Double(title) {
            Task {
                if let client = coordinator?.processManager.apiClient {
                    let token = client.managementToken
                    _ = await client.updateSettings(settings: ["speed": speedValue], token: token)
                }
            }
        }
    }
}
