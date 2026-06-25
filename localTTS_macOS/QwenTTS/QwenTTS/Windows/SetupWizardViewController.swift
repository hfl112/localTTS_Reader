import AppKit
import Foundation

@MainActor
class SetupWizardViewController: NSViewController {
    var onComplete: (() -> Void)?
    
    private let titleLabel = NSTextField(labelWithString: "QwenTTS 首次启动设置")
    private let infoLabel = NSTextField(labelWithString: "正在检查运行环境...")
    
    private let osCheckLabel = NSTextField(labelWithString: "macOS 兼容性: 检查中...")
    private let archCheckLabel = NSTextField(labelWithString: "Apple Silicon: 检查中...")
    private let diskCheckLabel = NSTextField(labelWithString: "磁盘空间: 检查中...")
    
    private let modelStatusLabel = NSTextField(labelWithString: "模型状态: 检查中...")
    private let downloadButton = NSButton()
    private let progressIndicator = NSProgressIndicator()
    
    private let continueButton = NSButton()
    
    override func loadView() {
        let effectView = NSVisualEffectView(frame: NSRect(x: 0, y: 0, width: 500, height: 400))
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
        
        let checklistStack = NSStackView(views: [
            osCheckLabel, archCheckLabel, diskCheckLabel, modelStatusLabel
        ])
        checklistStack.orientation = .vertical
        checklistStack.alignment = .leading
        checklistStack.spacing = 8
        
        let mainStack = NSStackView(views: [
            titleLabel, infoLabel,
            checklistStack,
            progressIndicator, downloadButton,
            continueButton
        ])
        mainStack.orientation = .vertical
        mainStack.alignment = .centerX
        mainStack.spacing = 20
        mainStack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(mainStack)
        
        NSLayoutConstraint.activate([
            mainStack.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            mainStack.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            progressIndicator.widthAnchor.constraint(equalToConstant: 300)
        ])
        
        progressIndicator.isIndeterminate = false
        progressIndicator.minValue = 0
        progressIndicator.maxValue = 100
        progressIndicator.isHidden = true
        
        downloadButton.title = "下载 Qwen3-TTS-0.6B 模型 (推荐)"
        downloadButton.bezelStyle = .rounded
        downloadButton.target = self
        downloadButton.action = #selector(downloadClicked)
        downloadButton.isHidden = true
        
        continueButton.title = "开始使用"
        continueButton.bezelStyle = .rounded
        continueButton.target = self
        continueButton.action = #selector(continueClicked)
        continueButton.isEnabled = false
        
        // Accessibility
        continueButton.setAccessibilityLabel(NSLocalizedString("wizard_continue", comment: "Start Using Application"))
        downloadButton.setAccessibilityLabel(NSLocalizedString("wizard_download", comment: "Download Model"))
    }
    
    private func runChecks() {
        // 1. OS Check
        let osVersion = ProcessInfo.processInfo.operatingSystemVersion
        if osVersion.majorVersion >= 13 {
            osCheckLabel.stringValue = "macOS 兼容性: ✅ 通过 (macOS \(osVersion.majorVersion).\(osVersion.minorVersion))"
        } else {
            osCheckLabel.stringValue = "macOS 兼容性: ❌ 需要 macOS 13+"
        }
        
        // 2. Arch Check
        var size: Int = 0
        sysctlbyname("hw.optional.arm64", nil, &size, nil, 0)
        var isARM64: Int32 = 0
        sysctlbyname("hw.optional.arm64", &isARM64, &size, nil, 0)
        if isARM64 == 1 {
            archCheckLabel.stringValue = "Apple Silicon: ✅ 通过"
        } else {
            archCheckLabel.stringValue = "Apple Silicon: ⚠️ 建议使用 M 系列芯片获得最佳性能"
        }
        
        // 3. Disk Check
        do {
            let appSupportUrl = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
            let values = try appSupportUrl.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
            if let capacity = values.volumeAvailableCapacityForImportantUsage {
                let gb = Double(capacity) / 1_000_000_000
                if gb > 10 {
                    diskCheckLabel.stringValue = String(format: "磁盘空间: ✅ 通过 (%.1f GB 可用)", gb)
                } else {
                    diskCheckLabel.stringValue = String(format: "磁盘空间: ⚠️ 剩余空间可能不足 (%.1f GB 可用)", gb)
                }
            }
        } catch {
            diskCheckLabel.stringValue = "磁盘空间: ❓ 无法检测"
        }
        
        checkModelStatus()
    }
    
    private func checkModelStatus() {
        let status = ModelManager.shared.checkModelStatus(name: "Qwen3-TTS-0.6B")
        switch status {
        case .installed:
            modelStatusLabel.stringValue = "模型状态: ✅ Qwen3-TTS-0.6B 已安装"
            downloadButton.isHidden = true
            progressIndicator.isHidden = true
            continueButton.isEnabled = true
        default:
            modelStatusLabel.stringValue = "模型状态: ❌ 核心模型未安装"
            downloadButton.isHidden = false
            continueButton.isEnabled = false
        }
    }
    
    @objc private func downloadClicked() {
        downloadButton.isEnabled = false
        progressIndicator.isHidden = false
        progressIndicator.doubleValue = 0
        
        let repoID = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16"
        ModelManager.shared.startDownload(name: "Qwen3-TTS-0.6B", repoID: repoID, progress: { [weak self] p in
            self?.progressIndicator.doubleValue = p * 100
            self?.modelStatusLabel.stringValue = String(format: "模型状态: ⏳ 下载中 %.1f%%", p * 100)
        }, completion: { [weak self] success in
            self?.checkModelStatus()
        })
    }
    
    @objc private func continueClicked() {
        onComplete?()
    }
}
