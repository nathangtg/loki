import AppKit
import SwiftUI

/// Owns the single, reusable Settings window. It uses the normal system appearance (it does not
/// inherit the dark glass look of the floating panel).
@MainActor
final class SettingsWindowController: NSObject, NSWindowDelegate {
    private static let frameName = "LokiSettings"

    private let settings: LokiSettings
    private let hotkeys: HotkeyManager
    private let serverAddress: String
    private var window: NSWindow?
    private var previousApp: NSRunningApplication?

    init(settings: LokiSettings, hotkeys: HotkeyManager, serverAddress: String) {
        self.settings = settings
        self.hotkeys = hotkeys
        self.serverAddress = serverAddress
        super.init()
    }

    func show() {
        let window = window ?? makeWindow()
        self.window = window
        if !window.isVisible {
            previousApp = FocusReturn.captureFrontmost()
        }
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    private func makeWindow() -> NSWindow {
        let view = SettingsView(settings: settings, hotkeys: hotkeys, serverAddress: serverAddress)
        let window = NSWindow(contentViewController: NSHostingController(rootView: view))
        window.styleMask = [.titled, .closable, .miniaturizable]
        window.title = "Loki Settings"
        window.isReleasedWhenClosed = false
        window.delegate = self
        if !window.setFrameUsingName(Self.frameName) {
            window.center()
        }
        window.setFrameAutosaveName(Self.frameName)
        return window
    }

    func windowWillClose(_ notification: Notification) {
        let previous = previousApp
        previousApp = nil
        // Let the window finish closing first, so Loki is no longer the key app when focus returns.
        DispatchQueue.main.async { FocusReturn.restore(previous) }
    }
}
