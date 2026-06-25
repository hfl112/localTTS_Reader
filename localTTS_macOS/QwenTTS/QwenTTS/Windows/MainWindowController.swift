import AppKit

class RootViewController: NSViewController {
    override func loadView() {
        let effectView = NSVisualEffectView()
        effectView.material = .underWindowBackground
        effectView.blendingMode = .behindWindow
        effectView.state = .active
        self.view = effectView
    }
}

@MainActor
class MainWindowController: NSWindowController {
    weak var coordinator: ApplicationCoordinator?
    private var splitVC: MainSplitViewController?
    
    init(coordinator: ApplicationCoordinator) {
        self.coordinator = coordinator
        
        // 创建无标题栏且内容区铺满的现代窗口 (支持 fullSizeContentView 延伸毛玻璃)
        let window = NSWindow(
            contentRect: NSRect(x: 100, y: 100, width: 850, height: 550),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        
        // 隐藏标题栏文字，使标题栏透明化以露出毛玻璃
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.title = "QwenTTS 控制台"
        window.minSize = NSSize(width: 800, height: 500)
        window.isReleasedWhenClosed = false
        window.backgroundColor = .clear
        window.isOpaque = false
        
        super.init(window: window)
        
        let rootVC = RootViewController()
        let splitVC = MainSplitViewController(coordinator: coordinator)
        self.splitVC = splitVC
        
        rootVC.addChild(splitVC)
        rootVC.view.addSubview(splitVC.view)
        splitVC.view.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            splitVC.view.topAnchor.constraint(equalTo: rootVC.view.topAnchor),
            splitVC.view.leadingAnchor.constraint(equalTo: rootVC.view.leadingAnchor),
            splitVC.view.trailingAnchor.constraint(equalTo: rootVC.view.trailingAnchor),
            splitVC.view.bottomAnchor.constraint(equalTo: rootVC.view.bottomAnchor)
        ])
        
        window.contentViewController = rootVC
        window.center()
        window.makeKeyAndOrderFront(nil)
    }
    
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    /// 切换主窗口内容页（转发给 split view），供 coordinator 打开指定页。
    func selectTab(_ index: Int) {
        _ = self.window           // 确保已 loadWindow，splitVC 已创建
        splitVC?.selectTab(index)
    }
}
