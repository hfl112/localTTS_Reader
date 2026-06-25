import AppKit
import Foundation

@MainActor
class SetupWizardViewController: NSViewController {
    var onComplete: (() -> Void)?

    private let titleLabel = NSTextField(labelWithString: "QwenTTS 首次启动设置")
    private let infoLabel = NSTextField(labelWithString: "正在检查运行环境…")

    /// 诊断项列表（每轮检查重建）。
    private let checklistStack = NSStackView()

    // 模型相关操作（仅当模型缺失时显示）
    private let modelActionsStack = NSStackView()
    private let downloadButton = NSButton()
    private let selectModelButton = NSButton()
    private let downloadHelpButton = NSButton()
    private let progressIndicator = NSProgressIndicator()

    private let recheckButton = NSButton()
    private let exportButton = NSButton()
    private let continueButton = NSButton()

    private var lastItems: [EnvironmentDiagnostics.Item] = []

    override func loadView() {
        let effectView = NSVisualEffectView(frame: NSRect(x: 0, y: 0, width: 560, height: 520))
        effectView.material = .popover
        effectView.blendingMode = .behindWindow
        effectView.state = .active
        self.view = effectView
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        setupUI()
        runChecks()
    }

    private func setupUI() {
        titleLabel.font = NSFont.boldSystemFont(ofSize: 20)
        titleLabel.alignment = .center
        infoLabel.textColor = .secondaryLabelColor
        infoLabel.alignment = .center

        checklistStack.orientation = .vertical
        checklistStack.alignment = .leading
        checklistStack.spacing = 10

        progressIndicator.isIndeterminate = false
        progressIndicator.minValue = 0
        progressIndicator.maxValue = 100
        progressIndicator.isHidden = true

        configureButton(downloadButton, "下载 Qwen3-TTS-0.6B（推荐）", #selector(downloadClicked))
        configureButton(selectModelButton, "选择已有模型目录…", #selector(selectModelClicked))
        configureButton(downloadHelpButton, "下载说明", #selector(downloadHelpClicked))
        modelActionsStack.orientation = .horizontal
        modelActionsStack.spacing = 10
        modelActionsStack.addArrangedSubview(downloadButton)
        modelActionsStack.addArrangedSubview(selectModelButton)
        modelActionsStack.addArrangedSubview(downloadHelpButton)
        modelActionsStack.isHidden = true

        configureButton(recheckButton, "重新检查", #selector(recheckClicked))
        configureButton(exportButton, "导出诊断", #selector(exportClicked))
        let bottomStack = NSStackView(views: [recheckButton, exportButton])
        bottomStack.orientation = .horizontal
        bottomStack.spacing = 10

        continueButton.title = "开始使用"
        continueButton.bezelStyle = .rounded
        continueButton.keyEquivalent = "\r"
        continueButton.target = self
        continueButton.action = #selector(continueClicked)
        continueButton.isEnabled = false
        continueButton.setAccessibilityLabel(NSLocalizedString("wizard_continue", comment: "Start Using Application"))
        downloadButton.setAccessibilityLabel(NSLocalizedString("wizard_download", comment: "Download Model"))

        let mainStack = NSStackView(views: [
            titleLabel, infoLabel, checklistStack,
            progressIndicator, modelActionsStack,
            bottomStack, continueButton
        ])
        mainStack.orientation = .vertical
        mainStack.alignment = .centerX
        mainStack.spacing = 18
        mainStack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(mainStack)
        NSLayoutConstraint.activate([
            mainStack.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            mainStack.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            mainStack.widthAnchor.constraint(lessThanOrEqualToConstant: 500),
            progressIndicator.widthAnchor.constraint(equalToConstant: 320)
        ])
    }

    private func configureButton(_ b: NSButton, _ title: String, _ action: Selector) {
        b.title = title
        b.bezelStyle = .rounded
        b.target = self
        b.action = action
    }

    // MARK: - Checks

    private func runChecks() {
        let items = EnvironmentDiagnostics.run()
        lastItems = items
        rebuildChecklist(items)

        let modelFailing = items.contains { $0.name == "模型" && $0.status == .fail }
        modelActionsStack.isHidden = !modelFailing

        let canProceed = EnvironmentDiagnostics.canProceed(items)
        continueButton.isEnabled = canProceed
        infoLabel.stringValue = canProceed
            ? "环境就绪，可以开始使用。"
            : "存在阻塞项（❌），请按提示处理后点「重新检查」。"
    }

    private func rebuildChecklist(_ items: [EnvironmentDiagnostics.Item]) {
        checklistStack.arrangedSubviews.forEach { $0.removeFromSuperview() }
        for item in items {
            let icon: String
            switch item.status {
            case .ok: icon = "✅"
            case .warn: icon = "⚠️"
            case .fail: icon = "❌"
            }
            let head = NSTextField(labelWithString: "\(icon) \(item.name)：\(item.detail)")
            head.lineBreakMode = .byTruncatingMiddle
            head.font = NSFont.systemFont(ofSize: 13)
            let rowViews: [NSView]
            if let hint = item.fixHint, item.status != .ok {
                let hintLabel = NSTextField(wrappingLabelWithString: "↳ \(hint)")
                hintLabel.font = NSFont.systemFont(ofSize: 11)
                hintLabel.textColor = .secondaryLabelColor
                rowViews = [head, hintLabel]
            } else {
                rowViews = [head]
            }
            let row = NSStackView(views: rowViews)
            row.orientation = .vertical
            row.alignment = .leading
            row.spacing = 2
            checklistStack.addArrangedSubview(row)
        }
    }

    // MARK: - Model actions

    @objc private func downloadClicked() {
        downloadButton.isEnabled = false
        selectModelButton.isEnabled = false
        progressIndicator.isHidden = false
        progressIndicator.doubleValue = 0
        let repoID = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16"
        ModelManager.shared.startDownload(name: "Qwen3-TTS-0.6B", repoID: repoID, progress: { [weak self] p in
            self?.progressIndicator.doubleValue = p * 100
            self?.infoLabel.stringValue = String(format: "下载中 %.1f%%…", p * 100)
        }, completion: { [weak self] _ in
            self?.progressIndicator.isHidden = true
            self?.downloadButton.isEnabled = true
            self?.selectModelButton.isEnabled = true
            self?.runChecks()
        })
    }

    /// 选择本机已有模型目录：用户选包含 model.safetensors+config.json 的模型目录，
    /// 在 App Support/QwenTTS/Models 下建同名软链接，使 ModelManager 与后端都能识别。
    @objc private func selectModelClicked() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "选择模型目录"
        panel.message = "请选择包含 model.safetensors 与 config.json 的模型目录（如 Qwen3-TTS-0.6B）"
        guard panel.runModal() == .OK, let src = panel.url else { return }

        let fm = FileManager.default
        let weights = src.appendingPathComponent("model.safetensors")
        let config = src.appendingPathComponent("config.json")
        guard fm.fileExists(atPath: weights.path), fm.fileExists(atPath: config.path) else {
            showAlert(style: .warning, "目录无效",
                      "所选目录缺少 model.safetensors 或 config.json，请选择真正的模型目录。")
            return
        }
        let dest = ModelManager.shared.modelsDirectory.appendingPathComponent(src.lastPathComponent)
        do {
            if fm.fileExists(atPath: dest.path) { try fm.removeItem(at: dest) }
            try fm.createSymbolicLink(at: dest, withDestinationURL: src)
            infoLabel.stringValue = "已链接模型：\(src.lastPathComponent)"
            runChecks()
        } catch {
            showAlert(style: .critical, "链接失败", error.localizedDescription)
        }
    }

    @objc private func downloadHelpClicked() {
        let dir = ModelManager.shared.modelsDirectory.path
        showAlert(style: .informational, "模型下载说明", """
        QwenTTS 需要本地 TTS 模型（约数 GB，未随安装包分发）。三种方式任选其一：

        1. 点「下载 Qwen3-TTS-0.6B」由 App 自动下载（推荐，需联网）。
        2. 若已在别处下载过模型，点「选择已有模型目录」指向它。
        3. 手动放置：把模型目录放到
           \(dir)
           目录下（目录内需含 model.safetensors 与 config.json）。

        放好后点「重新检查」。
        """)
    }

    // MARK: - Bottom actions

    @objc private func recheckClicked() { runChecks() }

    @objc private func exportClicked() {
        let lines = lastItems.map { item -> String in
            let s: String
            switch item.status { case .ok: s = "OK"; case .warn: s = "WARN"; case .fail: s = "FAIL" }
            let hint = item.fixHint.map { "  ↳ \($0)" } ?? ""
            return "[\(s)] \(item.name): \(item.detail)\(hint)"
        }
        let text = "QwenTTS 环境诊断\n" + lines.joined(separator: "\n")
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "qwentts-diagnostics.txt"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        try? text.write(to: url, atomically: true, encoding: .utf8)
    }

    @objc private func continueClicked() {
        // 末页试读在后续步骤接入；当前仅在无阻塞项时允许完成。
        guard EnvironmentDiagnostics.canProceed(lastItems) else { return }
        onComplete?()
    }

    private func showAlert(style: NSAlert.Style, _ title: String, _ message: String) {
        let alert = NSAlert()
        alert.alertStyle = style
        alert.messageText = title
        alert.informativeText = message
        alert.runModal()
    }
}
