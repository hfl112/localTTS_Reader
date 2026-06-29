import Foundation

/// ADR-003: the single computed playback truth, mirrored from the backend's
/// `playback_status` wire field. The frontend renders this directly and never
/// re-derives "is playing / is paused" from scattered booleans.
///
/// Decodes leniently: any unrecognized wire value becomes `.unknown` (treated
/// like idle by presentation) so a backend that adds a state can't crash the UI.
enum PlaybackStatus: String, Codable, Equatable {
    case idle
    case generating
    case playing
    case paused
    case unknown

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = PlaybackStatus(rawValue: raw) ?? .unknown
    }
}
