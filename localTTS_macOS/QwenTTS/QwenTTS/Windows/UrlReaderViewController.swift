import AppKit

@MainActor
class UrlReaderViewController: NSViewController, NSTableViewDataSource, NSTableViewDelegate {
    weak var coordinator: ApplicationCoordinator?
    
    private let urlLabel = NSTextField(labelWithString: "输入网页地址 (URL):")
    private let urlTextField = NSTextField()
    private let translateCheckbox = NSButton(checkboxWithTitle: "翻译为中文", target: nil, action: nil)
    private let saveCheckbox = NSButton(checkboxWithTitle: "保存到稍后阅读", target: nil, action: nil)
    private let podcastCheckbox = NSButton(checkboxWithTitle: "直接生成播客", target: nil, action: nil)
    private let submitButton = NSButton()
    
    private let jobsLabel = NSTextField(labelWithString: "网页抓取任务列表")
    private let scrollView = NSScrollView()
    private let tableView = NSTableView()
    private var jobs: [[String: Any]] = []
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
        urlTextField.placeholderString = "https://example.com/article"
        urlTextField.bezelStyle = .roundedBezel
        
        submitButton.title = "提交网页抓取"
        submitButton.bezelStyle = .rounded
        submitButton.target = self
        submitButton.action = #selector(submitClicked)
        
        tableView.dataSource = self
        tableView.delegate = self
        tableView.usesAlternatingRowBackgroundColors = true
        tableView.headerView = NSTableHeaderView()
        
        let colUrl = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("URL"))
        colUrl.title = "网址"
        colUrl.width = 320
        tableView.addTableColumn(colUrl)
        
        let colStatus = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("Status"))
        colStatus.title = "状态"
        colStatus.width = 120
        tableView.addTableColumn(colStatus)
        
        scrollView.documentView = tableView
        scrollView.hasVerticalScroller = true
        scrollView.borderType = .noBorder
        
        jobsLabel.font = NSFont.boldSystemFont(ofSize: 12)
        
        refreshButton.title = ""
        refreshButton.bezelStyle = .texturedRounded
        refreshButton.image = NSImage(systemSymbolName: "arrow.clockwise", accessibilityDescription: "Refresh")
        refreshButton.target = self
        refreshButton.action = #selector(refreshClicked)
        
        urlLabel.translatesAutoresizingMaskIntoConstraints = false
        urlTextField.translatesAutoresizingMaskIntoConstraints = false
        translateCheckbox.translatesAutoresizingMaskIntoConstraints = false
        saveCheckbox.translatesAutoresizingMaskIntoConstraints = false
        podcastCheckbox.translatesAutoresizingMaskIntoConstraints = false
        submitButton.translatesAutoresizingMaskIntoConstraints = false
        jobsLabel.translatesAutoresizingMaskIntoConstraints = false
        scrollView.translatesAutoresizingMaskIntoConstraints = false
        
        view.addSubview(urlLabel)
        view.addSubview(urlTextField)
        view.addSubview(translateCheckbox)
        view.addSubview(saveCheckbox)
        view.addSubview(podcastCheckbox)
        view.addSubview(submitButton)
        view.addSubview(jobsLabel)
        view.addSubview(scrollView)
        
        let buttonStack = NSStackView(views: [refreshButton])
        buttonStack.orientation = .horizontal
        buttonStack.edgeInsets = NSEdgeInsets(top: 10, left: 15, bottom: 10, right: 15)
        buttonStack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(buttonStack)
        
        NSLayoutConstraint.activate([
            urlLabel.topAnchor.constraint(equalTo: view.topAnchor, constant: 15),
            urlLabel.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 15),
            
            urlTextField.topAnchor.constraint(equalTo: urlLabel.bottomAnchor, constant: 5),
            urlTextField.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 15),
            urlTextField.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -15),
            
            translateCheckbox.topAnchor.constraint(equalTo: urlTextField.bottomAnchor, constant: 10),
            translateCheckbox.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 15),
            
            saveCheckbox.topAnchor.constraint(equalTo: translateCheckbox.topAnchor),
            saveCheckbox.leadingAnchor.constraint(equalTo: translateCheckbox.trailingAnchor, constant: 20),
            
            podcastCheckbox.topAnchor.constraint(equalTo: translateCheckbox.topAnchor),
            podcastCheckbox.leadingAnchor.constraint(equalTo: saveCheckbox.trailingAnchor, constant: 20),
            
            submitButton.topAnchor.constraint(equalTo: translateCheckbox.bottomAnchor, constant: 10),
            submitButton.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 15),
            submitButton.widthAnchor.constraint(equalToConstant: 120),
            
            jobsLabel.topAnchor.constraint(equalTo: submitButton.bottomAnchor, constant: 20),
            jobsLabel.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 15),
            
            scrollView.topAnchor.constraint(equalTo: jobsLabel.bottomAnchor, constant: 5),
            scrollView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            scrollView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            scrollView.bottomAnchor.constraint(equalTo: buttonStack.topAnchor),
            
            buttonStack.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            buttonStack.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            buttonStack.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            buttonStack.heightAnchor.constraint(equalToConstant: 45)
        ])
    }
    
    @objc private func submitClicked() {
        let url = urlTextField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !url.isEmpty, let client = coordinator?.processManager.apiClient else { return }
        
        let translate = translateCheckbox.state == .on
        let save = saveCheckbox.state == .on
        let podcast = podcastCheckbox.state == .on
        
        Task {
            let success = await client.readUrl(
                url: url,
                translate: translate,
                mode: translate ? "translate" : "original",
                save: save,
                podcast: podcast
            )
            if success {
                urlTextField.stringValue = ""
                refreshData()
            }
        }
    }
    
    @objc private func refreshClicked() {
        refreshData()
    }
    
    private func refreshData() {
        guard let client = coordinator?.processManager.apiClient else { return }
        Task {
            if let fetchedJobs = await client.fetchUrlJobs() {
                self.jobs = fetchedJobs
                self.tableView.reloadData()
            }
        }
    }
    
    // MARK: - NSTableViewDataSource & NSTableViewDelegate
    
    func numberOfRows(in tableView: NSTableView) -> Int {
        return jobs.count
    }
    
    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        guard row < jobs.count else { return nil }
        let job = jobs[row]
        let cellID = NSUserInterfaceItemIdentifier("UrlJobCell")
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
            case "URL":
                display = job["url"] as? String ?? job["job_id"] as? String ?? ""
            case "Status":
                let status = job["status"] as? String ?? "unknown"
                display = status
            default:
                break
            }
        }
        cell?.textField?.stringValue = display
        return cell
    }
}
