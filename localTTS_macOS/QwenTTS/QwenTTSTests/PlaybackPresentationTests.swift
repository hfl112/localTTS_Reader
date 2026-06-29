import XCTest

/// ADR-003 B2: the status→presentation mapping (replacing 3 duplicated button
/// logics) and the lenient enum decode.
final class PlaybackPresentationTests: XCTestCase {
    func testStatusMapsToButtonAction() {
        XCTAssertEqual(PlaybackPresentation(.idle).action, .read)
        XCTAssertEqual(PlaybackPresentation(.unknown).action, .read)
        XCTAssertEqual(PlaybackPresentation(.playing).action, .pause)
        XCTAssertEqual(PlaybackPresentation(.generating).action, .pause)   // generating is pausable
        XCTAssertEqual(PlaybackPresentation(.paused).action, .resume)
    }

    func testIconAndLabel() {
        XCTAssertEqual(PlaybackPresentation(.playing).iconName, "pause.fill")
        XCTAssertEqual(PlaybackPresentation(.paused).buttonLabel, "继续")
        XCTAssertEqual(PlaybackPresentation(.idle).buttonLabel, "播放")
    }

    func testPlayButtonIntent() {
        // ▶ play key: paused → resume; everything else → restart from beginning.
        XCTAssertEqual(playButtonIntent(for: .paused), .resume)
        XCTAssertEqual(playButtonIntent(for: .idle), .restartFromBeginning)        // 停止后
        XCTAssertEqual(playButtonIntent(for: .playing), .restartFromBeginning)
        XCTAssertEqual(playButtonIntent(for: .generating), .restartFromBeginning)
        XCTAssertEqual(playButtonIntent(for: .unknown), .restartFromBeginning)
    }

    func testDecodeFromWireString() throws {
        let dec = JSONDecoder()
        XCTAssertEqual(try dec.decode(PlaybackStatus.self, from: Data("\"paused\"".utf8)), .paused)
        XCTAssertEqual(try dec.decode(PlaybackStatus.self, from: Data("\"generating\"".utf8)), .generating)
        // unknown wire value must not crash — falls back to .unknown
        XCTAssertEqual(try dec.decode(PlaybackStatus.self, from: Data("\"future_state\"".utf8)), .unknown)
    }
}
