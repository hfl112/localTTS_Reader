import XCTest

/// ADR-003 B4a: the store's optimistic-update + poll-reconciliation wiring.
@MainActor
final class AppStateStoreTests: XCTestCase {
    func testCommandResultIsOptimisticAndStalePollDropped() {
        let store = AppStateStore()
        let commandAt = Date(timeIntervalSince1970: 1000)

        // Command says paused → reflected immediately (no poll needed).
        store.applyCommandResult(.paused, at: commandAt)
        XCTAssertEqual(store.playbackStatus, .paused)

        // An in-flight poll issued BEFORE the command (stale, says playing) must
        // NOT overwrite the optimistic status.
        var stale = Snapshot()
        stale.playback_status = "playing"
        store.updateSnapshot(stale, issuedAt: Date(timeIntervalSince1970: 999))
        XCTAssertEqual(store.playbackStatus, .paused)

        // A poll issued AFTER the command is authoritative again.
        var fresh = Snapshot()
        fresh.playback_status = "idle"
        store.updateSnapshot(fresh, issuedAt: Date(timeIntervalSince1970: 1001))
        XCTAssertEqual(store.playbackStatus, .idle)
    }

    func testUnknownWireValueDecodesToUnknown() {
        let store = AppStateStore()
        var snap = Snapshot()
        snap.playback_status = "some_future_state"
        store.updateSnapshot(snap, issuedAt: Date())
        XCTAssertEqual(store.playbackStatus, .unknown)
    }
}
