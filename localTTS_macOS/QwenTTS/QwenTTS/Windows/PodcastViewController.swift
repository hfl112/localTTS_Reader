import AppKit

@MainActor
class PodcastViewController: NSViewController, NSTableViewDataSource, NSTableViewDelegate {
    weak var coordinator: ApplicationCoordinator?
    
    private let jobsLabel = NSTextField(labelWithString: "后台生成任务")
    private let jobsScrollView = NSScrollView()
    private let jobsTableView = NSTableView()
    private var jobs: [[String: Any]] = []
    
    private let filesLabel = NSTextField(labelWithString: "已生成播客列表")
    private let filesScrollView = NSScrollView()
    private let filesTableView = NSTableView()
    private var files: [[String: Any]] = []
    
    private let playButton = NSButton()
    private let deleteButton = NSButton()
    private let pinButton = NSButton()
    private let clearButton = NSButton()
    private let refreshButton = NSButton()
    
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
        jobsTableView.dataSource = self
        jobsTableView.delegate = self
        jobsTableView.usesAlternatingRowBackgroundColors = true
        jobsTableView.headerView = NSTableHeaderView()

        let colJobName = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("JobName"))
        colJobName.title = "任务描述"
        colJobName.width = 300
        jobsTableView.addTableColumn(colJobName)

        let colJobStatus = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("JobStatus"))
        colJobStatus.title = "状态"
        colJobStatus.width = 150
        jobsTableView.addTableColumn(colJobStatus)

        jobsScrollView.documentView = jobsTableView
        jobsScrollView.hasVerticalScroller = true
        jobsScrollView.borderType = .noBorder

        filesTableView.dataSource = self
        filesTableView.delegate = self
        filesTableView.usesAlternatingRowBackgroundColors = true
        filesTableView.doubleAction = #selector(filesTableViewDoubleClicked)
        filesTableView.target = self
        filesTableView.headerView = NSTableHeaderView()
        
        let colFileName = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("FileName"))
        colFileName.title = "播客文件名"
        colFileName.width = 350
        filesTableView.addTableColumn(colFileName)
        
        let colFilePinned = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("FilePinned"))
        colFilePinned.title = "置顶"
        colFilePinned.width = 80
        filesTableView.addTableColumn(colFilePinned)
        
        filesScrollView.documentView = filesTableView
        filesScrollView.hasVerticalScroller = true
        filesScrollView.borderType = .noBorder
        
        jobsLabel.font = NSFont.boldSystemFont(ofSize: 12)
        filesLabel.font = NSFont.boldSystemFont(ofSize: 12)
        
        playButton.title = "播放"
        playButton.bezelStyle = .rounded
        playButton.target = self
        playButton.action = #selector(playSelected)
        
        deleteButton.title = "删除"
        deleteButton.bezelStyle = .rounded
        deleteButton.target = self
        deleteButton.action = #selector(deleteSelected)
        
        pinButton.title = "置顶"
        pinButton.bezelStyle = .rounded
        pinButton.target = self
        pinButton.action = #selector(togglePinSelected)
        
        clearButton.title = "清理未置顶"
        clearButton.bezelStyle = .rounded
        clearButton.target = self
        clearButton.action = #selector(clearUnpinned)
        
        refreshButton.title = ""
        refreshButton.bezelStyle = .texturedRounded
        refreshButton.image = NSImage(systemSymbolName: "arrow.clockwise", accessibilityDescription: "Refresh")
        refreshButton.target = self
        refreshButton.action = #selector(refreshClicked)
        
        jobsLabel.translatesAutoresizingMaskIntoConstraints = false
        jobsScrollView.translatesAutoresizingMaskIntoConstraints = false
        filesLabel.translatesAutoresizingMaskIntoConstraints = false
        filesScrollView.translatesAutoresizingMaskIntoConstraints = false
        
        view.addSubview(jobsLabel)
        view.addSubview(jobsScrollView)
        view.addSubview(filesLabel)
        view.addSubview(filesScrollView)
        
        let buttonStack = NSStackView(views: [playButton, pinButton, deleteButton, clearButton, refreshButton])
        buttonStack.orientation = .horizontal
        buttonStack.spacing = 10
        buttonStack.edgeInsets = NSEdgeInsets(top: 10, left: 15, bottom: 10, right: 15)
        buttonStack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(buttonStack)
        
        NSLayoutConstraint.activate([
            jobsLabel.topAnchor.constraint(equalTo: view.topAnchor, constant: 10),
            jobsLabel.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 15),
            
            jobsScrollView.topAnchor.constraint(equalTo: jobsLabel.bottomAnchor, constant: 5),
            jobsScrollView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            jobsScrollView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            jobsScrollView.heightAnchor.constraint(equalToConstant: 120),
            
            filesLabel.topAnchor.constraint(equalTo: jobsScrollView.bottomAnchor, constant: 15),
            filesLabel.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 15),
            
            filesScrollView.topAnchor.constraint(equalTo: filesLabel.bottomAnchor, constant: 5),
            filesScrollView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            filesScrollView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            filesScrollView.bottomAnchor.constraint(equalTo: buttonStack.topAnchor),
            
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
            if let fetchedJobs = await client.fetchPodcastJobs() {
                self.jobs = fetchedJobs
                self.jobsTableView.reloadData()
            }
            if let fetchedFiles = await client.fetchPodcasts() {
                self.files = fetchedFiles
                self.filesTableView.reloadData()
            }
        }
    }
    
    @objc private func playSelected() {
        let selectedRow = filesTableView.selectedRow
        guard selectedRow >= 0, selectedRow < files.count, let client = coordinator?.processManager.apiClient else { return }
        let file = files[selectedRow]
        guard let filename = file["filename"] as? String else { return }
        Task {
            _ = await client.playPodcast(filename: filename)
        }
    }
    
    @objc private func deleteSelected() {
        let selectedRow = filesTableView.selectedRow
        guard selectedRow >= 0, selectedRow < files.count, let client = coordinator?.processManager.apiClient else { return }
        let file = files[selectedRow]
        guard let filename = file["filename"] as? String else { return }
        Task {
            _ = await client.deletePodcast(filename: filename)
            refreshData()
        }
    }
    
    @objc private func togglePinSelected() {
        let selectedRow = filesTableView.selectedRow
        guard selectedRow >= 0, selectedRow < files.count, let client = coordinator?.processManager.apiClient else { return }
        let file = files[selectedRow]
        guard let filename = file["filename"] as? String else { return }
        Task {
            _ = await client.togglePodcastPin(filename: filename)
            refreshData()
        }
    }
    
    @objc private func clearUnpinned() {
        guard let client = coordinator?.processManager.apiClient else { return }
        Task {
            _ = await client.clearPodcasts()
            refreshData()
        }
    }

    @objc private func filesTableViewDoubleClicked() {
        let selectedRow = filesTableView.selectedRow
        guard selectedRow >= 0, selectedRow < files.count, let client = coordinator?.processManager.apiClient else { return }
        let file = files[selectedRow]
        guard let filename = file["filename"] as? String else { return }
        Task {
            await showPodcastTranscript(filename: filename, client: client)
        }
    }

    private func showPodcastTranscript(filename: String, client: BackendAPIClient) async {
        guard let transcript = await client.fetchPodcastTranscript(filename: filename) else { return }

        DispatchQueue.main.async {
            let alert = NSAlert()
            alert.messageText = filename
            alert.informativeText = transcript
            alert.alertStyle = .informational
            alert.addButton(withTitle: "关闭")
            _ = alert.runModal()
        }
    }
    
    // MARK: - NSTableViewDataSource & NSTableViewDelegate
    
    func numberOfRows(in tableView: NSTableView) -> Int {
        if tableView === jobsTableView {
            return jobs.count
        } else if tableView === filesTableView {
            return files.count
        }
        return 0
    }
    
    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        if tableView === jobsTableView {
            guard row < jobs.count else { return nil }
            let job = jobs[row]
            let cellID = NSUserInterfaceItemIdentifier("JobCell")
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
                case "JobName":
                    display = job["title"] as? String ?? job["job_id"] as? String ?? ""
                case "JobStatus":
                    let status = job["status"] as? String ?? "unknown"
                    let progress = job["progress"] as? String ?? ""
                    display = "\(status) \(progress)"
                default:
                    break
                }
            }
            cell?.textField?.stringValue = display
            return cell
            
        } else if tableView === filesTableView {
            guard row < files.count else { return nil }
            let file = files[row]
            let cellID = NSUserInterfaceItemIdentifier("FileCell")
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
                case "FileName":
                    display = file["filename"] as? String ?? ""
                case "FilePinned":
                    let pinned = file["pinned"] as? Bool ?? false
                    display = pinned ? "📌 已置顶" : ""
                default:
                    break
                }
            }
            cell?.textField?.stringValue = display
            return cell
        }
        return nil
    }
}
