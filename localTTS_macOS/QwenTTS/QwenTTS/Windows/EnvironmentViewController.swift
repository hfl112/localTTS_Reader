import AppKit
import Foundation

@MainActor
class EnvironmentViewController: NSViewController {
    weak var coordinator: ApplicationCoordinator?
    
    private let modeSegment = NSSegmentedControl(labels: ["内置环境 (默认)", "开发者自定义环境"], trackingMode: .selectOne, target: nil, action: nil)
    
    private let pyPathField = NSTextField()
    private let backendPathField = NSTextField()
    private let mlxPathField = NSTextField()
    private let modelsPathField = NSTextField()
    private let refPathField = NSTextField()
    private let ffmpegPathField = NSTextField()
    
    private let saveButton = NSButton()
    private let restartLabel = NSTextField(labelWithString: "注意：修改环境后必须重启 App 才会生效。")
    
    private var customFields: [NSTextField] = []
    
    init(coordinator: ApplicationCoordinator?) {
        self.coordinator = coordinator
        super.init(nibName: nil, bundle: nil)
    }
    
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
    
    override func loadView() {
        self.view = NSView(frame: NSRect(x: 0, y: 0, width: 600, height: 500))
    }
    
    override func viewDidLoad() {
        super.viewDidLoad()
        setupUI()
        loadConfig()
    }
    
    private func setupUI() {
        let titleLabel = NSTextField(labelWithString: "运行环境配置")
        titleLabel.font = NSFont.boldSystemFont(ofSize: 16)
        
        modeSegment.target = self
        modeSegment.action = #selector(modeChanged)
        
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 15
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        
        stack.addArrangedSubview(titleLabel)
        stack.addArrangedSubview(modeSegment)
        
        func addRow(label: String, field: NSTextField) {
            let row = NSStackView()
            row.orientation = .horizontal
            let lbl = NSTextField(labelWithString: label)
            lbl.widthAnchor.constraint(equalToConstant: 120).isActive = true
            field.widthAnchor.constraint(equalToConstant: 300).isActive = true
            row.addArrangedSubview(lbl)
            row.addArrangedSubview(field)
            stack.addArrangedSubview(row)
            customFields.append(field)
        }
        
        addRow(label: "Python Executable:", field: pyPathField)
        addRow(label: "Backend Script:", field: backendPathField)
        addRow(label: "MLX Audio Dir:", field: mlxPathField)
        addRow(label: "Models Dir:", field: modelsPathField)
        addRow(label: "Reference Audio Dir:", field: refPathField)
        addRow(label: "FFmpeg Path:", field: ffmpegPathField)
        
        saveButton.title = "保存环境设置"
        saveButton.bezelStyle = .rounded
        saveButton.target = self
        saveButton.action = #selector(saveClicked)
        
        restartLabel.textColor = .systemRed
        restartLabel.font = NSFont.systemFont(ofSize: 11)
        
        let btnStack = NSStackView(views: [saveButton, restartLabel])
        btnStack.orientation = .horizontal
        btnStack.spacing = 10
        stack.addArrangedSubview(btnStack)
        
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: view.topAnchor, constant: 20),
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 30),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -30)
        ])
    }
    
    private func loadConfig() {
        let mode = EnvironmentConfigManager.shared.mode
        modeSegment.selectedSegment = (mode == .builtin) ? 0 : 1
        
        let config = EnvironmentConfigManager.shared.customConfig
        pyPathField.stringValue = config.pythonPath
        backendPathField.stringValue = config.backendPath
        mlxPathField.stringValue = config.mlxAudioPath
        modelsPathField.stringValue = config.modelsPath
        refPathField.stringValue = config.referenceAudioPath
        ffmpegPathField.stringValue = config.ffmpegPath
        
        modeChanged()
    }
    
    @objc private func modeChanged() {
        let isCustom = modeSegment.selectedSegment == 1
        for field in customFields {
            field.isEnabled = isCustom
        }
    }
    
    @objc private func saveClicked() {
        let isCustom = modeSegment.selectedSegment == 1
        EnvironmentConfigManager.shared.mode = isCustom ? .custom : .builtin
        
        var config = EnvironmentConfigManager.shared.customConfig
        config.pythonPath = pyPathField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        config.backendPath = backendPathField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        config.mlxAudioPath = mlxPathField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        config.modelsPath = modelsPathField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        config.referenceAudioPath = refPathField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        config.ffmpegPath = ffmpegPathField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        
        EnvironmentConfigManager.shared.customConfig = config
        
        let alert = NSAlert()
        alert.messageText = "保存成功"
        alert.informativeText = "环境配置已保存。请退出并重新打开应用以使新环境生效！"
        alert.alertStyle = .informational
        alert.addButton(withTitle: "确定")
        alert.runModal()
    }
}
