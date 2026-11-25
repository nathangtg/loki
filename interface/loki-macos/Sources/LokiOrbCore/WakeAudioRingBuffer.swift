import Foundation

/// Tracks the durations of the most recent wake-audio buffers, trimming from the front so the
/// total never exceeds `maxDuration` (2.0s). Pure bookkeeping: it holds no audio samples.
public struct WakeAudioRingBuffer: Equatable {
    public static let maxDuration: TimeInterval = 2.0

    /// Durations of retained chunks, oldest first.
    public private(set) var chunks: [TimeInterval] = []

    public init() {}

    public var totalDuration: TimeInterval { chunks.reduce(0, +) }

    /// Appends a buffer of `durationSeconds` and trims from the front until the total is at most 2.0s.
    /// Non-finite or non-positive durations are ignored.
    public mutating func append(durationSeconds: TimeInterval) {
        guard durationSeconds.isFinite, durationSeconds > 0 else { return }
        chunks.append(min(durationSeconds, Self.maxDuration))
        var excess = totalDuration - Self.maxDuration
        while excess > 0, let first = chunks.first {
            if first <= excess {
                chunks.removeFirst()
                excess -= first
            } else {
                chunks[0] = first - excess
                excess = 0
            }
        }
        // Guard against floating-point residue.
        if totalDuration > Self.maxDuration, !chunks.isEmpty {
            chunks[0] = max(0, chunks[0] - (totalDuration - Self.maxDuration))
            if chunks[0] == 0 { chunks.removeFirst() }
        }
    }

    public mutating func removeAll() { chunks.removeAll() }
}
