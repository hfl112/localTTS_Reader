import Foundation

/// 后端 `/health` 就绪监控。从 `BackendProcessManager` 拆出，使 manager 专注"协调状态机"，
/// 健康轮询与超时判断独立可测/可复用。
///
/// 在主 actor 上运行：`onReady` / `onTimeout` 均在主线程回调，调用方（@MainActor 的
/// `BackendProcessManager`）可直接更新状态而无需再做线程切换。
@MainActor
final class BackendHealthMonitor {
    private var task: Task<Void, Never>?
    private var launchTime: Date?

    /// 每 500ms 轮询一次 `/health`，直到：
    /// - 后端就绪 → 调用 `onReady` 并停止；
    /// - 超过 `timeout` 仍未就绪 → 调用 `onTimeout` 并停止。
    /// 重复调用会先取消上一轮。
    func start(
        apiClient: BackendAPIClient?,
        token: @escaping () -> String,
        timeout: TimeInterval = 30.0,
        onReady: @escaping () -> Void,
        onTimeout: @escaping () -> Void
    ) {
        task?.cancel()
        launchTime = Date()
        task = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(500))
                guard let self, !Task.isCancelled else { return }
                let (alive, _) = await apiClient?.checkHealth(token: token()) ?? (false, nil)
                if alive {
                    onReady()
                    return
                }
                if let launchTime = self.launchTime,
                   Date().timeIntervalSince(launchTime) > timeout {
                    onTimeout()
                    return
                }
            }
        }
    }

    func cancel() {
        task?.cancel()
        task = nil
        launchTime = nil
    }
}
