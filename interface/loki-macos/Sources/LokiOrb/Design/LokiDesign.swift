import AppKit
import SwiftUI

/// An sRGB triple. Plain numbers (rather than `Color`) so the bubble style can interpolate
/// colors as part of one animatable vector (see `BubbleStyle`).
struct LokiRGB: Equatable {
    var r: Double
    var g: Double
    var b: Double

    init(_ r: Double, _ g: Double, _ b: Double) {
        self.r = r
        self.g = g
        self.b = b
    }

    var color: Color {
        Color(.sRGB, red: Self.clamp(r), green: Self.clamp(g), blue: Self.clamp(b), opacity: 1)
    }

    func color(opacity: Double) -> Color {
        Color(.sRGB, red: Self.clamp(r), green: Self.clamp(g), blue: Self.clamp(b), opacity: Self.clamp(opacity))
    }

    private static func clamp(_ v: Double) -> Double { min(1, max(0, v)) }

    func scaled(_ factor: Double) -> LokiRGB {
        LokiRGB(Self.clamp(r * factor), Self.clamp(g * factor), Self.clamp(b * factor))
    }

    func mixed(towards level: Double, by amount: Double) -> LokiRGB {
        LokiRGB(r + (level - r) * amount, g + (level - g) * amount, b + (level - b) * amount)
    }
}

/// One color and opacity in sRGB, used to describe each appearance variant of a token.
struct LokiTone {
    var r: Double
    var g: Double
    var b: Double
    var a: Double

    static func white(_ a: Double) -> LokiTone { LokiTone(r: 1, g: 1, b: 1, a: a) }
    static func black(_ a: Double) -> LokiTone { LokiTone(r: 0, g: 0, b: 0, a: a) }
    static func rgb(_ c: LokiRGB, _ a: Double) -> LokiTone { LokiTone(r: c.r, g: c.g, b: c.b, a: a) }

    var nsColor: NSColor { NSColor(srgbRed: r, green: g, blue: b, alpha: a) }
}

extension NSAppearance {
    enum LokiVariant {
        case dark, light, darkContrast, lightContrast
    }

    /// Which of the four looks this appearance asks for. Unknown appearances use the dark look.
    var lokiVariant: LokiVariant {
        let match = bestMatch(from: [
            .accessibilityHighContrastDarkAqua, .accessibilityHighContrastAqua, .darkAqua, .aqua,
        ])
        switch match {
        case .some(.aqua): return .light
        case .some(.accessibilityHighContrastAqua): return .lightContrast
        case .some(.accessibilityHighContrastDarkAqua): return .darkContrast
        default: return .dark
        }
    }
}

extension LokiDesign {
    /// A color that resolves against the effective appearance each time it is drawn. The contrast
    /// variants fall back to the plain Dark and Light tones when not given.
    static func adaptive(
        dark: LokiTone, light: LokiTone,
        darkContrast: LokiTone? = nil, lightContrast: LokiTone? = nil
    ) -> Color {
        let darkHC = darkContrast ?? dark
        let lightHC = lightContrast ?? light
        return Color(nsColor: NSColor(name: nil) { appearance in
            switch appearance.lokiVariant {
            case .dark: return dark.nsColor
            case .light: return light.nsColor
            case .darkContrast: return darkHC.nsColor
            case .lightContrast: return lightHC.nsColor
            }
        })
    }
}

/// LOKI "Liquid Aurora" design tokens: one place for color, type, spacing, radii, motion and sizes.
enum LokiDesign {

    // MARK: Palette

    enum Palette {
        static let electricBlue = LokiRGB(0.14, 0.42, 1.00)
        static let cyan = LokiRGB(0.10, 0.84, 0.98)
        static let indigo = LokiRGB(0.30, 0.27, 0.90)
        static let violet = LokiRGB(0.62, 0.33, 0.98)
        static let magenta = LokiRGB(0.96, 0.32, 0.76)
        static let mint = LokiRGB(0.36, 1.00, 0.80)
        static let ember = LokiRGB(1.00, 0.42, 0.33)
        static let abyss = LokiRGB(0.03, 0.04, 0.11)
        static let paleBlue = LokiRGB(0.62, 0.70, 1.00)
    }

    // MARK: Interface colors

    /// Appearance-aware tokens. Each one resolves per draw from the view's effective appearance, so a
    /// single token covers Dark, Light and their Increase Contrast variants. The bubble itself
    /// (`Palette`) floats over arbitrary desktop content and does not use these.
    enum Colors {
        static let textPrimary = adaptive(
            dark: .white(0.94), light: .black(0.86),
            darkContrast: .white(1.0), lightContrast: .black(1.0))
        static let textSecondary = adaptive(
            dark: .white(0.64), light: .black(0.62),
            darkContrast: .white(0.88), lightContrast: .black(0.82))
        static let textTertiary = adaptive(
            dark: .white(0.46), light: .black(0.54),
            darkContrast: .white(0.76), lightContrast: .black(0.74))
        static let hairline = adaptive(
            dark: .white(0.09), light: .black(0.10),
            darkContrast: .white(0.50), lightContrast: .black(0.50))
        static let surface = adaptive(
            dark: .white(0.055), light: .black(0.05),
            darkContrast: .white(0.14), lightContrast: .black(0.10))
        static let surfaceRaised = adaptive(
            dark: .white(0.10), light: .black(0.09),
            darkContrast: .white(0.24), lightContrast: .black(0.18))
        static let userMessage = adaptive(
            dark: .rgb(Palette.electricBlue, 0.30), light: .rgb(Palette.electricBlue, 0.15),
            darkContrast: .rgb(Palette.electricBlue, 0.50), lightContrast: .rgb(Palette.electricBlue, 0.22))
        static let userMessageStroke = adaptive(
            dark: .rgb(Palette.electricBlue, 0.35), light: .rgb(Palette.electricBlue, 0.45),
            darkContrast: .rgb(Palette.paleBlue, 0.90), lightContrast: .rgb(Palette.electricBlue, 0.95))
        static let errorTint = adaptive(
            dark: .rgb(Palette.ember, 0.14), light: .rgb(Palette.ember, 0.12),
            darkContrast: .rgb(Palette.ember, 0.24), lightContrast: .rgb(Palette.ember, 0.20))
        static let errorStroke = adaptive(
            dark: .rgb(Palette.ember, 0.38), light: .rgb(LokiRGB(0.78, 0.22, 0.16), 0.60),
            darkContrast: .rgb(Palette.ember, 1.0), lightContrast: .rgb(LokiRGB(0.66, 0.12, 0.08), 1.0))
        static let codeSurface = adaptive(
            dark: .black(0.34), light: .black(0.06),
            darkContrast: .black(0.60), lightContrast: .black(0.10))

        /// Fill behind the caption capsule and the input field (drawn over a material).
        static let captionFill = adaptive(
            dark: .black(0.25), light: .white(0.30),
            darkContrast: .black(0.55), lightContrast: .white(0.70))
        static func inputFill(focused: Bool) -> Color {
            focused
                ? adaptive(dark: .black(0.34), light: .white(0.55), darkContrast: .black(0.60), lightContrast: .white(0.85))
                : adaptive(dark: .black(0.26), light: .white(0.38), darkContrast: .black(0.50), lightContrast: .white(0.70))
        }
        /// Behind the mic button while listening. White glyphs stay readable on it in every appearance.
        static let listeningFill = adaptive(
            dark: .rgb(Palette.cyan, 0.55), light: .rgb(LokiRGB(0.0, 0.46, 0.58), 0.95),
            darkContrast: .rgb(LokiRGB(0.0, 0.50, 0.62), 1.0), lightContrast: .rgb(LokiRGB(0.0, 0.34, 0.44), 1.0))

        /// Outline of the card; gradient in the standard look, a solid line with Increase Contrast.
        static let cardStrokeLeading = adaptive(dark: .white(0.22), light: .black(0.16))
        static let cardStrokeMiddle = adaptive(dark: .white(0.05), light: .black(0.05))
        static let cardStrokeSolid = adaptive(dark: .white(0.70), light: .black(0.65))
        static let shadow = adaptive(dark: .black(0.38), light: .black(0.20))
        /// Light wash over the material in the Light appearance; the dark appearance uses `Palette.abyss`.
        static let cardWash = adaptive(dark: .rgb(Palette.abyss, 0.62), light: .white(0.30))

        /// A palette hue as a foreground tint. The bright dark-mode hues (cyan, mint) are darkened on
        /// light surfaces, where they would otherwise nearly vanish, and lightened for high contrast.
        static func tint(_ rgb: LokiRGB, opacity: Double = 1) -> Color {
            adaptive(
                dark: .rgb(rgb, opacity),
                light: .rgb(rgb.scaled(0.62), opacity),
                darkContrast: .rgb(rgb.mixed(towards: 1, by: 0.25), opacity),
                lightContrast: .rgb(rgb.scaled(0.48), opacity))
        }

        static let accentGradient = LinearGradient(
            colors: [Palette.electricBlue.color, Palette.violet.color],
            startPoint: .top,
            endPoint: .bottom
        )
        static let focusGradient = LinearGradient(
            colors: [Palette.cyan.color, Palette.electricBlue.color, Palette.violet.color, Palette.magenta.color.opacity(0.8)],
            startPoint: .leading,
            endPoint: .trailing
        )
        /// Focus ring for the input field in Light and for Increase Contrast, where the pastel
        /// gradient has too little contrast against the surface.
        static let focusSolid = adaptive(
            dark: .rgb(Palette.cyan, 1.0), light: .rgb(LokiRGB(0.0, 0.40, 0.90), 1.0),
            darkContrast: .white(1.0), lightContrast: .black(1.0))
    }

    // MARK: Typography

    /// System text styles (`.body` 13 pt, `.subheadline` 11 pt, ...) so type matches the rest of macOS.
    enum Typography {
        static let wordmark = Font.system(.callout, design: .rounded).weight(.semibold)
        static let status = Font.subheadline.weight(.medium)
        static let body = Font.body
        static let input = Font.body
        static let heading = Font.system(.title3).weight(.semibold)
        static let caption = Font.subheadline
        static let captionStrong = Font.subheadline.weight(.medium)
        static let label = Font.subheadline.weight(.medium)
        static let code = Font.system(.callout, design: .monospaced)
        static let emptyTitle = Font.system(.title2, design: .rounded).weight(.semibold)
        /// Glyphs that sit next to text or inside small controls.
        static let symbolTiny = Font.caption2.weight(.bold)
        static let symbolSmall = Font.subheadline.weight(.semibold)
        static let symbolControl = Font.callout.weight(.semibold)
        static let symbolGear = Font.body
    }

    // MARK: Spacing and radii

    enum Spacing {
        static let xs: CGFloat = 4
        static let s: CGFloat = 8
        static let m: CGFloat = 12
        static let l: CGFloat = 16
        static let xl: CGFloat = 20
    }

    enum Radius {
        static let card: CGFloat = 26
        static let input: CGFloat = 19
        static let message: CGFloat = 15
        static let code: CGFloat = 10
    }

    // MARK: Motion

    enum Motion {
        /// Color / energy morphs between assistant states. Critically damped so colors never overshoot.
        static let stateChange = Animation.spring(response: 0.9, dampingFraction: 1.0)
        static let reduced = Animation.easeInOut(duration: 0.25)
        static let expand = Animation.spring(response: 0.52, dampingFraction: 0.82)
        static let collapse = Animation.spring(response: 0.42, dampingFraction: 0.9)
        static let message = Animation.spring(response: 0.38, dampingFraction: 0.86)
        static let focus = Animation.easeOut(duration: 0.18)

        static let panelCollapseDelay: TimeInterval = 0.5
        static let completedHold: TimeInterval = 1.6
        static let errorHold: TimeInterval = 5.0
        static let burstDuration: TimeInterval = 1.1

        static var reduceMotion: Bool {
            NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        }

        static func window(expanding: Bool) -> Animation {
            if reduceMotion { return reduced }
            return expanding ? expand : collapse
        }
    }

    // MARK: Bubble

    enum Bubble {
        /// Square canvas the full-size bubble draws into; the body occupies `bodyRatioFull` of it,
        /// the rest is halo and ripple space.
        static let compactFrame: CGFloat = 128
        static let miniFrame: CGFloat = 44
        static let bodyRatioFull: CGFloat = 0.5
        static let bodyRatioMini: CGFloat = 0.8
        /// Idle is slow, soft motion, so a low frame rate keeps the always-on cost small.
        static let idleInterval: TimeInterval = 1.0 / 24.0
        static let activeInterval: TimeInterval = 1.0 / 60.0
    }

    // MARK: Windows

    enum Window {
        static let compact = CGSize(width: 320, height: 176)
        static let expanded = CGSize(width: 420, height: 580)
        /// Transparent margin around the expanded card so its shadow is not clipped.
        static let cardInset: CGFloat = 20
        static let compactBottomPadding: CGFloat = 8
    }
}
