import AppKit

@MainActor
class MainTabViewController: NSTabViewController {
    weak var coordinator: ApplicationCoordinator?

    init(coordinator: ApplicationCoordinator) {
        self.coordinator = coordinator
        super.init(nibName: nil, bundle: nil)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        
        // 开启现代原生感十足的 Toolbar 导航风格
        self.tabStyle = .toolbar
        
        // 1. 朗读中控 (Console)
        let consoleVC = ConsoleViewController(coordinator: coordinator)
        let consoleItem = NSTabViewItem(viewController: consoleVC)
        consoleItem.label = "朗读中控"
        consoleItem.image = NSImage(systemSymbolName: "play.circle.fill", accessibilityDescription: nil)
        addTabViewItem(consoleItem)
        
        // 2. 网页抓取 (URL Reader)
        let urlVC = UrlReaderViewController(coordinator: coordinator)
        let urlItem = NSTabViewItem(viewController: urlVC)
        urlItem.label = "网页抓取"
        urlItem.image = NSImage(systemSymbolName: "link", accessibilityDescription: nil)
        addTabViewItem(urlItem)
        
        // 3. 播客管理 (Podcasts)
        let podcastVC = PodcastViewController(coordinator: coordinator)
        let podcastItem = NSTabViewItem(viewController: podcastVC)
        podcastItem.label = "播客管理"
        podcastItem.image = NSImage(systemSymbolName: "podcast", accessibilityDescription: nil)
        addTabViewItem(podcastItem)
        
        // 4. 稍后阅读 (Saved Items)
        let savedVC = SavedItemsViewController(coordinator: coordinator)
        let savedItem = NSTabViewItem(viewController: savedVC)
        savedItem.label = "稍后阅读"
        savedItem.image = NSImage(systemSymbolName: "bookmark.fill", accessibilityDescription: nil)
        addTabViewItem(savedItem)
        
        // 5. 缓存清理 (Cache)
        let cacheVC = CacheViewController(coordinator: coordinator)
        let cacheItem = NSTabViewItem(viewController: cacheVC)
        cacheItem.label = "缓存清理"
        cacheItem.image = NSImage(systemSymbolName: "trash", accessibilityDescription: nil)
        addTabViewItem(cacheItem)
        
        // 6. 系统设置 (Settings)
        let settingsVC = SettingsViewController(coordinator: coordinator)
        let settingsItem = NSTabViewItem(viewController: settingsVC)
        settingsItem.label = "系统设置"
        settingsItem.image = NSImage(systemSymbolName: "gearshape.2.fill", accessibilityDescription: nil)
        addTabViewItem(settingsItem)
        
        // 7. 运行环境 (Environment)
        let envVC = EnvironmentViewController(coordinator: coordinator)
        let envItem = NSTabViewItem(viewController: envVC)
        envItem.label = "运行环境"
        envItem.image = NSImage(systemSymbolName: "terminal.fill", accessibilityDescription: nil)
        addTabViewItem(envItem)
    }
}
