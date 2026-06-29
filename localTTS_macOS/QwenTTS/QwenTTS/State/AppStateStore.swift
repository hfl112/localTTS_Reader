import Foundation

enum BackendState: String {
    case stopped
    case launching
    case waitingForHealth
    case ready
    case stopping
    case failed
}

/// 集中式状态管理。作为唯一的播放/快照数据源：BackendProcessManager 的单一轮询器
/// 写入这里并通知所有订阅者（如 ConsoleViewController），不再各自轮询 /snapshot。
///
/// 该类型上的状态始终在主线程读写；监听者回调也始终在主线程触发。
/// 用 `@MainActor` 让编译器强制这一契约（此前仅靠调用方恰好继承主线程，无强制）。
@MainActor
class AppStateStore {
    // MARK: - 集中式状态
    private(set) var backendState: BackendState = .stopped
    private(set) var currentTitle: String = ""
    private(set) var progressText: String = ""
    private(set) var isPlaying: Bool = false
    private(set) var isPaused: Bool = false

    // ADR-003: the single reconciled playback truth the UI renders. Updated
    // optimistically from command responses and from polls (stale polls dropped).
    private(set) var playbackStatus: PlaybackStatus = .idle
    /// When the last playback command's result was applied — polls issued before
    /// this are pre-command views and must not overwrite the optimistic status.
    private var lastCommandAt: Date?

    /// 最新一次拉到的完整 Snapshot（单一数据源）
    private(set) var lastSnapshot: Snapshot?

    // MARK: - 连接健康度 / 错误冒泡
    /// 轮询 / health 成功时为 true；传输失败时为 false，供 UI 显示“后端未连接/请求失败”。
    private(set) var connectionHealthy: Bool = true
    /// 最近一次传输错误描述（含 path 与 error）；连接恢复后清空。
    private(set) var lastError: String?

    // MARK: - 订阅机制
    /// 监听者表，按整型 token 索引，便于注销。每次有新 Snapshot 时在主线程回调。
    private var snapshotListeners: [Int: (Snapshot) -> Void] = [:]
    private var nextListenerToken = 0

    /// 注册一个快照监听者，返回用于注销的 token。
    @discardableResult
    func addSnapshotListener(_ listener: @escaping (Snapshot) -> Void) -> Int {
        let token = nextListenerToken
        nextListenerToken += 1
        snapshotListeners[token] = listener
        return token
    }

    /// 注销之前注册的监听者。
    func removeSnapshotListener(_ token: Int) {
        snapshotListeners.removeValue(forKey: token)
    }

    // MARK: - 状态更新入口
    func updateBackendState(_ state: BackendState) {
        self.backendState = state
        print("[AppStateStore] Backend state changed -> \(state.rawValue)")
    }

    func updatePlayback(title: String, progress: String, playing: Bool, paused: Bool) {
        self.currentTitle = title
        self.progressText = progress
        self.isPlaying = playing
        self.isPaused = paused
    }

    /// ADR-003: apply a playback command's returned status optimistically, so the
    /// UI flips immediately instead of waiting for the next ~500ms poll. Records
    /// the time so in-flight (pre-command) polls don't overwrite it.
    func applyCommandResult(_ status: PlaybackStatus, at now: Date = Date()) {
        self.playbackStatus = status
        self.lastCommandAt = now
    }

    /// 单一轮询器拉到 snapshot 后调用：更新派生字段并通知所有监听者。
    /// `issuedAt` = 该次轮询请求的发起时刻，用于丢弃命令之前的过期视图（ADR-003 #5）。
    func updateSnapshot(_ snapshot: Snapshot, issuedAt: Date = Date()) {
        self.lastSnapshot = snapshot
        let polled = PlaybackStatus(rawValue: snapshot.playback_status ?? "") ?? .unknown
        self.playbackStatus = PlaybackReconciler.reconcile(
            current: self.playbackStatus,
            polled: polled,
            polledIssuedAt: issuedAt,
            lastCommandAt: self.lastCommandAt
        )
        updatePlayback(
            title: snapshot.main_title ?? "",
            progress: snapshot.main_progress ?? "",
            playing: snapshot.main_is_playing ?? false,
            paused: snapshot.is_paused ?? false
        )
        // 拉到 snapshot 说明连接正常
        if !connectionHealthy || lastError != nil {
            connectionHealthy = true
            lastError = nil
        }
        // 通知订阅者（@MainActor 保证主线程）。先快照成数组再遍历：监听者回调可能
        // 在视图出现/消失时重入注册/注销，直接遍历字典会因迭代中改集合而崩溃。
        for listener in Array(snapshotListeners.values) {
            listener(snapshot)
        }
    }

    /// 轮询 / health 失败时调用，记录错误并标记连接不健康。
    func reportConnectionError(_ message: String) {
        self.connectionHealthy = false
        self.lastError = message
        print("[AppStateStore] Connection error: \(message)")
    }
}
