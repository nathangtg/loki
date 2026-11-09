import AppKit

/// Where the user left the panel: the bottom-center point of the panel, relative to the visible
/// frame of the screen that held it. A fraction in x and points from the bottom in y keep the spot
/// meaningful when the resolution or the Dock changes.
struct PanelPosition: Equatable {
    var screenID: UInt32
    var fractionX: Double
    var offsetY: Double

    private enum Field {
        static let screen = "screen"
        static let fractionX = "fx"
        static let offsetY = "dy"
    }

    var dictionary: [String: Any] {
        [Field.screen: Int(screenID), Field.fractionX: fractionX, Field.offsetY: offsetY]
    }

    /// Rejects anything that is not a complete, finite stored position.
    init?(dictionary: [String: Any]) {
        guard let screen = dictionary[Field.screen] as? Int, screen >= 0, screen <= Int(UInt32.max),
              let fx = dictionary[Field.fractionX] as? Double, fx.isFinite,
              let dy = dictionary[Field.offsetY] as? Double, dy.isFinite
        else { return nil }
        screenID = UInt32(screen)
        fractionX = fx
        offsetY = dy
    }

    init(screenID: UInt32, fractionX: Double, offsetY: Double) {
        self.screenID = screenID
        self.fractionX = fractionX
        self.offsetY = offsetY
    }
}

/// A display as far as placement is concerned.
struct PlacementScreen: Equatable {
    var id: UInt32
    var visibleFrame: CGRect
}

/// Pure frame math for the floating panel, kept free of AppKit state so it can be checked with
/// plain rectangles (several fake screens, a missing screen, a shrunken visible frame).
enum PanelPlacement {
    /// Gap between the bubble's window and the bottom of the visible frame at the default position.
    static let bottomMargin: CGFloat = 28

    /// Origin that centers a panel of `size` horizontally at the default height in `visible`.
    static func bottomCenterOrigin(size: CGSize, in visible: CGRect) -> CGPoint {
        CGPoint(x: visible.midX - size.width / 2, y: visible.minY + bottomMargin)
    }

    /// The panel's bottom-center point, relative to `screen`.
    static func position(for frame: CGRect, on screen: PlacementScreen) -> PanelPosition {
        let visible = screen.visibleFrame
        let width = max(visible.width, 1)
        return PanelPosition(
            screenID: screen.id,
            fractionX: Double((frame.midX - visible.minX) / width),
            offsetY: Double(frame.minY - visible.minY)
        )
    }

    /// Origin of a panel of `size` at `position`, inside `visible`.
    static func origin(for position: PanelPosition, size: CGSize, in visible: CGRect) -> CGPoint {
        CGPoint(
            x: visible.minX + CGFloat(position.fractionX) * visible.width - size.width / 2,
            y: visible.minY + CGFloat(position.offsetY)
        )
    }

    /// Moves `frame` the least distance needed to lie inside one of `visibleFrames`: the one it
    /// overlaps most, or the nearest when it overlaps none. Returned unchanged when there are no
    /// screens at all.
    static func clamped(_ frame: CGRect, into visibleFrames: [CGRect]) -> CGRect {
        guard let target = bestRect(for: frame, in: visibleFrames) else { return frame }
        var origin = frame.origin
        origin.y = min(origin.y, target.maxY - frame.height)
        origin.y = max(origin.y, target.minY)
        origin.x = min(origin.x, target.maxX - frame.width)
        origin.x = max(origin.x, target.minX)
        return CGRect(origin: origin, size: frame.size)
    }

    private static func bestRect(for frame: CGRect, in rects: [CGRect]) -> CGRect? {
        var best: (rect: CGRect, overlap: CGFloat)?
        for rect in rects {
            let overlap = frame.intersection(rect)
            let area = overlap.isNull ? 0 : overlap.width * overlap.height
            if area > (best?.overlap ?? 0) { best = (rect, area) }
        }
        if let best { return best.rect }
        // No overlap with any screen: take the one whose center is closest.
        return rects.min { distance($0, frame) < distance($1, frame) }
    }

    private static func distance(_ a: CGRect, _ b: CGRect) -> CGFloat {
        let dx = a.midX - b.midX
        let dy = a.midY - b.midY
        return dx * dx + dy * dy
    }

    /// Index of the rectangle containing `point`, if any. Edges follow `NSMouseInRect`: the minimum
    /// edges belong to the rectangle and the maximum edges do not.
    static func index(containing point: CGPoint, in frames: [CGRect]) -> Int? {
        frames.firstIndex { point.x >= $0.minX && point.x < $0.maxX && point.y >= $0.minY && point.y < $0.maxY }
    }

    /// The same relative position on `target` as `frame` has on `source`, kept on `target`.
    static func translated(_ frame: CGRect, from source: PlacementScreen, to target: PlacementScreen) -> CGRect {
        let relative = position(for: frame, on: source)
        let origin = origin(for: relative, size: frame.size, in: target.visibleFrame)
        return clamped(CGRect(origin: origin, size: frame.size), into: [target.visibleFrame])
    }
}

extension NSScreen {
    /// The `NSScreenNumber` (CGDirectDisplayID) that identifies this display across launches.
    var displayID: UInt32? {
        (deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value
    }

    var placementScreen: PlacementScreen? {
        displayID.map { PlacementScreen(id: $0, visibleFrame: visibleFrame) }
    }
}
