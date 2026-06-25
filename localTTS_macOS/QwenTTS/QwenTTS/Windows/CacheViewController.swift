import AppKit

@MainActor
class CacheViewController: NSViewController, NSTableViewDataSource, NSTableViewDelegate {
    weak var coordinator: ApplicationCoordinator?
    
    private let scrollView = NSScrollView()
    private let tableView = NSTableView()
    private var cacheItems: [[String: Any]] = []
    
    private let playButton = NSButton()
    private let exportButton = NSButton()
    private let deleteButton = NSButton()
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
        tableView.headerView = NSTableHeaderView()
        
        let colPreview = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("TextPreview"))
        colPreview.title = "文本预览"
        colPreview.width = 300
        tableView.addTableColumn(colPreview)
        
        let colMd5 = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("MD5"))
        colMd5.title = "缓存 MD5"
        colMd5.width = 150
        tableView.addTableColumn(colMd5)
        
        scrollView.documentView = tableView
        scrollView.hasVerticalScroller = true
        scrollView.borderType = .noBorder
        
        playButton.title = "播放"
        playButton.bezelStyle = .rounded
        playButton.target = self
        playButton.action = #selector(playSelected)
        
        exportButton.title = "导出 WAV"
        exportButton.bezelStyle = .rounded
        exportButton.target = self
        exportButton.action = #selector(exportSelected)
        
        deleteButton.title = "删除"
        deleteButton.bezelStyle = .rounded
        deleteButton.target = self
        deleteButton.action = #selector(deleteSelected)
        
        clearButton.title = "清空缓存"
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
        emptyLabel.stringValue = "暂无临时缓存内容"
        emptyLabel.isHidden = true
        
        scrollView.translatesAutoresizingMaskIntoConstraints = false
        emptyLabel.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(scrollView)
        view.addSubview(emptyLabel)
        
        let buttonStack = NSStackView(views: [playButton, exportButton, deleteButton, clearButton, refreshButton])
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
            if let fetched = await client.fetchCacheItems() {
                self.cacheItems = fetched
                self.tableView.reloadData()
                self.emptyLabel.isHidden = !self.cacheItems.isEmpty
            }
        }
    }
    
    @objc private func playSelected() {
        let selectedRow = tableView.selectedRow
        guard selectedRow >= 0, selectedRow < cacheItems.count, let client = coordinator?.processManager.apiClient else { return }
        let item = cacheItems[selectedRow]
        guard let md5 = item["md5"] as? String else { return }
        Task {
            _ = await client.playCache(md5: md5)
        }
    }
    
    @objc private func exportSelected() {
        let selectedRow = tableView.selectedRow
        guard selectedRow >= 0, selectedRow < cacheItems.count, let client = coordinator?.processManager.apiClient else { return }
        let item = cacheItems[selectedRow]
        guard let md5 = item["md5"] as? String else { return }
        Task {
            let success = await client.exportCache(md5: md5)
            if success {
                let alert = NSAlert()
                alert.messageText = "导出成功"
                alert.informativeText = "缓存已成功导出为播客 WAV 文件，请至播客管理界面查看。"
                alert.alertStyle = .informational
                alert.addButton(withTitle: "确定")
                alert.runModal()
            }
        }
    }
    
    @objc private func deleteSelected() {
        let selectedRow = tableView.selectedRow
        guard selectedRow >= 0, selectedRow < cacheItems.count, let client = coordinator?.processManager.apiClient else { return }
        let item = cacheItems[selectedRow]
        guard let md5 = item["md5"] as? String else { return }
        Task {
            _ = await client.deleteCache(md5: md5)
            refreshData()
        }
    }
    
    @objc private func clearAll() {
        guard let client = coordinator?.processManager.apiClient else { return }
        Task {
            _ = await client.clearCache()
            refreshData()
        }
    }
    
    // MARK: - NSTableViewDataSource & NSTableViewDelegate
    
    func numberOfRows(in tableView: NSTableView) -> Int {
        return cacheItems.count
    }
    
    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        guard row < cacheItems.count else { return nil }
        let item = cacheItems[row]
        let cellID = NSUserInterfaceItemIdentifier("CacheItemCell")
        var cell = tableView.makeView(withIdentifier: cellID, owner: self) as? NSTableCellView
        if cell == nil {
            cell = NSTableCellView()
            cell?.identifier = cellID
            let tf = NSTextField()
            tf.isEditable = false
            tf.isSelectable = false
            tf.isBordered = false
            tf.drawsBackground = false
            tf.translatesAutoresizingMaskIntoConstraints = false
            cell?.addSubview(tf)
            cell?.textField = tf
            NSLayoutConstraint.activate([
                tf.leadingAnchor.constraint(equalTo: cell!.leadingAnchor, constant: 5),
                tf.trailingAnchor.constraint(equalTo: cell!.trailingAnchor, constant: -5),
                tf.centerYAnchor.constraint(equalTo: cell!.centerYAnchor)
            ])
        }
        
        var display = ""
        if let colID = tableColumn?.identifier.rawValue {
            switch colID {
            case "TextPreview":
                display = item["text"] as? String ?? ""
            case "MD5":
                display = item["md5"] as? String ?? ""
            default:
                break
            }
        }
        cell?.textField?.stringValue = display
        return cell
    }
}
