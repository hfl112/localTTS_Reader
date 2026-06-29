import XCTest

/// ADR-003 B3: the poll/command reconciliation that kills the "闪一下" flicker.
final class PlaybackReconcilerTests: XCTestCase {
    func testPollIssuedBeforeCommandIsDropped() {
        let pollIssued = Date(timeIntervalSince1970: 100)
        let commandAt = Date(timeIntervalSince1970: 200)
        // optimistic current=paused; a stale in-flight poll says playing → keep paused
        XCTAssertEqual(
            PlaybackReconciler.reconcile(current: .paused, polled: .playing,
                                         polledIssuedAt: pollIssued, lastCommandAt: commandAt),
            .paused
        )
    }

    func testPollIssuedAfterCommandIsAdopted() {
        let commandAt = Date(timeIntervalSince1970: 100)
        let pollIssued = Date(timeIntervalSince1970: 200)
        XCTAssertEqual(
            PlaybackReconciler.reconcile(current: .paused, polled: .playing,
                                         polledIssuedAt: pollIssued, lastCommandAt: commandAt),
            .playing
        )
    }

    func testNoPriorCommandAdoptsPoll() {
        XCTAssertEqual(
            PlaybackReconciler.reconcile(current: .idle, polled: .playing,
                                         polledIssuedAt: Date(), lastCommandAt: nil),
            .playing
        )
    }
}
