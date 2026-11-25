func runSmokeChecks() {
    let failure = forAll { rng, _ in
        let a = Int.random(in: -1000...1000, using: &rng)
        let b = Int.random(in: -1000...1000, using: &rng)
        return a + b == b + a ? nil : "addition not commutative for \(a), \(b)"
    }
    check("smoke: forAll runs", failure == nil, failure ?? "")

    var first = SplitMix64(seed: 42)
    var second = SplitMix64(seed: 42)
    let a = (0..<50).map { _ in first.next() }
    let b = (0..<50).map { _ in second.next() }
    check("same seed produces same sequence", a == b, "seed=42")

    var other = SplitMix64(seed: 43)
    let c = (0..<50).map { _ in other.next() }
    check("different seed produces different sequence", a != c, "seeds=42,43")
}
