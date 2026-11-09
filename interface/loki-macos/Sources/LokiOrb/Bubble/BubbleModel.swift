import Foundation
import SwiftUI

/// What the assistant is doing right now. This is the single source of truth the bubble,
/// captions and header all derive from.
enum AssistantPhase: Equatable {
    case idle
    case listening
    case thinking
    case executing
    case responding
    case awaitingReply
    case completed
    case error

    var title: String {
        switch self {
        case .idle: return "Ready"
        case .listening: return "Listening"
        case .thinking: return "Thinking"
        case .executing: return "Working"
        case .responding: return "Responding"
        case .awaitingReply: return "Waiting for your reply"
        case .completed: return "Done"
        case .error: return "Something went wrong"
        }
    }

    /// A glyph per state so the state is never communicated by color alone.
    var symbol: String {
        switch self {
        case .idle: return "circle.dotted"
        case .listening: return "waveform"
        case .thinking: return "ellipsis"
        case .executing: return "bolt.fill"
        case .responding: return "sparkles"
        case .awaitingReply: return "bubble.left"
        case .completed: return "checkmark"
        case .error: return "exclamationmark.triangle"
        }
    }

    /// Dominant hue, used for subtle UI tinting (card ambience, status glyphs).
    var tint: LokiRGB {
        switch self {
        case .idle: return LokiDesign.Palette.indigo
        case .listening: return LokiDesign.Palette.cyan
        case .thinking: return LokiDesign.Palette.violet
        case .executing: return LokiDesign.Palette.cyan
        case .responding: return LokiDesign.Palette.electricBlue
        case .awaitingReply: return LokiDesign.Palette.cyan
        case .completed: return LokiDesign.Palette.mint
        case .error: return LokiDesign.Palette.ember
        }
    }

    /// Phases that are actively doing work on the user's behalf.
    var isBusy: Bool {
        switch self {
        case .thinking, .executing, .responding: return true
        default: return false
        }
    }

    /// Frame interval for the bubble's timeline. Quiet states render at a low rate.
    var renderInterval: TimeInterval {
        switch self {
        case .idle, .error: return LokiDesign.Bubble.idleInterval
        case .awaitingReply: return 1.0 / 30.0
        default: return LokiDesign.Bubble.activeInterval
        }
    }
}

/// Real-time level meter shared between audio sources and the bubble renderer.
///
/// Deliberately *not* observable: the level changes 40-60 times per second and publishing it would
/// invalidate SwiftUI views. The renderer polls `sample(at:)` once per drawn frame instead. All
/// access happens on the main thread.
final class AudioMeter {
    private var target: Double = 0
    private var shown: Double = 0
    private var lastSampleTime: TimeInterval = 0

    func push(_ level: CGFloat) {
        target = min(1, max(0, Double(level)))
    }

    func reset() {
        target = 0
    }

    /// Returns the display level, eased toward the latest pushed value (fast attack, slower release)
    /// so discrete audio buffers read as continuous motion at any frame rate.
    func sample(at time: TimeInterval) -> Double {
        let dt = lastSampleTime == 0 ? 1.0 / 60.0 : min(0.1, max(0, time - lastSampleTime))
        lastSampleTime = time
        let rate = target > shown ? 24.0 : 7.0
        shown += (target - shown) * (1 - exp(-rate * dt))
        if shown < 0.001 && target == 0 { shown = 0 }
        return shown
    }
}

/// Observable state for the bubble only. Keeping it separate from `LokiController` means streaming
/// text and conversation updates never re-render the bubble, and vice versa.
@MainActor
final class BubbleModel: ObservableObject {
    @Published var phase: AssistantPhase = .idle
    /// Transient 0...1 swell used for immediate tactile feedback (expand, tap).
    @Published private(set) var emphasis: Double = 0
    /// Start of the most recent completion burst (seconds since reference date).
    @Published private(set) var burstStart: TimeInterval?
    /// False while the panel is occluded or the display is asleep, so the timeline can stop drawing.
    @Published var isRendering: Bool = true

    let meter = AudioMeter()
    private var emphasisTask: Task<Void, Never>?

    func kick() {
        emphasisTask?.cancel()
        withAnimation(.spring(response: 0.22, dampingFraction: 0.62)) { emphasis = 1 }
        emphasisTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 220_000_000)
            guard !Task.isCancelled else { return }
            withAnimation(.easeOut(duration: 0.7)) { self?.emphasis = 0 }
        }
    }

    func celebrate() {
        burstStart = Date().timeIntervalSinceReferenceDate
    }

    deinit {
        emphasisTask?.cancel()
    }
}
