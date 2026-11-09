import AppKit

/// Target of the menu items shared by the main menu and the status item menu. It also validates
/// them, so items enable, disable and retitle themselves from the assistant's current state.
@MainActor
final class AppCommands: NSObject, NSMenuItemValidation {
    private let controller: LokiController
    private let settings: LokiSettings

    /// Provided by the app delegate, which owns the floating panel.
    var isBubbleVisible: () -> Bool = { true }
    var setBubbleVisible: (Bool) -> Void = { _ in }
    /// True when a window other than the floating panel (Settings, About) is the key window.
    var isOtherWindowKey: () -> Bool = { false }

    init(controller: LokiController, settings: LokiSettings) {
        self.controller = controller
        self.settings = settings
        super.init()
    }

    // MARK: - Actions

    /// Open or close the conversation. A hidden bubble is shown first and the conversation opens.
    @objc func toggleConversation(_ sender: Any?) {
        if !isBubbleVisible() {
            setBubbleVisible(true)
            controller.setExpanded(true)
        } else {
            controller.toggleExpanded()
        }
    }

    /// Cmd+W: closes the key window when it is Settings or About, otherwise the conversation.
    @objc func closeConversation(_ sender: Any?) {
        if isOtherWindowKey() {
            NSApp.keyWindow?.performClose(nil)
        } else {
            controller.dismissExpanded()
        }
    }

    @objc func toggleListening(_ sender: Any?) {
        controller.toggleVoice()
    }

    @objc func stopSpeaking(_ sender: Any?) {
        controller.stopSpeaking()
    }

    @objc func clearConversation(_ sender: Any?) {
        controller.clearConversation()
    }

    @objc func focusInput(_ sender: Any?) {
        if !isBubbleVisible() { setBubbleVisible(true) }
        controller.focusInput()
    }

    @objc func resetPosition(_ sender: Any?) {
        controller.resetPosition()
    }

    @objc func toggleBubble(_ sender: Any?) {
        let show = !isBubbleVisible()
        setBubbleVisible(show)
        // An explicit choice from a menu is remembered for the next launch.
        settings.showBubbleAtLaunch = show
    }

    @objc func bringToFront(_ sender: Any?) {
        setBubbleVisible(true)
    }

    @objc func showSettings(_ sender: Any?) {
        controller.showSettings()
    }

    @objc func showAbout(_ sender: Any?) {
        AboutPanel.show()
    }

    @objc func openHelp(_ sender: Any?) {
        if let url = AboutPanel.helpURL {
            NSWorkspace.shared.open(url)
        }
    }

    // MARK: - Validation

    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        switch item.action {
        case #selector(toggleConversation(_:)):
            item.title = controller.isExpanded ? "Close Conversation" : "Open Conversation"
            return true
        case #selector(closeConversation(_:)):
            let otherWindow = isOtherWindowKey()
            item.title = otherWindow ? "Close Window" : "Close Conversation"
            return otherWindow || controller.isExpanded
        case #selector(toggleListening(_:)):
            item.title = controller.phase == .listening ? "Stop Listening" : "Start Listening"
            return true
        case #selector(stopSpeaking(_:)):
            return controller.isSpeaking
        case #selector(clearConversation(_:)):
            return !controller.messages.isEmpty
        case #selector(toggleBubble(_:)):
            item.title = isBubbleVisible() ? "Hide Bubble" : "Show Bubble"
            return true
        case #selector(showSettings(_:)):
            return controller.canShowSettings
        default:
            return true
        }
    }
}
