import AppKit

/// Remembers the app that was frontmost before Loki took keyboard focus and hands focus back to it.
enum FocusReturn {
    /// The frontmost app, unless it is Loki itself.
    @MainActor
    static func captureFrontmost() -> NSRunningApplication? {
        let front = NSWorkspace.shared.frontmostApplication
        return front?.processIdentifier == ProcessInfo.processInfo.processIdentifier ? nil : front
    }

    /// Re-activates `previous` unless it quit or Loki is no longer the active app (the user already
    /// moved elsewhere).
    @MainActor
    static func restore(_ previous: NSRunningApplication?) {
        guard let previous, !previous.isTerminated, NSApp.isActive else { return }
        if #available(macOS 14.0, *) {
            NSApp.yieldActivation(to: previous)
            previous.activate()
        } else {
            previous.activate(options: [])
        }
    }
}
