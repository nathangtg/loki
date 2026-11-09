import SwiftUI

/// Every continuous parameter that distinguishes one assistant state's look from another,
/// packed into one fixed-size vector so SwiftUI can spring-interpolate *all* of them at once.
///
/// Time-based motion (orbits, ripples, deformation) uses fixed frequencies and is never rescaled
/// per state. Only amplitudes, colors and intensities are interpolated, so a state change can
/// never cause a phase jump or an animation reset, and an in-flight transition can be retargeted
/// by the next state at any moment.
struct BubbleStyle: VectorArithmetic, Equatable {
    var v = SIMD32<Double>(repeating: 0)

    // MARK: Slots

    private static let colorBase = 0           // five RGB triples: 0..<15
    private static let energy = 15
    private static let flow = 16
    private static let glow = 17
    private static let core = 18
    private static let ripple = 19
    private static let swirl = 20
    private static let sheen = 21
    private static let drift = 22
    private static let pulse = 23
    private static let brightness = 24
    private static let audioGain = 25
    private static let bodyRatio = 26
    private static let pulseFast = 27
    private static let accentMix = 28
    private static let swell = 29

    // MARK: Colors (field0, field1, field2, accent, rim)

    func color(_ index: Int) -> LokiRGB {
        let i = Self.colorBase + index * 3
        return LokiRGB(v[i], v[i + 1], v[i + 2])
    }

    private mutating func setColor(_ index: Int, _ c: LokiRGB) {
        let i = Self.colorBase + index * 3
        v[i] = c.r
        v[i + 1] = c.g
        v[i + 2] = c.b
    }

    // MARK: Scalars

    /// Amount of organic outline deformation.
    var energy: Double { v[Self.energy] }
    /// How far the gradient fields roam inside the body.
    var flow: Double { v[Self.flow] }
    /// Outer aura intensity.
    var glow: Double { v[Self.glow] }
    /// Luminous central core intensity.
    var core: Double { v[Self.core] }
    /// Strength of the expanding rings (listening / awaiting).
    var ripple: Double { v[Self.ripple] }
    /// Internal swirl intensity.
    var swirl: Double { v[Self.swirl] }
    /// Specular highlight intensity.
    var sheen: Double { v[Self.sheen] }
    /// Directional light sweep (execution).
    var drift: Double { v[Self.drift] }
    /// Slow breathing depth.
    var pulse: Double { v[Self.pulse] }
    var brightness: Double { v[Self.brightness] }
    /// How strongly the real audio level scales and deforms the body.
    var audioGain: Double { v[Self.audioGain] }
    /// Body diameter as a fraction of the drawing square (the rest is aura space).
    var ratio: Double { v[Self.bodyRatio] }
    /// Faster, shallower pulse used for "controlled pulses" during execution.
    var pulseFast: Double { v[Self.pulseFast] }
    /// Strength of the fourth (accent) gradient field. Magenta accents only appear when responding.
    var accentMix: Double { v[Self.accentMix] }
    /// Transient scale swell, driven by `BubbleModel.emphasis`.
    var swell: Double { v[Self.swell] }

    // MARK: VectorArithmetic

    static var zero: BubbleStyle { BubbleStyle() }

    static func += (lhs: inout BubbleStyle, rhs: BubbleStyle) { lhs.v += rhs.v }
    static func -= (lhs: inout BubbleStyle, rhs: BubbleStyle) { lhs.v -= rhs.v }
    static func + (lhs: BubbleStyle, rhs: BubbleStyle) -> BubbleStyle { BubbleStyle(v: lhs.v + rhs.v) }
    static func - (lhs: BubbleStyle, rhs: BubbleStyle) -> BubbleStyle { BubbleStyle(v: lhs.v - rhs.v) }

    mutating func scale(by rhs: Double) { v *= rhs }

    var magnitudeSquared: Double { (v * v).sum() }
}

// MARK: - Presets

extension BubbleStyle {

    /// The look of each assistant state. Tuned so the bubble stays recognizable across all of them:
    /// same dark base, same core, same highlight; only hue, energy and light change.
    static func preset(for phase: AssistantPhase) -> BubbleStyle {
        typealias P = LokiDesign.Palette
        switch phase {
        case .idle:
            return make(
                fields: (P.indigo, P.electricBlue, P.violet), accent: P.paleBlue, rim: P.electricBlue,
                energy: 0.16, flow: 0.35, glow: 0.34, core: 0.22, ripple: 0, swirl: 0.08, sheen: 0.75,
                drift: 0, pulse: 0.035, pulseFast: 0, brightness: 0.80, audioGain: 0, accentMix: 0.25)

        case .listening:
            return make(
                fields: (P.electricBlue, P.cyan, P.indigo), accent: P.cyan, rim: P.cyan,
                energy: 0.28, flow: 0.50, glow: 0.62, core: 0.45, ripple: 1, swirl: 0.12, sheen: 0.90,
                drift: 0, pulse: 0.03, pulseFast: 0, brightness: 1.0, audioGain: 1, accentMix: 0.30)

        case .thinking:
            return make(
                fields: (P.indigo, P.violet, P.electricBlue), accent: P.violet, rim: P.violet,
                energy: 0.50, flow: 0.85, glow: 0.50, core: 0.80, ripple: 0, swirl: 1.0, sheen: 0.70,
                drift: 0, pulse: 0.05, pulseFast: 0.02, brightness: 0.92, audioGain: 0, accentMix: 0.45)

        case .executing:
            return make(
                fields: (P.electricBlue, P.cyan, P.indigo), accent: P.cyan, rim: P.cyan,
                energy: 0.20, flow: 0.50, glow: 0.66, core: 0.70, ripple: 0, swirl: 0.15, sheen: 0.85,
                drift: 1, pulse: 0.02, pulseFast: 0.035, brightness: 1.0, audioGain: 0, accentMix: 0.35)

        case .responding:
            return make(
                fields: (P.electricBlue, P.cyan, P.violet), accent: P.magenta, rim: P.violet,
                energy: 0.80, flow: 1.0, glow: 0.88, core: 0.66, ripple: 0.22, swirl: 0.55, sheen: 1.0,
                drift: 0, pulse: 0.06, pulseFast: 0.02, brightness: 1.02, audioGain: 0.55, accentMix: 1.0)

        case .awaitingReply:
            return make(
                fields: (P.indigo, P.cyan, P.electricBlue), accent: P.paleBlue, rim: P.cyan,
                energy: 0.20, flow: 0.45, glow: 0.50, core: 0.40, ripple: 0.55, swirl: 0.10, sheen: 0.85,
                drift: 0, pulse: 0.06, pulseFast: 0, brightness: 0.95, audioGain: 0, accentMix: 0.20)

        case .completed:
            return make(
                fields: (P.cyan, P.mint, P.electricBlue), accent: P.mint, rim: P.mint,
                energy: 0.15, flow: 0.40, glow: 0.80, core: 0.95, ripple: 0, swirl: 0.05, sheen: 1.0,
                drift: 0, pulse: 0.04, pulseFast: 0, brightness: 1.05, audioGain: 0, accentMix: 0.30)

        case .error:
            return make(
                fields: (LokiRGB(0.90, 0.36, 0.34), P.violet, P.indigo), accent: P.ember, rim: P.ember,
                energy: 0.12, flow: 0.30, glow: 0.45, core: 0.35, ripple: 0, swirl: 0.05, sheen: 0.60,
                drift: 0, pulse: 0.03, pulseFast: 0, brightness: 0.82, audioGain: 0, accentMix: 0.40)
        }
    }

    /// Adapts a phase preset to where the bubble is shown and to transient emphasis.
    func adapted(mini: Bool, emphasis: Double) -> BubbleStyle {
        var s = self
        s.v[Self.bodyRatio] = mini ? LokiDesign.Bubble.bodyRatioMini : LokiDesign.Bubble.bodyRatioFull
        if mini {
            s.v[Self.ripple] = 0
            s.v[Self.glow] *= 0.8
        }
        s.v[Self.glow] += 0.30 * emphasis
        s.v[Self.core] += 0.20 * emphasis
        s.v[Self.swell] = 0.10 * emphasis
        return s
    }

    /// The same look with a different body size (a fraction of the drawing square).
    func withBodyRatio(_ ratio: Double) -> BubbleStyle {
        var s = self
        s.v[Self.bodyRatio] = ratio
        return s
    }

    /// Calmer variant for Reduce Motion: far less deformation and roaming, same colors and state cues.
    func reducedMotion() -> BubbleStyle {
        var s = self
        s.v[Self.energy] *= 0.25
        s.v[Self.flow] *= 0.4
        s.v[Self.swirl] *= 0.4
        s.v[Self.pulse] = 0
        s.v[Self.pulseFast] = 0
        s.v[Self.drift] *= 0.5
        return s
    }

    private static func make(
        fields: (LokiRGB, LokiRGB, LokiRGB), accent: LokiRGB, rim: LokiRGB,
        energy: Double, flow: Double, glow: Double, core: Double, ripple: Double, swirl: Double,
        sheen: Double, drift: Double, pulse: Double, pulseFast: Double, brightness: Double,
        audioGain: Double, accentMix: Double
    ) -> BubbleStyle {
        var s = BubbleStyle()
        s.setColor(0, fields.0)
        s.setColor(1, fields.1)
        s.setColor(2, fields.2)
        s.setColor(3, accent)
        s.setColor(4, rim)
        s.v[Self.energy] = energy
        s.v[Self.flow] = flow
        s.v[Self.glow] = glow
        s.v[Self.core] = core
        s.v[Self.ripple] = ripple
        s.v[Self.swirl] = swirl
        s.v[Self.sheen] = sheen
        s.v[Self.drift] = drift
        s.v[Self.pulse] = pulse
        s.v[Self.pulseFast] = pulseFast
        s.v[Self.brightness] = brightness
        s.v[Self.audioGain] = audioGain
        s.v[Self.accentMix] = accentMix
        s.v[Self.bodyRatio] = LokiDesign.Bubble.bodyRatioFull
        return s
    }
}
