import AppKit

@MainActor
class SettingsViewController: NSViewController {
    weak var coordinator: ApplicationCoordinator?
    
    private let voiceLabel = NSTextField(labelWithString: "默认音色:")
    private let voiceButton = NSPopUpButton()
    
    private let profileLabel = NSTextField(labelWithString: "性能模式:")
    private let profileButton = NSPopUpButton()
    
    private let batteryLabel = NSTextField(labelWithString: "电池播客策略:")
    private let batteryButton = NSPopUpButton()
    
    private let tempLabel = NSTextField(labelWithString: "Temperature:")
    private let tempSlider = NSSlider()
    private let tempValueLabel = NSTextField(labelWithString: "0.2")
    
    private let topPLabel = NSTextField(labelWithString: "Top P:")
    private let topPSlider = NSSlider()
    private let topPValueLabel = NSTextField(labelWithString: "0.5")
    
    private let seedLabel = NSTextField(labelWithString: "随机种子 (Seed):")
    private let seedTextField = NSTextField()
    
    private let penaltyLabel = NSTextField(labelWithString: "重复惩罚 (Repetition Penalty):")
    private let penaltySlider = NSSlider()
    private let penaltyValueLabel = NSTextField(labelWithString: "1.1")
    
    // Safety Control
    private let pairingLabel = NSTextField(labelWithString: "扩展配对码:")
    private let pairingTextField = NSTextField()
    private let pairingButton = NSButton()
    
    private let saveButton = NSButton()
    private let refreshButton = NSButton()
    
    // Model Manager Controls
    private let modelHeader = NSTextField(labelWithString: "本地模型管理")
    
    private let model17BLabel = NSTextField(labelWithString: "Qwen3-TTS-1.7B-8bit:")
    private let model17BStatusLabel = NSTextField(labelWithString: "状态: 未知")
    private let model17BProgress = NSProgressIndicator()
    private let model17BButton = NSButton()
    
    private let model06BLabel = NSTextField(labelWithString: "Qwen3-TTS-0.6B:")
    private let model06BStatusLabel = NSTextField(labelWithString: "状态: 未知")
    private let model06BProgress = NSProgressIndicator()
    private let model06BButton = NSButton()
    
    init(coordinator: ApplicationCoordinator?) {
        self.coordinator = coordinator
        super.init(nibName: nil, bundle: nil)
    }
    
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
    
    override func loadView() {
        self.view = NSView(frame: NSRect(x: 0, y: 0, width: 600, height: 530))
    }
    
    override func viewDidLoad() {
        super.viewDidLoad()
        setupUI()
        loadSettings()
        updateModelUI()
    }
    
    private func setupUI() {
        voiceButton.addItems(withTitles: ["Serena", "Ryan", "Vivian"])
        profileButton.addItems(withTitles: ["balanced", "fast", "quiet"])
        batteryButton.addItems(withTitles: ["pause_on_battery", "ignore_battery"])
        
        tempSlider.minValue = 0.0
        tempSlider.maxValue = 1.5
        tempSlider.target = self
        tempSlider.action = #selector(tempChanged)
        
        topPSlider.minValue = 0.0
        topPSlider.maxValue = 1.0
        topPSlider.target = self
        topPSlider.action = #selector(topPChanged)
        
        penaltySlider.minValue = 0.5
        penaltySlider.maxValue = 2.0
        penaltySlider.target = self
        penaltySlider.action = #selector(penaltyChanged)
        
        seedTextField.bezelStyle = .roundedBezel
        pairingTextField.bezelStyle = .roundedBezel
        
        pairingButton.title = "生成配对码"
        pairingButton.bezelStyle = .rounded
        pairingButton.target = self
        pairingButton.action = #selector(generatePairingClicked)
        
        saveButton.title = "保存修改"
        saveButton.bezelStyle = .rounded
        saveButton.target = self
        saveButton.action = #selector(saveClicked)
        
        refreshButton.title = "重新加载"
        refreshButton.bezelStyle = .rounded
        refreshButton.target = self
        refreshButton.action = #selector(refreshClicked)
        
        // Model UI configs
        modelHeader.font = NSFont.boldSystemFont(ofSize: 13)
        
        model17BProgress.isIndeterminate = false
        model17BProgress.minValue = 0
        model17BProgress.maxValue = 100
        model17BProgress.doubleValue = 0
        model17BProgress.isHidden = true
        
        model17BButton.title = "下载"
        model17BButton.bezelStyle = .rounded
        model17BButton.target = self
        model17BButton.action = #selector(click17B)
        
        model06BProgress.isIndeterminate = false
        model06BProgress.minValue = 0
        model06BProgress.maxValue = 100
        model06BProgress.doubleValue = 0
        model06BProgress.isHidden = true
        
        model06BButton.title = "下载"
        model06BButton.bezelStyle = .rounded
        model06BButton.target = self
        model06BButton.action = #selector(click06B)
        
        let formStack = NSStackView()
        formStack.orientation = .vertical
        formStack.alignment = .leading
        formStack.spacing = 12
        formStack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(formStack)
        
        func addRow(label: NSTextField, control: NSView, extra: NSView? = nil) {
            let rowStack = NSStackView()
            rowStack.orientation = .horizontal
            rowStack.alignment = .centerY
            rowStack.spacing = 10
            
            label.translatesAutoresizingMaskIntoConstraints = false
            label.widthAnchor.constraint(equalToConstant: 180).isActive = true
            rowStack.addArrangedSubview(label)
            
            control.translatesAutoresizingMaskIntoConstraints = false
            rowStack.addArrangedSubview(control)
            
            if let extra = extra {
                extra.translatesAutoresizingMaskIntoConstraints = false
                rowStack.addArrangedSubview(extra)
            }
            formStack.addArrangedSubview(rowStack)
        }
        
        addRow(label: voiceLabel, control: voiceButton)
        addRow(label: profileLabel, control: profileButton)
        addRow(label: batteryLabel, control: batteryButton)
        addRow(label: tempLabel, control: tempSlider, extra: tempValueLabel)
        addRow(label: topPLabel, control: topPSlider, extra: topPValueLabel)
        addRow(label: penaltyLabel, control: penaltySlider, extra: penaltyValueLabel)
        
        seedTextField.widthAnchor.constraint(equalToConstant: 120).isActive = true
        addRow(label: seedLabel, control: seedTextField)
        
        pairingTextField.widthAnchor.constraint(equalToConstant: 120).isActive = true
        addRow(label: pairingLabel, control: pairingTextField, extra: pairingButton)
        
        // 分隔行与模型管理器
        let sepLabel = NSTextField(labelWithString: "──────────────────────────────────────────")
        sepLabel.textColor = .tertiaryLabelColor
        formStack.addArrangedSubview(sepLabel)
        formStack.addArrangedSubview(modelHeader)
        
        let row17B = NSStackView(views: [model17BLabel, model17BStatusLabel, model17BProgress, model17BButton])
        row17B.orientation = .horizontal
        row17B.alignment = .centerY
        row17B.spacing = 10
        model17BLabel.widthAnchor.constraint(equalToConstant: 180).isActive = true
        model17BStatusLabel.widthAnchor.constraint(equalToConstant: 120).isActive = true
        model17BProgress.widthAnchor.constraint(equalToConstant: 100).isActive = true
        formStack.addArrangedSubview(row17B)
        
        let row06B = NSStackView(views: [model06BLabel, model06BStatusLabel, model06BProgress, model06BButton])
        row06B.orientation = .horizontal
        row06B.alignment = .centerY
        row06B.spacing = 10
        model06BLabel.widthAnchor.constraint(equalToConstant: 180).isActive = true
        model06BStatusLabel.widthAnchor.constraint(equalToConstant: 120).isActive = true
        model06BProgress.widthAnchor.constraint(equalToConstant: 100).isActive = true
        formStack.addArrangedSubview(row06B)
        
        let buttonStack = NSStackView(views: [saveButton, refreshButton])
        buttonStack.orientation = .horizontal
        buttonStack.spacing = 15
        buttonStack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(buttonStack)
        
        NSLayoutConstraint.activate([
            formStack.topAnchor.constraint(equalTo: view.topAnchor, constant: 20),
            formStack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 30),
            formStack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -30),
            
            buttonStack.topAnchor.constraint(equalTo: formStack.bottomAnchor, constant: 25),
            buttonStack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 30),
            buttonStack.heightAnchor.constraint(equalToConstant: 40)
        ])
    }
    
    @objc private func generatePairingClicked() {
        let chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        let token = String((0..<8).map { _ in chars.randomElement()! })
        pairingTextField.stringValue = token
    }
    
    private func updateModelUI() {
        let status17B = ModelManager.shared.checkModelStatus(name: "Qwen3-TTS-1.7B-8bit")
        switch status17B {
        case .installed:
            model17BStatusLabel.stringValue = "✅ 已安装"
            model17BButton.title = "重新下载"
            model17BProgress.isHidden = true
        case .missing:
            model17BStatusLabel.stringValue = "❌ 未安装"
            model17BButton.title = "开始下载"
            model17BProgress.isHidden = true
        case .paused:
            model17BStatusLabel.stringValue = "⏸️ 已暂停"
            model17BButton.title = "继续下载"
            model17BProgress.isHidden = false
        case .downloading(let progress):
            model17BStatusLabel.stringValue = "⏳ 下载中"
            model17BButton.title = "暂停下载"
            model17BProgress.isHidden = false
            model17BProgress.doubleValue = progress * 100
        }
        
        let status06B = ModelManager.shared.checkModelStatus(name: "Qwen3-TTS-0.6B")
        switch status06B {
        case .installed:
            model06BStatusLabel.stringValue = "✅ 已安装"
            model06BButton.title = "重新下载"
            model06BProgress.isHidden = true
        case .missing:
            model06BStatusLabel.stringValue = "❌ 未安装"
            model06BButton.title = "开始下载"
            model06BProgress.isHidden = true
        case .paused:
            model06BStatusLabel.stringValue = "⏸️ 已暂停"
            model06BButton.title = "继续下载"
            model06BProgress.isHidden = false
        case .downloading(let progress):
            model06BStatusLabel.stringValue = "⏳ 下载中"
            model06BButton.title = "暂停下载"
            model06BProgress.isHidden = false
            model06BProgress.doubleValue = progress * 100
        }
    }
    
    @objc private func click17B() {
        let name = "Qwen3-TTS-1.7B-8bit"
        let status = ModelManager.shared.checkModelStatus(name: name)
        
        if case .downloading = status {
            ModelManager.shared.pauseDownload()
            updateModelUI()
        } else {
            let repoID = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
            ModelManager.shared.startDownload(name: name, repoID: repoID, progress: { [weak self] p in
                self?.model17BProgress.doubleValue = p * 100
                self?.model17BStatusLabel.stringValue = String(format: "⏳ 下载中 %.1f%%", p * 100)
                self?.model17BProgress.isHidden = false
                self?.model17BButton.title = "暂停下载"
            }, completion: { [weak self] success in
                self?.updateModelUI()
                let alert = NSAlert()
                alert.messageText = success ? "安装成功" : "下载或安装失败"
                alert.informativeText = success ? "Qwen3-TTS-1.7B-8bit 模型已下载解压并安装成功！" : "下载失败，请检查网络后重试。"
                alert.alertStyle = .informational
                alert.addButton(withTitle: "确定")
                alert.runModal()
            })
            updateModelUI()
        }
    }
    
    @objc private func click06B() {
        let name = "Qwen3-TTS-0.6B"
        let status = ModelManager.shared.checkModelStatus(name: name)
        
        if case .downloading = status {
            ModelManager.shared.pauseDownload()
            updateModelUI()
        } else {
            let repoID = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16"
            ModelManager.shared.startDownload(name: name, repoID: repoID, progress: { [weak self] p in
                self?.model06BProgress.doubleValue = p * 100
                self?.model06BStatusLabel.stringValue = String(format: "⏳ 下载中 %.1f%%", p * 100)
                self?.model06BProgress.isHidden = false
                self?.model06BButton.title = "暂停下载"
            }, completion: { [weak self] success in
                self?.updateModelUI()
                let alert = NSAlert()
                alert.messageText = success ? "安装成功" : "下载或安装失败"
                alert.informativeText = success ? "Qwen3-TTS-0.6B 模型已下载解压并安装成功！" : "下载失败，请检查网络后重试。"
                alert.alertStyle = .informational
                alert.addButton(withTitle: "确定")
                alert.runModal()
            })
            updateModelUI()
        }
    }
    
    @objc private func tempChanged() {
        tempValueLabel.stringValue = String(format: "%.2f", tempSlider.doubleValue)
    }
    
    @objc private func topPChanged() {
        topPValueLabel.stringValue = String(format: "%.2f", topPSlider.doubleValue)
    }
    
    @objc private func penaltyChanged() {
        penaltyValueLabel.stringValue = String(format: "%.2f", penaltySlider.doubleValue)
    }
    
    @objc private func refreshClicked() {
        loadSettings()
        updateModelUI()
    }
    
    private func loadSettings() {
        guard let client = coordinator?.processManager.apiClient else { return }
        Task {
            if let config = await client.fetchSettings() {
                if let voice = config.voice {
                    voiceButton.selectItem(withTitle: voice)
                }
                if let profile = config.performance_profile {
                    profileButton.selectItem(withTitle: profile)
                }
                if let battery = config.battery_podcast_policy {
                    batteryButton.selectItem(withTitle: battery)
                }
                if let temp = config.temperature {
                    tempSlider.doubleValue = temp
                    tempChanged()
                }
                if let topP = config.top_p {
                    topPSlider.doubleValue = topP
                    topPChanged()
                }
                if let rep = config.repetition_penalty {
                    penaltySlider.doubleValue = rep
                    penaltyChanged()
                }
                if let seed = config.seed {
                    seedTextField.stringValue = String(seed)
                }
                if let pairing = config.extension_pairing_token {
                    pairingTextField.stringValue = pairing
                }
            }
        }
    }
    
    @objc private func saveClicked() {
        guard let client = coordinator?.processManager.apiClient,
              let coordinator = coordinator else { return }
        
        let voice = voiceButton.titleOfSelectedItem
        let profile = profileButton.titleOfSelectedItem
        let battery = batteryButton.titleOfSelectedItem
        let temp = tempSlider.doubleValue
        let topP = topPSlider.doubleValue
        let rep = penaltySlider.doubleValue
        let seed = Int(seedTextField.stringValue) ?? 42
        let pairing = pairingTextField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        
        let settings: [String: Any] = [
            "voice": voice ?? "Serena",
            "performance_profile": profile ?? "balanced",
            "battery_podcast_policy": battery ?? "pause_on_battery",
            "temperature": temp,
            "top_p": topP,
            "repetition_penalty": rep,
            "seed": seed,
            "extension_pairing_token": pairing
        ]
        
        Task {
            let token = coordinator.processManager.apiClient?.managementToken ?? ""
            let success = await client.updateSettings(settings: settings, token: token)
            if success {
                let alert = NSAlert()
                alert.messageText = "保存成功"
                alert.informativeText = "系统设置已更新成功并同步到后端。"
                alert.alertStyle = .informational
                alert.addButton(withTitle: "确定")
                alert.runModal()
            }
        }
    }
}
