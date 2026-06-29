import XCTest

/// ADR-003 B1: proves the logic-only unit-test target builds and runs under
/// `xcodebuild test`. Real behavior tests (PlaybackPresentation, reconcile,
/// Snapshot decode, MockBackend conformance) land in B2/B3/B4a/B5.
final class PlaceholderTests: XCTestCase {
    func testHarnessRuns() {
        XCTAssertEqual(1 + 1, 2)
    }
}
