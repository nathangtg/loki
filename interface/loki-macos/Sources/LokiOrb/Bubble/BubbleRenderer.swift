import SwiftUI

/// Draws the Liquid Aurora bubble. Pure function of (style, time, audio level, burst progress):
/// no stored animation state, so it can be re-targeted or re-sized at any moment.
///
/// Layer stack, back to front:
///  1. Outer halo (radial gradient, no blur pass)
///  2. Listening / awaiting ripples
///  3. Deformed body, clipped:
///       dark base -> three roaming gradient fields + accent field -> internal swirl ->
///       execution light sweep -> luminous core -> inner rim light -> depth shading -> specular sheen
///  4. Hairline rim
///  5. Completion burst ring
enum BubbleRenderer {

    private static let outlinePoints = 64

    static func draw(
        into context: inout GraphicsContext,
        size: CGSize,
        style: BubbleStyle,
        time t: Double,
        level rawLevel: Double,
        burst: Double?
    ) {
        let side = Double(min(size.width, size.height))
        guard side > 1 else { return }
        let center = CGPoint(x: Double(size.width) / 2, y: Double(size.height) / 2)
        let maxRadius = side / 2 - 0.5
        let baseRadius = side * style.ratio / 2

        let level = min(1, max(0, rawLevel)) * style.audioGain
        let breathing = style.pulse * sin(t * 1.1) + style.pulseFast * sin(t * 3.8 + 0.6)
        var burstBump = 0.0
        if let burst { burstBump = 0.06 * sin(burst * .pi) }
        let scale = 1 + breathing + level * 0.20 + style.swell + burstBump
        let radius = min(baseRadius * scale, maxRadius * 0.86)
        let luminosity = style.brightness * (1 + 0.05 * sin(t * 0.7 + 1.1))

        let rim = style.color(4)
        let field0 = style.color(0)
        let field1 = style.color(1)
        let field2 = style.color(2)
        let accent = style.color(3)

        drawHalo(&context, center: center, radius: radius, outer: maxRadius, style: style, rim: rim, field: field1, level: level)

        if style.ripple > 0.01 {
            drawRipples(&context, center: center, radius: radius, outer: maxRadius, style: style, rim: rim, level: level, t: t)
        }

        let body = bodyPath(center: center, radius: radius, energy: style.energy, level: level, t: t)

        // Opaque dark base: the body reads as a dimensional object, not a flat tint.
        context.fill(body, with: .color(LokiDesign.Palette.abyss.color))

        context.drawLayer { layer in
            layer.clip(to: body)

            // Soft depth gradient on the base so fields have something to glow against.
            layer.fill(
                Path(ellipseIn: CGRect(x: center.x - radius, y: center.y - radius, width: radius * 2, height: radius * 2)),
                with: .radialGradient(
                    Gradient(colors: [field0.color(opacity: 0.30 * luminosity), .clear]),
                    center: CGPoint(x: center.x, y: center.y + radius * 0.2),
                    startRadius: 0,
                    endRadius: radius * 1.1
                )
            )

            layer.blendMode = .screen

            // Gradient fields: Lissajous paths at unrelated frequencies create depth and parallax.
            let roam = radius * style.flow * 0.55
            let wobble = radius * 0.12 * style.energy
            let fields: [(LokiRGB, Double, Double, Double, Double, Double, Double)] = [
                // color, fx, fy, phase, size, alpha, wobble phase
                (field0, 0.71, 0.93, 0.0, 1.05, 0.88, 0.4),
                (field1, 0.97, 0.67, 2.1, 0.92, 0.80, 1.7),
                (field2, 0.59, 1.13, 4.2, 1.00, 0.84, 2.9),
                (accent, 1.21, 0.83, 1.3, 0.70, 0.78 * style.accentMix, 3.8),
            ]
            for f in fields where f.5 > 0.01 {
                let cx = center.x + roam * cos(t * f.1 + f.3) + wobble * sin(t * 2.3 + f.6)
                let cy = center.y + roam * sin(t * f.2 + f.3 * 0.7) + wobble * cos(t * 2.7 + f.6)
                let r = radius * f.4 * (1 + 0.12 * sin(t * 0.8 + f.3))
                layer.fill(
                    Path(ellipseIn: CGRect(x: cx - r, y: cy - r, width: r * 2, height: r * 2)),
                    with: .radialGradient(
                        Gradient(stops: [
                            .init(color: f.0.color(opacity: f.5 * luminosity), location: 0),
                            .init(color: f.0.color(opacity: f.5 * luminosity * 0.35), location: 0.55),
                            .init(color: f.0.color(opacity: 0), location: 1),
                        ]),
                        center: CGPoint(x: cx, y: cy),
                        startRadius: 0,
                        endRadius: r
                    )
                )
            }

            // Internal swirl: an off-center conic field with a non-uniform angular velocity.
            if style.swirl > 0.01 {
                let swirlCenter = CGPoint(
                    x: center.x + radius * 0.14 * cos(t * 0.5),
                    y: center.y + radius * 0.14 * sin(t * 0.63)
                )
                let angle = t * 0.55 + 1.2 * sin(t * 0.31)
                let a = 0.55 * style.swirl * luminosity
                layer.fill(
                    Path(ellipseIn: CGRect(x: center.x - radius, y: center.y - radius, width: radius * 2, height: radius * 2)),
                    with: .conicGradient(
                        Gradient(stops: [
                            .init(color: accent.color(opacity: 0), location: 0.0),
                            .init(color: accent.color(opacity: a), location: 0.18),
                            .init(color: field1.color(opacity: 0), location: 0.40),
                            .init(color: field2.color(opacity: a * 0.8), location: 0.62),
                            .init(color: field0.color(opacity: 0), location: 0.82),
                            .init(color: accent.color(opacity: 0), location: 1.0),
                        ]),
                        center: swirlCenter,
                        angle: .radians(angle)
                    )
                )
            }

            // Execution: a focused, directional light sweep.
            if style.drift > 0.01 {
                let phase = t * 0.55 - floor(t * 0.55)
                let travel = radius * (-1.5 + 3.0 * phase)
                let dir = CGPoint(x: cos(0.45), y: sin(0.45))
                let mid = CGPoint(x: center.x + dir.x * travel, y: center.y + dir.y * travel)
                let half = radius * 0.55
                let edge = sin(phase * .pi)
                layer.fill(
                    Path(CGRect(x: center.x - radius, y: center.y - radius, width: radius * 2, height: radius * 2)),
                    with: .linearGradient(
                        Gradient(stops: [
                            .init(color: .clear, location: 0),
                            .init(color: LokiDesign.Palette.cyan.color(opacity: 0.55 * style.drift * edge), location: 0.5),
                            .init(color: .clear, location: 1),
                        ]),
                        startPoint: CGPoint(x: mid.x - dir.x * half, y: mid.y - dir.y * half),
                        endPoint: CGPoint(x: mid.x + dir.x * half, y: mid.y + dir.y * half)
                    )
                )
            }

            // Luminous core.
            let coreRadius = radius * (0.58 + 0.06 * sin(t * 1.7) + 0.12 * level)
            let coreCenter = CGPoint(
                x: center.x + radius * 0.05 * style.flow * cos(t * 0.9),
                y: center.y + radius * 0.05 * style.flow * sin(t * 1.1)
            )
            layer.fill(
                Path(ellipseIn: CGRect(x: coreCenter.x - coreRadius, y: coreCenter.y - coreRadius, width: coreRadius * 2, height: coreRadius * 2)),
                with: .radialGradient(
                    Gradient(stops: [
                        .init(color: Color.white.opacity(min(1, style.core * 0.95)), location: 0),
                        .init(color: accent.color(opacity: style.core * 0.55), location: 0.35),
                        .init(color: accent.color(opacity: 0), location: 1),
                    ]),
                    center: coreCenter,
                    startRadius: 0,
                    endRadius: coreRadius
                )
            )

            layer.blendMode = .normal

            // Inner rim light (fresnel-like): the edge catches the state color.
            layer.fill(
                Path(ellipseIn: CGRect(x: center.x - radius * 1.2, y: center.y - radius * 1.2, width: radius * 2.4, height: radius * 2.4)),
                with: .radialGradient(
                    Gradient(stops: [
                        .init(color: rim.color(opacity: 0), location: 0.0),
                        .init(color: rim.color(opacity: 0), location: 0.78),
                        .init(color: rim.color(opacity: 0.42 * luminosity), location: 0.93),
                        .init(color: Color.white.opacity(0.10), location: 1.0),
                    ]),
                    center: center,
                    startRadius: 0,
                    endRadius: radius * 1.2
                )
            )

            // Depth: the lower edge falls into shadow.
            layer.fill(
                Path(CGRect(x: center.x - radius, y: center.y - radius, width: radius * 2, height: radius * 2)),
                with: .linearGradient(
                    Gradient(colors: [.clear, Color.black.opacity(0.0), Color.black.opacity(0.38)]),
                    startPoint: CGPoint(x: center.x, y: center.y - radius),
                    endPoint: CGPoint(x: center.x, y: center.y + radius)
                )
            )

            // Specular sheen: a soft, slowly drifting highlight in the upper left.
            let shift = radius * 0.03
            let sheenCenter = CGPoint(
                x: center.x - radius * 0.30 + shift * sin(t * 0.4),
                y: center.y - radius * 0.46 + shift * cos(t * 0.5)
            )
            var sheenLayer = layer
            sheenLayer.translateBy(x: sheenCenter.x, y: sheenCenter.y)
            sheenLayer.rotate(by: .degrees(-28))
            let sheenRect = CGRect(x: -radius * 0.50, y: -radius * 0.24, width: radius * 1.0, height: radius * 0.48)
            sheenLayer.fill(
                Path(ellipseIn: sheenRect),
                with: .linearGradient(
                    Gradient(colors: [Color.white.opacity(0.62 * style.sheen), Color.white.opacity(0.0)]),
                    startPoint: CGPoint(x: 0, y: sheenRect.minY),
                    endPoint: CGPoint(x: 0, y: sheenRect.maxY)
                )
            )
        }

        // Hairline rim: bright where the light hits, tinted by state elsewhere.
        context.stroke(
            body,
            with: .linearGradient(
                Gradient(colors: [Color.white.opacity(0.42), rim.color(opacity: 0.22), rim.color(opacity: 0.10)]),
                startPoint: CGPoint(x: center.x - radius, y: center.y - radius),
                endPoint: CGPoint(x: center.x + radius, y: center.y + radius)
            ),
            lineWidth: max(0.6, side * 0.006)
        )

        if let burst {
            drawBurst(&context, center: center, radius: radius, outer: maxRadius, progress: burst)
        }
    }

    // MARK: - Layers

    private static func drawHalo(
        _ context: inout GraphicsContext,
        center: CGPoint, radius: Double, outer: Double,
        style: BubbleStyle, rim: LokiRGB, field: LokiRGB, level: Double
    ) {
        let g = style.glow * (1 + 0.5 * level)
        let rect = CGRect(x: center.x - outer, y: center.y - outer, width: outer * 2, height: outer * 2)
        context.fill(
            Path(ellipseIn: rect),
            with: .radialGradient(
                Gradient(stops: [
                    .init(color: rim.color(opacity: min(0.9, g * 0.62)), location: 0),
                    .init(color: rim.color(opacity: min(0.6, g * 0.30)), location: 0.22),
                    .init(color: field.color(opacity: min(0.4, g * 0.10)), location: 0.58),
                    .init(color: field.color(opacity: 0), location: 1),
                ]),
                center: center,
                startRadius: radius * 0.8,
                endRadius: outer
            )
        )
    }

    private static func drawRipples(
        _ context: inout GraphicsContext,
        center: CGPoint, radius: Double, outer: Double,
        style: BubbleStyle, rim: LokiRGB, level: Double, t: Double
    ) {
        let reach = outer - radius
        // With real input the rings follow the voice; with no signal they stay faint and calm.
        let drive = 0.25 + 0.75 * min(1, level * 1.6)
        for i in 0..<3 {
            let p = (t * 0.42 + Double(i) / 3.0).truncatingRemainder(dividingBy: 1)
            let eased = 1 - pow(1 - p, 2)
            let r = radius * 1.04 + reach * eased * (0.55 + 0.45 * drive)
            let alpha = pow(1 - p, 2) * style.ripple * drive * 0.75
            guard alpha > 0.01, r < outer else { continue }
            context.stroke(
                Path(ellipseIn: CGRect(x: center.x - r, y: center.y - r, width: r * 2, height: r * 2)),
                with: .color(rim.color(opacity: alpha)),
                lineWidth: 1.0 + 1.0 * (1 - p)
            )
        }
    }

    private static func drawBurst(
        _ context: inout GraphicsContext,
        center: CGPoint, radius: Double, outer: Double, progress p: Double
    ) {
        let eased = 1 - pow(1 - p, 3)
        let r = radius * 1.05 + (outer - radius * 1.05) * eased
        let alpha = pow(1 - p, 1.5) * 0.8
        context.stroke(
            Path(ellipseIn: CGRect(x: center.x - r, y: center.y - r, width: r * 2, height: r * 2)),
            with: .color(LokiDesign.Palette.mint.color(opacity: alpha)),
            lineWidth: 0.8 + 2.0 * (1 - p)
        )
    }

    /// Circle with three low harmonics of smoothly varying phase. Amplitude is driven by `energy`
    /// (state) and, when real audio is present, by `level`.
    private static func bodyPath(center: CGPoint, radius: Double, energy: Double, level: Double, t: Double) -> Path {
        let n = outlinePoints
        var path = Path()
        let amp = radius * energy * 0.085
        let audioAmp = radius * level * 0.06
        for i in 0..<n {
            let theta = Double(i) / Double(n) * 2 * .pi
            let h1 = sin(2 * theta + t * 0.9) * 0.50
            let h2 = sin(3 * theta - t * 1.3 + 1.3) * 0.35
            let h3 = sin(5 * theta + t * 1.9 + 0.4) * 0.15
            let a = sin(4 * theta - t * 5.0) * audioAmp
            let r = radius + amp * (h1 + h2 + h3) + a
            let p = CGPoint(x: center.x + cos(theta) * r, y: center.y + sin(theta) * r)
            if i == 0 { path.move(to: p) } else { path.addLine(to: p) }
        }
        path.closeSubpath()
        return path
    }
}
