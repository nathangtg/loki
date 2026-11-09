import AppKit
import SwiftUI

/// The Loki app icon: the Liquid Aurora bubble on a dark rounded square, drawn with the same
/// renderer as the live bubble at a fixed moment. Used for `NSApp.applicationIconImage` and, via
/// `--export-icon`, to generate the iconset that `scripts/bundle.sh` turns into AppIcon.icns.
enum AppIcon {

    /// Draws the icon into a square of `side` points.
    private struct IconView: View {
        let side: CGFloat

        var body: some View {
            // macOS icon grid: the artwork fills about 80% of the canvas, leaving room for the shadow.
            let tile = side * 0.80
            let corner = tile * 0.225
            ZStack {
                RoundedRectangle(cornerRadius: corner, style: .continuous)
                    .fill(
                        LinearGradient(
                            colors: [
                                Color(red: 0.10, green: 0.12, blue: 0.27),
                                Color(red: 0.02, green: 0.03, blue: 0.09),
                            ],
                            startPoint: .top,
                            endPoint: .bottom
                        )
                    )
                    .frame(width: tile, height: tile)
                    .shadow(color: .black.opacity(0.35), radius: side * 0.012, x: 0, y: side * 0.010)
                RoundedRectangle(cornerRadius: corner, style: .continuous)
                    .strokeBorder(Color.white.opacity(0.14), lineWidth: max(1, side * 0.003))
                    .frame(width: tile, height: tile)
                Canvas { context, size in
                    var ctx = context
                    BubbleRenderer.draw(into: &ctx, size: size, style: bubbleStyle, time: 7.3, level: 0, burst: nil)
                }
                .frame(width: tile * 0.98, height: tile * 0.98)
            }
            .frame(width: side, height: side)
        }
    }

    /// A lively but calm look: the responding palette, with its deformation and motion turned down.
    private static var bubbleStyle: BubbleStyle {
        BubbleStyle.preset(for: .responding).adapted(mini: false, emphasis: 0).reducedMotion().withBodyRatio(0.62)
    }

    @MainActor
    static func cgImage(side: Int) -> CGImage? {
        let renderer = ImageRenderer(content: IconView(side: CGFloat(side)))
        renderer.scale = 1
        return renderer.cgImage
    }

    @MainActor
    static func image(side: Int = 512) -> NSImage? {
        guard let cg = cgImage(side: side * 2) else { return nil }
        return NSImage(cgImage: cg, size: NSSize(width: side, height: side))
    }

    /// Uses the Loki icon for the About panel and alerts when no bundle icon exists (`swift run`).
    @MainActor
    static func applyToApplication() {
        guard !AppBundle.isBundled, let image = image(side: 256) else { return }
        NSApp.applicationIconImage = image
    }

    /// Writes `icon_<size>x<size>[@2x].png` for the ten entries of a macOS iconset into `directory`.
    /// Returns false if any image could not be rendered or written.
    @MainActor
    static func exportIconset(to directory: URL) -> Bool {
        let entries: [(name: String, pixels: Int)] = [
            ("icon_16x16", 16), ("icon_16x16@2x", 32),
            ("icon_32x32", 32), ("icon_32x32@2x", 64),
            ("icon_128x128", 128), ("icon_128x128@2x", 256),
            ("icon_256x256", 256), ("icon_256x256@2x", 512),
            ("icon_512x512", 512), ("icon_512x512@2x", 1024),
        ]
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        } catch {
            return false
        }
        for entry in entries {
            guard let cg = cgImage(side: entry.pixels) else { return false }
            let rep = NSBitmapImageRep(cgImage: cg)
            guard let png = rep.representation(using: .png, properties: [:]) else { return false }
            do {
                try png.write(to: directory.appendingPathComponent(entry.name + ".png"))
            } catch {
                return false
            }
        }
        return true
    }
}
