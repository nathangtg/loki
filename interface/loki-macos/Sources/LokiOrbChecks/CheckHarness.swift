import Foundation

/// Deterministic SplitMix64 generator, so a failing run can be replayed from its seed.
struct SplitMix64: RandomNumberGenerator {
    private var state: UInt64

    init(seed: UInt64) {
        self.state = seed
    }

    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
}

/// Seed taken from `LOKI_PBT_SEED` when set (for replay), otherwise random.
func resolveSeed() -> UInt64 {
    if let raw = ProcessInfo.processInfo.environment["LOKI_PBT_SEED"],
        let seed = UInt64(raw)
    {
        return seed
    }
    return UInt64.random(in: UInt64.min...UInt64.max)
}

/// Failure messages recorded by `check`, reported by `main.swift`.
var recordedFailures: [String] = []

/// Records a failure (with `message`) when `condition` is false. Returns the condition.
@discardableResult
func check(_ name: String, _ condition: @autoclosure () -> Bool, _ message: String = "") -> Bool {
    if condition() {
        print("PASS \(name)")
        return true
    }
    let text = "FAIL \(name)\(message.isEmpty ? "" : ": \(message)")"
    print(text)
    recordedFailures.append(text)
    return false
}

/// Runs `body` `iterations` times with a seeded generator. The body returns nil on success
/// or a failure description. The seed is printed and included in the failure message.
/// Returns nil when all iterations pass, otherwise the failure message.
func forAll(
    iterations: Int = 100,
    seed: UInt64? = nil,
    _ body: (inout SplitMix64, Int) -> String?
) -> String? {
    let seed = seed ?? resolveSeed()
    print("forAll seed=\(seed) iterations=\(iterations) (replay with LOKI_PBT_SEED=\(seed))")
    var rng = SplitMix64(seed: seed)
    for i in 0..<iterations {
        if let failure = body(&rng, i) {
            return "Property failed at iteration \(i), seed=\(seed): \(failure)"
        }
    }
    return nil
}
