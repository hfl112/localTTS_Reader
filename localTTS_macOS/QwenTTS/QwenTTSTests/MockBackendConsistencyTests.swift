import XCTest

/// ADR-003 B5: MockBackend's playback_status mapping must match the backend
/// predicate (esp. COOLING → playing, not generating) and decode to a known
/// PlaybackStatus, so the mock can't drift from the real contract.
final class MockBackendConsistencyTests: XCTestCase {
    func testMockStateMapsToBackendStatus() {
        XCTAssertEqual(MockBackend.playbackStatusString(for: .idle), "idle")
        XCTAssertEqual(MockBackend.playbackStatusString(for: .speaking), "playing")
        XCTAssertEqual(MockBackend.playbackStatusString(for: .paused), "paused")
        XCTAssertEqual(MockBackend.playbackStatusString(for: .cooling), "playing")
    }

    func testEveryMockStatusDecodesToKnownEnum() throws {
        let dec = JSONDecoder()
        for state: MockBackend.PlaybackState in [.idle, .speaking, .paused, .cooling] {
            let raw = MockBackend.playbackStatusString(for: state)
            let decoded = try dec.decode(PlaybackStatus.self, from: Data("\"\(raw)\"".utf8))
            XCTAssertNotEqual(decoded, .unknown, "mock value \(raw) is not a known PlaybackStatus")
        }
    }
}
