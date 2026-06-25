import AppKit

@MainActor
class SavedItemsViewController: NSViewController, NSTableViewDataSource, NSTableViewDelegate {
    weak var coordinator: ApplicationCoordinator?
    
    private let scrollView = NSScrollView()
    private let tableView = NSTableView()
    private var items: [[String: Any]] = []
    
    private let playButton = NSButton()
    private let deleteButton = NSButton()
    private let makePodcastButton = NSButton()
    private let makeSinglePodcastButton = NSButton()
    private let clearButton = NSButton()
    private let refreshButton = NSButton()
    private let emptyLabel = NSTextField()
    
    init(coordinator: ApplicationCoordinator?) {
        self.coordinator = coordinator
        super.init(nibName: nil, bundle: nil)
    }
    
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
    
    override func loadView() {
        self.view = NSView(frame: NSRect(x: 0, y: 0, width: 600, height: 400))
    }
    
    override func viewDidLoad() {
        super.viewDidLoad()
        setupUI()
        refreshData()
    }
    
    private func setupUI() {
        tableView.dataSource = self
        tableView.delegate = self
        tableView.usesAlternatingRowBackgroundColors = true
        tableView.allowsMultipleSelection = true
        tableView.headerView = NSTableHeaderView()
        
        let colTitle = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("Title"))
        colTitle.title = "标题"
        colTitle.width = 300
        tableView.addTableColumn(colTitle)
        
        let colSource = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("Source"))
        colSource.title = "来源"
        colSource.width = 80
        tableView.addTableColumn(colSource)
        
        let colExported = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("Exported"))
        colExported.title = "状态"
        colExported.width = 100
        tableView.addTableColumn(colExported)
        
        scrollView.documentView = tableView
        scrollView.hasVerticalScroller = true
        scrollView.hasHorizontalScroller = false
        scrollView.autohidesScrollers = true
        scrollView.borderType = .noBorder
        
        playButton.title = "播放选中"
        playButton.bezelStyle = .rounded
        playButton.target = self
        playButton.action = #selector(playSelected)
        
        deleteButton.title = "删除"
        deleteButton.bezelStyle = .rounded
        deleteButton.target = self
        deleteButton.action = #selector(deleteSelected)
        
        makePodcastButton.title = "生成合集播客"
        makePodcastButton.bezelStyle = .rounded
        makePodcastButton.target = self
        makePodcastButton.action = #selector(generateMegaPodcast)
        
        makeSinglePodcastButton.title = "生成单篇播客"
        makeSinglePodcastButton.bezelStyle = .rounded
        makeSinglePodcastButton.target = self
        makeSinglePodcastButton.action = #selector(generateSinglePodcast)
        
        clearButton.title = "清空列表"
        clearButton.bezelStyle = .rounded
        clearButton.target = self
        clearButton.action = #selector(clearAll)
        
        refreshButton.title = ""
        refreshButton.bezelStyle = .texturedRounded
        refreshButton.image = NSImage(systemSymbolName: "arrow.clockwise", accessibilityDescription: "Refresh")
        refreshButton.target = self
        refreshButton.action = #selector(refreshClicked)
        
        emptyLabel.isEditable = false
        emptyLabel.isSelectable = false
        emptyLabel.isBordered = false
        emptyLabel.drawsBackground = false
        emptyLabel.textColor = .secondaryLabelColor
        emptyLabel.alignment = .center
        emptyLabel.font = NSFont.systemFont(ofSize: 14)
        emptyLabel.stringValue = "暂无稍后阅读内容"
        emptyLabel.isHidden = true
        
        scrollView.translatesAutoresizingMaskIntoConstraints = false
        emptyLabel.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(scrollView)
        view.addSubview(emptyLabel)
        
        let buttonStack = NSStackView(views: [playButton, makeSinglePodcastButton, makePodcastButton, deleteButton, clearButton, refreshButton])
        buttonStack.orientation = .horizontal
        buttonStack.spacing = 10
        buttonStack.edgeInsets = NSEdgeInsets(top: 10, left: 15, bottom: 10, right: 15)
        buttonStack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(buttonStack)
        
        NSLayoutConstraint.activate([
            scrollView.topAnchor.constraint(equalTo: view.topAnchor),
            scrollView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            scrollView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            scrollView.bottomAnchor.constraint(equalTo: buttonStack.topAnchor),
            
            emptyLabel.centerXAnchor.constraint(equalTo: scrollView.centerXAnchor),
            emptyLabel.centerYAnchor.constraint(equalTo: scrollView.centerYAnchor),
            
            buttonStack.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            buttonStack.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            buttonStack.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            buttonStack.heightAnchor.constraint(equalToConstant: 45)
        ])
    }
    
    @objc private func refreshClicked() {
        refreshData()
    }
    
    private func refreshData() {
        guard let client = coordinator?.processManager.apiClient else { return }
        Task {
            if let fetched = await client.fetchSavedItems() {
                self.items = fetched
                self.tableView.reloadData()
                self.emptyLabel.isHidden = !self.items.isEmpty
            }
        }
    }
    
    @objc private func playSelected() {
        let indices = tableView.selectedRowIndexes.map { $0 }
        guard !indices.isEmpty, let client = coordinator?.processManager.apiClient else { return }
        Task {
            _ = await client.playSaved(indices: indices)
        }
    }
    
    @objc private func deleteSelected() {
        let indices = tableView.selectedRowIndexes.map { $0 }
        guard !indices.isEmpty, let client = coordinator?.processManager.apiClient else { return }
        Task {
            for index in indices.sorted(by: >) {
                if index < items.count {
                    let item = items[index]
                    let md5 = item["md5"] as? String
                    _ = await client.deleteSaved(md5: md5, index: index)
                }
            }
            refreshData()
        }
    }
    
    @objc private func generateMegaPodcast() {
        guard let client = coordinator?.processManager.apiClient else { return }
        Task {
            let success = await client.generatePodcast()
            if success {
                showAlert(title: "任务已提交", message: "合集播客已在后台开始生成，请前往'播客管理'查看。")
            }
        }
    }
    
    @objc private func generateSinglePodcast() {
        let selectedRow = tableView.selectedRow
        guard selectedRow >= 0, selectedRow < items.count else { return }
        guard let client = coordinator?.processManager.apiClient else { return }
        let item = items[selectedRow]
        let text = item["text"] as? String ?? ""
        let title = item["title"] as? String
        let source = item["source"] as? String ?? "web"
        let voice = item["voice"] as? String
        
        Task {
            let success = await client.generateSinglePodcast(text: text, source: source, voice: voice, title: title)
            if success {
                showAlert(title: "任务已提交", message: "单篇播客已在后台开始生成。")
            }
        }
    }
    
    @objc private func clearAll() {
        guard let client = coordinator?.processManager.apiClient else { return }
        Task {
            _ = await client.clearSavedItems()
            refreshData()
        }
    }
    
    private func showAlert(title: String, message: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.alertStyle = .informational
        alert.addButton(withTitle: "确定")
        alert.runModal()
    }
    
    // MARK: - NSTableViewDataSource & NSTableViewDelegate
    
    func numberOfSections(in tableView: NSTableView) -> Int {
        return 1
    }
    
    func numberOfRows(in tableView: NSTableView) -> Int {
        return items.count
    }
    
    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        guard row < items.count else { return nil }
        let item = items[row]
        let cellID = NSUserInterfaceItemIdentifier("SavedItemCell")
        
        var cell = tableView.makeView(withIdentifier: cellID, owner: self) as? NSTableCellView
        if cell == nil {
            cell = NSTableCellView()
            cell?.identifier = cellID
            
            let textField = NSTextField()
            textField.isEditable = false
            textField.isSelectable = false
            textField.isBordered = false
            textField.drawsBackground = false
            textField.translatesAutoresizingMaskIntoConstraints = false
            cell?.addSubview(textField)
            cell?.textField = textField
            
            NSLayoutConstraint.activate([
                textField.leadingAnchor.constraint(equalTo: cell!.leadingAnchor, constant: 5),
                textField.trailingAnchor.constraint(equalTo: cell!.trailingAnchor, constant: -5),
                textField.centerYAnchor.constraint(equalTo: cell!.centerYAnchor)
            ])
        }
        
        var displayString = ""
        if let colID = tableColumn?.identifier.rawValue {
            switch colID {
            case "Title":
                displayString = item["title"] as? String ?? item["text"] as? String ?? ""
            case "Source":
                displayString = item["source"] as? String ?? ""
            case "Exported":
                let isPending = item["is_pending"] as? Bool ?? false
                let isExported = item["is_exported"] as? Bool ?? false
                if isPending {
                    displayString = "⏳ 解析中"
                } else {
                    displayString = isExported ? "✅ 已生成播客" : "📁 未导出"
                }
            default:
                break
            }
        }
        
        cell?.textField?.stringValue = displayString
        return cell
    }
}
