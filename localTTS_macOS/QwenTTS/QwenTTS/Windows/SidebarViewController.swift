import AppKit

@MainActor
class SidebarViewController: NSViewController {
    var onSelectTab: ((Int) -> Void)?
    
    private let stackView = NSStackView()
    private var buttons: [NSButton] = []
    
    override func loadView() {
        self.view = NSView(frame: NSRect(x: 0, y: 0, width: 200, height: 500))
        
        // 确保侧边栏支持高斯模糊（虽然 NSSplitViewItem.sidebar 会自动处理大部分）
        let visualEffect = NSVisualEffectView(frame: view.bounds)
        visualEffect.material = .sidebar
        visualEffect.blendingMode = .behindWindow
        visualEffect.state = .active
        visualEffect.autoresizingMask = [.width, .height]
        view.addSubview(visualEffect)
        
        stackView.orientation = .vertical
        stackView.alignment = .leading
        stackView.spacing = 10
        stackView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stackView)
        
        NSLayoutConstraint.activate([
            stackView.topAnchor.constraint(equalTo: view.topAnchor, constant: 40),
            stackView.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 15),
            stackView.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -15)
        ])
        
        // Logo Title
        let titleLabel = NSTextField(labelWithString: "QwenTTS")
        titleLabel.font = NSFont.systemFont(ofSize: 22, weight: .bold)
        titleLabel.textColor = .labelColor
        let titleContainer = NSView()
        titleContainer.translatesAutoresizingMaskIntoConstraints = false
        titleLabel.translatesAutoresizingMaskIntoConstraints = false
        titleContainer.addSubview(titleLabel)
        titleContainer.heightAnchor.constraint(equalToConstant: 40).isActive = true
        NSLayoutConstraint.activate([
            titleLabel.centerYAnchor.constraint(equalTo: titleContainer.centerYAnchor),
            titleLabel.leadingAnchor.constraint(equalTo: titleContainer.leadingAnchor, constant: 8)
        ])
        stackView.addArrangedSubview(titleContainer)
        
        // Separator
        let separator = NSBox()
        separator.boxType = .separator
        separator.translatesAutoresizingMaskIntoConstraints = false
        stackView.addArrangedSubview(separator)
        separator.widthAnchor.constraint(equalTo: stackView.widthAnchor).isActive = true
        
        // Tabs
        let tabs = [
            ("朗读中控", "play.circle.fill"),
            ("内容中心", "square.stack.3d.up.fill"),
            ("设置", "gearshape.fill"),
            ("AI 引擎", "brain.head.profile")
        ]
        
        for (index, tab) in tabs.enumerated() {
            let btn = createSidebarButton(title: tab.0, icon: tab.1, tag: index)
            stackView.addArrangedSubview(btn)
            btn.widthAnchor.constraint(equalTo: stackView.widthAnchor).isActive = true
            buttons.append(btn)
        }
        
        updateSelection(selectedIndex: 0)
    }
    
    private func createSidebarButton(title: String, icon: String, tag: Int) -> NSButton {
        let btn = NSButton()
        btn.title = title
        btn.image = NSImage(systemSymbolName: icon, accessibilityDescription: nil)
        btn.imagePosition = .imageLeft
        btn.alignment = .left
        btn.isBordered = false
        btn.tag = tag
        btn.font = NSFont.systemFont(ofSize: 14, weight: .medium)
        btn.target = self
        btn.action = #selector(tabClicked(_:))
        btn.contentTintColor = .labelColor
        
        // Padding/styling for modern sidebar feel
        btn.heightAnchor.constraint(equalToConstant: 32).isActive = true
        
        // Wrap in a custom view or use layer? Using core button styles is enough for now,
        // we can adjust selection background via layer if needed.
        btn.wantsLayer = true
        btn.layer?.cornerRadius = 8
        
        return btn
    }
    
    @objc private func tabClicked(_ sender: NSButton) {
        updateSelection(selectedIndex: sender.tag)
        onSelectTab?(sender.tag)
    }
    
    private func updateSelection(selectedIndex: Int) {
        for btn in buttons {
            if btn.tag == selectedIndex {
                btn.layer?.backgroundColor = NSColor.controlAccentColor.withAlphaComponent(0.12).cgColor
                btn.layer?.cornerRadius = 10
                btn.contentTintColor = .controlAccentColor
            } else {
                btn.layer?.backgroundColor = NSColor.clear.cgColor
                btn.contentTintColor = .labelColor
            }
        }
    }
}
