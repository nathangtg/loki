import AppKit
import Foundation

/// Keeps a second copy of Loki from starting: two copies would show two status items and the
/// second one's global hotkey registration would silently fail.
enum SingleInstanceGuard {
    private static var lockDescriptor: Int32 = -1

    /// True when another Loki process is already running. Otherwise this process takes the
    /// instance lock for its lifetime.
    static func anotherInstanceIsRunning() -> Bool {
        if otherInstanceInRunningApplications() { return true }
        return !acquireLock()
    }

    /// Bundled builds are matched by bundle identifier, unbundled builds (`swift run`) by
    /// executable name.
    private static func otherInstanceInRunningApplications() -> Bool {
        let me = ProcessInfo.processInfo.processIdentifier
        if let identifier = Bundle.main.bundleIdentifier {
            return NSRunningApplication.runningApplications(withBundleIdentifier: identifier)
                .contains { $0.processIdentifier != me }
        }
        let name = Bundle.main.executableURL?.lastPathComponent ?? ProcessInfo.processInfo.processName
        return NSWorkspace.shared.runningApplications.contains {
            $0.processIdentifier != me && $0.executableURL?.lastPathComponent == name
        }
    }

    /// An advisory file lock is released by the kernel when the process exits, so it cannot go
    /// stale, and it also covers launches that race each other or that the workspace list misses.
    private static func acquireLock() -> Bool {
        let path = (NSTemporaryDirectory() as NSString).appendingPathComponent("com.loki.LokiOrb.lock")
        let descriptor = open(path, O_CREAT | O_RDWR, 0o600)
        guard descriptor >= 0 else { return true }
        if flock(descriptor, LOCK_EX | LOCK_NB) != 0 {
            close(descriptor)
            return false
        }
        lockDescriptor = descriptor
        return true
    }
}
