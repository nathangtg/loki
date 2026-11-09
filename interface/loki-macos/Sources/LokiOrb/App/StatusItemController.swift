import AppKit
import Combine

/// The menu bar item. Its template icon follows the assistant phase, and its menu is validated
/// against the current state each time it opens.
@MainActor
final class StatusItemController: NSObject, NSMenuDelegate {
    private let controller: LokiController
    private let statusItem: NSStatusItem
    private let headerItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private var cancellables = Set<AnyCancellable>()
    private var shownSymbol: String?

    init(controller: LokiController, commands: AppCommands) {
        self.controller = controller
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        super.init()

        statusItem.menu = makeMenu(commands)
        statusItem.button?.setAccessibilityLabel("Loki")
        refresh()

        // Published values change after `willSet`; hop to the next main-queue turn to read them.
        controller.$phase
            .combineLatest(controller.$activityLabel, controller.$isExpanded)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _, _, _ in self?.refresh() }
            .store(in: &cancellables)
    }

    // MARK: - Menu

    private func makeMenu(_ commands: AppCommands) -> NSMenu {
        let menu = NSMenu(title: "Loki")
        menu.delegate = self

        headerItem.isEnabled = false
        menu.addItem(headerItem)
        menu.addItem(.separator())

        menu.addItem(item(
            "Start Listening", #selector(AppCommands.toggleListening(_:)), "mic",
            key: "v", modifiers: [.command, .shift], target: commands
        ))
        menu.addItem(item(
            "Open Conversation", #selector(AppCommands.toggleConversation(_:)), "text.bubble",
            key: " ", modifiers: [.command, .shift], target: commands
        ))
        menu.addItem(item(
            "Stop Speaking", #selector(AppCommands.stopSpeaking(_:)), "speaker.slash",
            key: ".", target: commands
        ))
        menu.addItem(.separator())

        menu.addItem(item("Hide Bubble", #selector(AppCommands.toggleBubble(_:)), "eye.slash", target: commands))
        menu.addItem(item(
            "Reset Orb Position", #selector(AppCommands.resetPosition(_:)), "arrow.counterclockwise",
            target: commands
        ))
        menu.addItem(.separator())

        menu.addItem(item(
            "Settings…", #selector(AppCommands.showSettings(_:)), "gearshape", key: ",", target: commands
        ))
        menu.addItem(item("About Loki", #selector(AppCommands.showAbout(_:)), "info.circle", target: commands))
        menu.addItem(.separator())
        menu.addItem(item("Quit Loki", #selector(NSApplication.terminate(_:)), nil, key: "q"))
        return menu
    }

    private func item(
        _ title: String,
        _ action: Selector,
        _ symbol: String?,
        key: String = "",
        modifiers: NSEvent.ModifierFlags = [.command],
        target: AnyObject? = nil
    ) -> NSMenuItem {
        let item = MainMenu.item(title, action, key: key, modifiers: modifiers, target: target)
        if let symbol {
            item.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)
        }
        return item
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        updateHeader()
    }

    // MARK: - State

    private func refresh() {
        updateIcon()
        updateHeader()
        let status = controller.statusLine
        guard let button = statusItem.button else { return }
        button.toolTip = "Loki: \(status)"
        button.setAccessibilityLabel("Loki")
        button.setAccessibilityValue(status)
    }

    private func updateHeader() {
        headerItem.title = controller.statusLine
        headerItem.image = Self.symbolImage(Self.menuBarSymbol(for: controller.phase), description: nil)
    }

    private func updateIcon() {
        let symbol = Self.menuBarSymbol(for: controller.phase)
        guard symbol != shownSymbol, let button = statusItem.button else { return }
        shownSymbol = symbol
        button.image = Self.symbolImage(symbol, description: controller.phase.title)
    }

    /// A glyph per phase so the state reads in the menu bar without color (the icon is a template
    /// image, which macOS tints for the menu bar appearance).
    private static func menuBarSymbol(for phase: AssistantPhase) -> String {
        switch phase {
        case .idle: return "circle.circle"
        case .listening: return "waveform"
        case .thinking, .executing: return "circle.hexagongrid.fill"
        case .responding: return "sparkles"
        case .awaitingReply: return "bubble.left"
        case .completed: return "checkmark.circle"
        case .error: return "exclamationmark.triangle"
        }
    }

    private static func symbolImage(_ name: String, description: String?) -> NSImage? {
        let image = NSImage(systemSymbolName: name, accessibilityDescription: description)
            ?? NSImage(systemSymbolName: "circle", accessibilityDescription: description)
        image?.isTemplate = true
        return image
    }
}
