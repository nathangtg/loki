import AppKit
import Combine
import SwiftUI

final class FloatingOrbPanel: NSPanel {
    var allowsKeyFocus: Bool = false

    init<Content: View>(contentRect: NSRect, rootView: Content) {
        super.init(
            contentRect: contentRect,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )

        self.isFloatingPanel = true
        self.level = .floating
        self.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        self.title = "Loki"
        // The Window menu offers "Bring Loki to Front"; a second entry for this panel adds nothing.
        self.isExcludedFromWindowsMenu = true
        self.setAccessibilityLabel("Loki assistant")
        self.backgroundColor = .clear
        self.isOpaque = false
        self.hasShadow = false
        self.isMovableByWindowBackground = true
        self.hidesOnDeactivate = false

        let hostingView = NSHostingView(rootView: rootView)
        hostingView.frame = contentRect
        hostingView.autoresizingMask = [.width, .height]
        self.contentView = hostingView
    }

    override var canBecomeKey: Bool {
        allowsKeyFocus
    }

    override var canBecomeMain: Bool {
        false
    }

    // MARK: - Placement

    /// Called (debounced) with the new position after the user drags the panel. Programmatic moves
    /// made by the methods below never report.
    var onUserMoved: ((PanelPosition) -> Void)?

    private var programmaticMoveDepth = 0
    private var expectedFrame: NSRect?
    private var moveGeneration = 0
    private var moveObserver: AnyCancellable?

    /// Starts reporting drags through `onUserMoved`.
    func observeUserMoves() {
        guard moveObserver == nil else { return }
        moveObserver = NotificationCenter.default.publisher(for: NSWindow.didMoveNotification, object: self)
            .sink { [weak self] _ in self?.noteMove() }
    }

    private func noteMove() {
        guard programmaticMoveDepth == 0 else { return }
        moveGeneration += 1
        let generation = moveGeneration
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: 300_000_000)
            guard let self, generation == self.moveGeneration else { return }
            self.reportUserMove()
        }
    }

    private func reportUserMove() {
        // A move the panel made itself can be delivered after the call returned.
        if let expected = expectedFrame {
            expectedFrame = nil
            if abs(expected.minX - frame.minX) < 0.5 && abs(expected.minY - frame.minY) < 0.5 { return }
        }
        guard let screen = currentScreen, let placement = screen.placementScreen else { return }
        onUserMoved?(PanelPlacement.position(for: frame, on: placement))
    }

    /// The screen holding most of the panel, else the main screen.
    private var currentScreen: NSScreen? { screen ?? NSScreen.main ?? NSScreen.screens.first }

    private func setFrameProgrammatically(_ rect: NSRect, animate: Bool = false) {
        programmaticMoveDepth += 1
        defer { programmaticMoveDepth -= 1 }
        expectedFrame = rect
        setFrame(rect, display: true, animate: animate)
    }

    /// Default position: horizontally centered above the bottom of the visible frame.
    func positionBottomCenter() {
        guard let visible = currentScreen?.visibleFrame else { return }
        let origin = PanelPlacement.bottomCenterOrigin(size: frame.size, in: visible)
        setFrameProgrammatically(NSRect(origin: origin, size: frame.size))
    }

    /// Restores the position saved by the user, when its screen is still attached; otherwise the
    /// default bottom-center position on the main screen.
    func restorePosition(_ saved: PanelPosition?) {
        guard let saved, let screen = NSScreen.screens.first(where: { $0.displayID == saved.screenID }) else {
            positionBottomCenter()
            return
        }
        let origin = PanelPlacement.origin(for: saved, size: frame.size, in: screen.visibleFrame)
        let target = NSRect(origin: origin, size: frame.size)
        setFrameProgrammatically(PanelPlacement.clamped(target, into: [screen.visibleFrame]))
    }

    /// Brings the panel back inside the visible frames of the attached screens. Called when
    /// displays are attached or removed, or when the Dock or menu bar changes the visible frame.
    func clampToScreens() {
        let rects = NSScreen.screens.map(\.visibleFrame)
        let target = PanelPlacement.clamped(frame, into: rects)
        guard target != frame else { return }
        setFrameProgrammatically(target)
    }

    /// Moves the panel to the same relative position on the screen holding `point`. Returns
    /// whether the panel moved.
    @discardableResult
    func moveToScreen(containing point: NSPoint) -> Bool {
        let screens = NSScreen.screens
        guard let index = PanelPlacement.index(containing: point, in: screens.map(\.frame)),
              let target = screens[index].placementScreen,
              let source = currentScreen?.placementScreen,
              source.id != target.id
        else { return false }
        let moved = PanelPlacement.translated(frame, from: source, to: target)
        guard moved != frame else { return false }
        setFrameProgrammatically(moved)
        return true
    }

    /// Resizes while keeping the bottom edge and horizontal center fixed, so the bubble stays put
    /// and the window grows upward. The result is kept on screen.
    func resizeAnchoredBottomCenter(to size: CGSize) {
        guard abs(frame.width - size.width) > 0.5 || abs(frame.height - size.height) > 0.5 else { return }
        let origin = NSPoint(x: frame.midX - size.width / 2, y: frame.minY)
        var target = NSRect(origin: origin, size: size)
        if let visible = currentScreen?.visibleFrame {
            target = PanelPlacement.clamped(target, into: [visible])
        }
        setFrameProgrammatically(target)
    }
}
