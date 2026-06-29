import Foundation

/// ADR-003 (#7): the single `status → (action, icon, label)` mapping for the
/// play/pause button. Replaces the three duplicated `if isPlaying/isPaused`
/// branches in ConsoleViewController, PlaybackPopoverController, and render().
///
/// The table owns *which command* the button issues, its SF Symbol, and its
/// label. The `.read` action's content source (text field vs clipboard) stays
/// in the view controller — only that one branch is content-dependent.
enum PlaybackAction: Equatable {
    case read      // idle/unknown: start reading (VC supplies the content)
    case pause     // playing/generating
    case resume    // paused
}

/// 三独立键(Dashboard)中 ▶ 播放键的唯一决策:
/// 之前暂停 → 继续;否则(停止/空闲/在放/生成)→ 从头播放当前文章。
/// 暂停键永远只暂停、停止键永远只停止,无需决策。
enum PlayButtonIntent: Equatable {
    case resume                 // 之前暂停 → 继续
    case restartFromBeginning   // 之前停止/空闲 → 从头
}

func playButtonIntent(for status: PlaybackStatus) -> PlayButtonIntent {
    status == .paused ? .resume : .restartFromBeginning
}

struct PlaybackPresentation: Equatable {
    let action: PlaybackAction
    let iconName: String
    let buttonLabel: String

    init(_ status: PlaybackStatus) {
        switch status {
        case .playing:
            action = .pause; iconName = "pause.fill"; buttonLabel = "暂停"
        case .generating:
            action = .pause; iconName = "pause.fill"; buttonLabel = "暂停"
        case .paused:
            action = .resume; iconName = "play.fill"; buttonLabel = "继续"
        case .idle, .unknown:
            action = .read; iconName = "play.fill"; buttonLabel = "播放"
        }
    }
}
