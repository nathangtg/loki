import AppKit
import Combine
import SwiftUI

@main
struct LokiOrbApp {
    static func main() {
        // Build-time helper: `LokiOrb --export-icon <dir>` writes the iconset PNGs and exits.
        if let index = CommandLine.arguments.firstIndex(of: "--export-icon") {
            let paths = CommandLine.arguments.dropFirst(index + 1)
            guard let path = paths.first else {
                FileHandle.standardError.write(Data("usage: LokiOrb --export-icon <directory>\n".utf8))
                exit(2)
            }
            let ok = MainActor.assumeIsolated {
                AppIcon.exportIconset(to: URL(fileURLWithPath: path, isDirectory: true))
            }
            exit(ok ? 0 : 1)
        }

        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var panel: FloatingOrbPanel!
    private var statusItemController: StatusItemController?
    private let controller = LokiController()
    private lazy var commands = AppCommands(controller: controller, settings: settings)
    private let hotkeys = HotkeyManager()
    private let settings = LokiSettings.shared
    private lazy var settingsWindow = SettingsWindowController(
        settings: settings, hotkeys: hotkeys, serverAddress: controller.serverAddress
    )
    private var cancellables = Set<AnyCancellable>()
    private var servicesProvider: ServicesProvider?

    private var resizeGeneration = 0
    private var screenAsleep = false

    /// Whether the floating panel is shown. Kept here rather than derived from the window; the
    /// explicit Show/Hide Bubble menu choice is persisted by `AppCommands` in `LokiSettings`.
    private(set) var isBubbleVisible = true

    /// The app that was frontmost before the conversation took keyboard focus, and whether the
    /// conversation currently holds that focus.
    private var previousApp: NSRunningApplication?
    private var holdsKeyFocus = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        if SingleInstanceGuard.anotherInstanceIsRunning() {
            FileHandle.standardError.write(Data("Loki is already running; exiting this instance.\n".utf8))
            exit(0)
        }

        AppIcon.applyToApplication()

        let rootView = AssistantRootView(controller: controller)
        let rect = NSRect(origin: .zero, size: LokiDesign.Window.compact)
        panel = FloatingOrbPanel(contentRect: rect, rootView: rootView)
        panel.restorePosition(settings.panelPosition)
        panel.onUserMoved = { [weak self] position in self?.settings.panelPosition = position }
        panel.observeUserMoves()
        isBubbleVisible = settings.showBubbleAtLaunch
        if isBubbleVisible {
            panel.orderFrontRegardless()
        }

        commands.isOtherWindowKey = { [weak self] in
            guard let self, let key = NSApp.keyWindow else { return false }
            return key !== self.panel
        }
        commands.isBubbleVisible = { [weak self] in self?.isBubbleVisible ?? true }
        commands.setBubbleVisible = { [weak self] visible in self?.setBubbleVisible(visible) }

        controller.onFocusRequest = { [weak self] needsFocus in
            self?.handleFocusRequest(needsFocus)
        }
        controller.onResetPosition = { [weak self] in
            self?.resetPosition()
        }
        controller.onShowSettings = { [weak self] in
            self?.showSettings()
        }

        hotkeys.onToggleVoice = { [weak self] in
            guard let self else { return }
            // Stopping a recording must not move the panel; starting one brings it to the user.
            if self.controller.phase != .listening { self.summonToActiveDisplay() }
            self.controller.toggleVoice()
        }
        hotkeys.onToggleTextBar = { [weak self] in
            guard let self else { return }
            // Only opening summons; closing the conversation leaves the panel where it is.
            if !self.controller.isExpanded || !self.isBubbleVisible { self.summonToActiveDisplay() }
            self.commands.toggleConversation(nil)
        }
        hotkeys.registerHotkeys()

        MainMenu.install(commands: commands)
        registerServices()
        observeAppearance()
        observeWindowSize()
        observeScreenChanges()
        observeRenderingVisibility()
        statusItemController = StatusItemController(controller: controller, commands: commands)
        updateRendering()
    }

    // MARK: - Services

    /// "Ask Loki" in the Services menu: puts the selected text into the input, without sending it.
    private func registerServices() {
        let provider = ServicesProvider { [weak self] text in
            guard let self else { return }
            self.summonToActiveDisplay()
            self.controller.appendToInput(text)
        }
        provider.register()
        servicesProvider = provider
    }

    // MARK: - Settings

    func showSettings() {
        settingsWindow.show()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    // MARK: - Focus

    private func handleFocusRequest(_ needsFocus: Bool) {
        panel.allowsKeyFocus = needsFocus
        if needsFocus {
            // A follow-up question can arrive while the bubble is hidden; it must be seen.
            if !isBubbleVisible { setBubbleVisible(true) }
            // Re-capture on every request: the conversation can stay open while the user moves to
            // another app, and Ask Loki, a drop or Focus Input then arrive from that app. When Loki
            // itself is frontmost there is nothing newer to record, so the earlier app is kept.
            let frontmost = FocusReturn.captureFrontmost()
            if !holdsKeyFocus {
                holdsKeyFocus = true
                previousApp = frontmost
            } else if let frontmost {
                previousApp = frontmost
            }
            NSApp.activate(ignoringOtherApps: true)
            panel.makeKeyAndOrderFront(nil)
        } else {
            panel.resignKey()
            releaseKeyFocus()
        }
    }

    /// Hands keyboard focus back to the app the user was in, unless they already moved elsewhere.
    private func releaseKeyFocus() {
        guard holdsKeyFocus else { return }
        holdsKeyFocus = false
        let previous = previousApp
        previousApp = nil
        FocusReturn.restore(previous)
    }

    // MARK: - Visibility

    func setBubbleVisible(_ visible: Bool) {
        guard visible != isBubbleVisible else {
            if visible { panel.orderFrontRegardless() }
            return
        }
        isBubbleVisible = visible
        if visible {
            panel.orderFrontRegardless()
        } else {
            if controller.isExpanded { controller.dismissExpanded() }
            panel.orderOut(nil)
        }
        updateRendering()
    }

    // MARK: - Appearance

    /// Applies the Appearance setting to the panel (nil follows the system), and re-applies it when
    /// Increase Contrast changes, because a forced appearance needs its high-contrast variant chosen.
    private func observeAppearance() {
        applyAppearance(settings.appearance)
        settings.$appearance
            .dropFirst()
            .receive(on: DispatchQueue.main)
            .sink { [weak self] appearance in self?.applyAppearance(appearance) }
            .store(in: &cancellables)
        NSWorkspace.shared.notificationCenter
            .publisher(for: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in
                guard let self else { return }
                self.applyAppearance(self.settings.appearance)
            }
            .store(in: &cancellables)
    }

    private func applyAppearance(_ appearance: LokiAppearance) {
        let increaseContrast = NSWorkspace.shared.accessibilityDisplayShouldIncreaseContrast
        panel.appearance = appearance.nsAppearance(increaseContrast: increaseContrast)
    }

    // MARK: - Window sizing

    /// Expanding grows the (transparent) window first so SwiftUI has room to animate the card in.
    /// Collapsing waits for the SwiftUI animation to finish before shrinking the window.
    private func observeWindowSize() {
        controller.$isExpanded
            .removeDuplicates()
            .sink { [weak self] expanded in
                self?.applyWindowSize(expanded: expanded)
            }
            .store(in: &cancellables)
    }

    private func applyWindowSize(expanded: Bool) {
        resizeGeneration += 1
        let generation = resizeGeneration

        if expanded {
            panel.resizeAnchoredBottomCenter(to: LokiDesign.Window.expanded)
            return
        }

        let delay = LokiDesign.Motion.reduceMotion ? 0.3 : LokiDesign.Motion.panelCollapseDelay
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            // A newer expand/collapse request supersedes this one.
            guard let self, generation == self.resizeGeneration, !self.controller.isExpanded else { return }
            self.panel.resizeAnchoredBottomCenter(to: LokiDesign.Window.compact)
        }
    }

    // MARK: - Rendering lifecycle

    /// The bubble animates continuously, so stop drawing while the panel is hidden or the screen sleeps.
    private func observeRenderingVisibility() {
        NotificationCenter.default.publisher(for: NSWindow.didChangeOcclusionStateNotification, object: panel)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.updateRendering() }
            .store(in: &cancellables)

        let workspace = NSWorkspace.shared.notificationCenter
        let sleeping = [NSWorkspace.screensDidSleepNotification, NSWorkspace.willSleepNotification]
        let waking = [NSWorkspace.screensDidWakeNotification, NSWorkspace.didWakeNotification]
        for name in sleeping {
            workspace.publisher(for: name)
                .receive(on: DispatchQueue.main)
                .sink { [weak self] _ in
                    self?.screenAsleep = true
                    self?.updateRendering()
                }
                .store(in: &cancellables)
        }
        for name in waking {
            workspace.publisher(for: name)
                .receive(on: DispatchQueue.main)
                .sink { [weak self] _ in
                    self?.screenAsleep = false
                    self?.updateRendering()
                }
                .store(in: &cancellables)
        }
    }

    private func updateRendering() {
        let visible = isBubbleVisible && panel.occlusionState.contains(.visible)
        controller.bubble.isRendering = visible && !screenAsleep
    }

    // MARK: - Position

    /// Forgets the remembered position, so this and later launches use bottom-center.
    private func resetPosition() {
        settings.panelPosition = nil
        panel.positionBottomCenter()
        setBubbleVisible(true)
    }

    /// Displays attached or removed, a resolution change, or the Dock or menu bar auto-hiding all
    /// change the visible frames; keep the panel reachable.
    private func observeScreenChanges() {
        NotificationCenter.default.publisher(for: NSApplication.didChangeScreenParametersNotification)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.panel.clampToScreens() }
            .store(in: &cancellables)
    }

    /// With several displays, a shortcut brings the panel to the display the pointer is on.
    private func summonToActiveDisplay() {
        guard settings.summonOnActiveDisplay else { return }
        panel.moveToScreen(containing: NSEvent.mouseLocation)
    }
}
