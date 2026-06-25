import AppKit

extension Notification.Name {
    /// 跨页跳转到「AI 引擎」配置页（如 Console 失败提示中的「打开 AI 引擎」按钮）。
    static let qwenShowEngineSettings = Notification.Name("qwenShowEngineSettings")
}

@MainActor
class MainSplitViewController: NSSplitViewController {
    /// tabVC 中「AI 引擎」页的索引（与 viewDidLoad 中的添加顺序一致）。
    private static let engineTabIndex = 3
    weak var coordinator: ApplicationCoordinator?
    
    private let sidebarVC = SidebarViewController()
    private let tabVC = NSTabViewController()
    
    init(coordinator: ApplicationCoordinator) {
        self.coordinator = coordinator
        super.init(nibName: nil, bundle: nil)
    }
    
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
    
    override func viewDidLoad() {
        super.viewDidLoad()
        
        // 1. Sidebar Item
        let sidebarItem = NSSplitViewItem(sidebarWithViewController: sidebarVC)
        sidebarItem.minimumThickness = 200
        sidebarItem.maximumThickness = 250
        sidebarItem.canCollapse = false
        self.addSplitViewItem(sidebarItem)
        
        // 2. Content Tabs
        tabVC.tabStyle = .unspecified // 隐藏 TabBar
        
        let consoleVC = ConsoleViewController(coordinator: coordinator)
        tabVC.addTabViewItem(NSTabViewItem(viewController: consoleVC))
        
        let libraryVC = LibraryHostingController(coordinator: coordinator)
        tabVC.addTabViewItem(NSTabViewItem(viewController: libraryVC))
        
        let settingsVC = SettingsHostingController(coordinator: coordinator)
        tabVC.addTabViewItem(NSTabViewItem(viewController: settingsVC))

        // tag3：AI 引擎 / 翻译配置页（sidebar 与 tabVC 1:1 映射，无需改 onSelectTab）
        let engineVC = EngineSettingsViewController(coordinator: coordinator)
        tabVC.addTabViewItem(NSTabViewItem(viewController: engineVC))

        let contentItem = NSSplitViewItem(viewController: tabVC)
        self.addSplitViewItem(contentItem)
        
        // 3. 联动
        sidebarVC.onSelectTab = { [weak self] index in
            let logPath = "/tmp/qwentts_debug.log"
            if let data = "Tab selected: \(index)\n".data(using: .utf8) {
                if let fileHandle = try? FileHandle(forWritingTo: URL(fileURLWithPath: logPath)) {
                    fileHandle.seekToEndOfFile()
                    fileHandle.write(data)
                    fileHandle.closeFile()
                }
            }
            self?.tabVC.selectedTabViewItemIndex = index
            if let count = self?.tabVC.tabViewItems.count {
                if let data = "Tab changed to \(self?.tabVC.selectedTabViewItemIndex ?? -1), total tabs: \(count)\n".data(using: .utf8) {
                    if let fileHandle = try? FileHandle(forWritingTo: URL(fileURLWithPath: logPath)) {
                        fileHandle.seekToEndOfFile()
                        fileHandle.write(data)
                        fileHandle.closeFile()
                    }
                }
            }
        }

        // 跨页跳转：Console 失败提示中的「打开 AI 引擎」按钮 → 切到引擎配置页。
        NotificationCenter.default.addObserver(
            forName: .qwenShowEngineSettings, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.sidebarVC.selectTab(MainSplitViewController.engineTabIndex)
            }
        }
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
    }
}
