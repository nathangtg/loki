import Foundation

/// Pure RMS energy gate for the wake-phrase listener.
public enum WakeEnergyGate {
    /// Default RMS level a buffer must reach to be forwarded to the recognizer.
    public static let defaultThreshold: Float = 0.02

    /// Returns `true` (forward to the recognizer) iff `level >= threshold`; otherwise the buffer is dropped.
    /// A NaN level is never forwarded.
    public static func shouldForward(level: Float, threshold: Float = defaultThreshold) -> Bool {
        level >= threshold
    }
}
