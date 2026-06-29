import Foundation

/// ADR-003 (#5): reconcile the optimistic status (set from a command response)
/// against the 500ms polling stream. A poll whose fetch was *issued before* the
/// most recent command was applied is a pre-command view — drop it (keep
/// current) so it can't flicker the UI back. Otherwise adopt the polled status.
enum PlaybackReconciler {
    static func reconcile(
        current: PlaybackStatus,
        polled: PlaybackStatus,
        polledIssuedAt: Date,
        lastCommandAt: Date?
    ) -> PlaybackStatus {
        if let cmd = lastCommandAt, polledIssuedAt < cmd {
            return current   // stale poll, predates the command — ignore it
        }
        return polled
    }
}
