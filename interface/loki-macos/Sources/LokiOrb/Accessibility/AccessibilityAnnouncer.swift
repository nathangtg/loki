import AppKit
import Foundation

/// Posts state changes to assistive technology as accessibility announcements, so a VoiceOver user
/// hears progress, completion, errors and questions without looking at the screen.
///
/// Announcements are only posted while VoiceOver is running. Everything announced is also present as
/// text in the conversation or the status line.
@MainActor
final class AccessibilityAnnouncer {
    /// Minimum time between two announced steps. Faster steps are dropped, not queued.
    static let stepInterval: TimeInterval = 2.0

    private var lastStepText: String?
    private var lastStepDate: Date?

    var isVoiceOverRunning: Bool {
        NSWorkspace.shared.isVoiceOverEnabled
    }

    /// Call when a new command starts, so the same step text may be announced again.
    func beginJob() {
        lastStepText = nil
        lastStepDate = nil
    }

    func announce(_ text: String, priority: NSAccessibilityPriorityLevel = .medium) {
        let message = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !message.isEmpty, isVoiceOverRunning else { return }
        NSAccessibility.post(
            element: NSApp as Any,
            notification: .announcementRequested,
            userInfo: [
                .announcement: message,
                .priority: priority.rawValue,
            ]
        )
    }

    /// Announces an executing step, at most one per `stepInterval` and never the same text twice in a row.
    func announceStep(_ text: String, now: Date = Date()) {
        guard shouldAnnounceStep(text, now: now) else { return }
        announce(text, priority: .low)
    }

    /// Records the step and returns whether it should be announced.
    func shouldAnnounceStep(_ text: String, now: Date) -> Bool {
        let step = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !step.isEmpty, step != lastStepText else { return false }
        if let last = lastStepDate, now.timeIntervalSince(last) < Self.stepInterval { return false }
        lastStepText = step
        lastStepDate = now
        return true
    }
}
