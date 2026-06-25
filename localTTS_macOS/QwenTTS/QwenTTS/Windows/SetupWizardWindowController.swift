import AppKit

class SetupWizardWindowController: NSWindowController {
    convenience init(onContinue: ((@escaping (String?) -> Void) -> Void)? = nil,
                     onComplete: @escaping () -> Void) {
        let vc = SetupWizardViewController()
        vc.onContinue = onContinue
        vc.onComplete = onComplete
        
        let window = NSWindow(contentViewController: vc)
        window.title = "首次配置"
        window.styleMask = [.titled, .closable, .miniaturizable, .fullSizeContentView]
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.isOpaque = false
        window.backgroundColor = .clear
        window.center()
        
        self.init(window: window)
    }
}
