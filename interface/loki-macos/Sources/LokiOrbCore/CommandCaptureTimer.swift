import Foundation

/// Timing configuration for a wake-triggered command capture.
public struct CommandCaptureConfig: Equatable, Sendable {
    /// Continuous silence after speech that starts the grace window.
    public var silenceBeforeGrace: TimeInterval
    /// Length of the grace window before the command is submitted.
    public var graceWindowDuration: TimeInterval
    /// Time without any speech after the wake phrase before the capture is abandoned.
    public var noSpeechTimeout: TimeInterval

    public init(
        silenceBeforeGrace: TimeInterval = 1.5,
        graceWindowDuration: TimeInterval = 3.0,
        noSpeechTimeout: TimeInterval = 8.0
    ) {
        self.silenceBeforeGrace = silenceBeforeGrace
        self.graceWindowDuration = graceWindowDuration
        self.noSpeechTimeout = noSpeechTimeout
    }
}

public enum CaptureEvent: Equatable, Sendable {
    case audioLevel(isSpeech: Bool, at: TimeInterval)
    case cancelRequested(at: TimeInterval)
    case hotkeyOverride(at: TimeInterval)
}

public enum SubmitReason: Equatable, Sendable {
    case graceWindowElapsed
    case hotkeyOverride
}

public enum CaptureOutcome: Equatable, Sendable {
    case continueCapturing
    case enteredGraceWindow
    case resumedCapturing
    case submit(reason: SubmitReason)
    case cancelled
    case timedOutNoSpeech
}

/// Pure state machine deciding when a wake-triggered capture enters the grace window,
/// resumes, submits, cancels or times out. It has no dependency on audio APIs.
public struct CommandCaptureTimer {
    private enum Phase: Equatable {
        case capturing(hasHeardSpeech: Bool, lastSpeechAt: TimeInterval?)
        case grace(enteredAt: TimeInterval)
        case finished(CaptureOutcome)
    }

    private var phase: Phase
    private let config: CommandCaptureConfig
    private let startedAt: TimeInterval

    public init(startedAt: TimeInterval, config: CommandCaptureConfig = CommandCaptureConfig()) {
        self.startedAt = startedAt
        self.config = config
        self.phase = .capturing(hasHeardSpeech: false, lastSpeechAt: nil)
    }

    /// True once the timer reached a terminal outcome (submit, cancelled or timed out).
    public var isFinished: Bool {
        if case .finished = phase { return true }
        return false
    }

    /// Advances the state machine. Once finished, every call returns the same terminal
    /// outcome without evaluating the event.
    public mutating func handle(_ event: CaptureEvent) -> CaptureOutcome {
        if case .finished(let outcome) = phase { return outcome }

        switch event {
        case .cancelRequested:
            return finish(.cancelled)
        case .hotkeyOverride:
            return finish(.submit(reason: .hotkeyOverride))
        case .audioLevel(let isSpeech, let at):
            switch phase {
            case .capturing(let hasHeardSpeech, let lastSpeechAt):
                return handleCapturing(
                    isSpeech: isSpeech, at: at,
                    hasHeardSpeech: hasHeardSpeech, lastSpeechAt: lastSpeechAt)
            case .grace(let enteredAt):
                if at - enteredAt >= config.graceWindowDuration {
                    return finish(.submit(reason: .graceWindowElapsed))
                }
                if isSpeech {
                    phase = .capturing(hasHeardSpeech: true, lastSpeechAt: at)
                    return .resumedCapturing
                }
                return .continueCapturing
            case .finished(let outcome):
                return outcome
            }
        }
    }

    private mutating func handleCapturing(
        isSpeech: Bool, at: TimeInterval, hasHeardSpeech: Bool, lastSpeechAt: TimeInterval?
    ) -> CaptureOutcome {
        if !hasHeardSpeech {
            if at - startedAt >= config.noSpeechTimeout {
                return finish(.timedOutNoSpeech)
            }
            if isSpeech {
                phase = .capturing(hasHeardSpeech: true, lastSpeechAt: at)
            }
            return .continueCapturing
        }

        if isSpeech {
            phase = .capturing(hasHeardSpeech: true, lastSpeechAt: at)
            return .continueCapturing
        }

        if let last = lastSpeechAt, at - last >= config.silenceBeforeGrace {
            phase = .grace(enteredAt: at)
            return .enteredGraceWindow
        }
        return .continueCapturing
    }

    private mutating func finish(_ outcome: CaptureOutcome) -> CaptureOutcome {
        phase = .finished(outcome)
        return outcome
    }
}
