import AppKit

/// Builds the application main menu. Loki is an accessory app, so the menu bar is not shown, but
/// the menu still routes key equivalents (Cmd+C/V/X/A/Z in the input field, Cmd+W, Cmd+, ...)
/// whenever the conversation is the key window.
///
/// The global Carbon hotkeys (Cmd+Shift+V, Cmd+Shift+Space) stay in `HotkeyManager`; the items
/// that mention them here only display the shortcut.
@MainActor
enum MainMenu {
    static func install(commands: AppCommands) {
        let mainMenu = NSMenu(title: "Main Menu")
        mainMenu.addItem(submenuItem(appMenu(commands)))
        mainMenu.addItem(submenuItem(editMenu()))
        mainMenu.addItem(submenuItem(conversationMenu(commands)))

        let window = windowMenu(commands)
        mainMenu.addItem(submenuItem(window))
        let help = helpMenu(commands)
        mainMenu.addItem(submenuItem(help))

        NSApp.mainMenu = mainMenu
        NSApp.windowsMenu = window
        NSApp.helpMenu = help
    }

    // MARK: - Menus

    private static func appMenu(_ commands: AppCommands) -> NSMenu {
        let menu = NSMenu(title: "Loki")
        menu.addItem(item("About Loki", #selector(AppCommands.showAbout(_:)), target: commands))
        menu.addItem(.separator())
        menu.addItem(item("Settings…", #selector(AppCommands.showSettings(_:)), key: ",", target: commands))
        menu.addItem(.separator())
        menu.addItem(item("Quit Loki", #selector(NSApplication.terminate(_:)), key: "q"))
        return menu
    }

    /// Standard responder-chain items (no target), so the focused text field handles them.
    private static func editMenu() -> NSMenu {
        let menu = NSMenu(title: "Edit")
        menu.addItem(item("Undo", Selector(("undo:")), key: "z"))
        menu.addItem(item("Redo", Selector(("redo:")), key: "z", modifiers: [.command, .shift]))
        menu.addItem(.separator())
        menu.addItem(item("Cut", #selector(NSText.cut(_:)), key: "x"))
        menu.addItem(item("Copy", #selector(NSText.copy(_:)), key: "c"))
        menu.addItem(item("Paste", #selector(NSText.paste(_:)), key: "v"))
        menu.addItem(item("Select All", #selector(NSText.selectAll(_:)), key: "a"))
        return menu
    }

    private static func conversationMenu(_ commands: AppCommands) -> NSMenu {
        let menu = NSMenu(title: "Conversation")
        menu.addItem(item(
            "Open Conversation", #selector(AppCommands.toggleConversation(_:)),
            key: " ", modifiers: [.command, .shift], target: commands
        ))
        menu.addItem(item(
            "Start Listening", #selector(AppCommands.toggleListening(_:)),
            key: "v", modifiers: [.command, .shift], target: commands
        ))
        menu.addItem(item("Stop Speaking", #selector(AppCommands.stopSpeaking(_:)), key: ".", target: commands))
        menu.addItem(.separator())
        menu.addItem(item("Focus Input", #selector(AppCommands.focusInput(_:)), key: "l", target: commands))
        menu.addItem(item("Clear Conversation", #selector(AppCommands.clearConversation(_:)), key: "k", target: commands))
        menu.addItem(.separator())
        menu.addItem(item("Reset Position", #selector(AppCommands.resetPosition(_:)), target: commands))
        return menu
    }

    private static func windowMenu(_ commands: AppCommands) -> NSMenu {
        let menu = NSMenu(title: "Window")
        menu.addItem(item("Close Conversation", #selector(AppCommands.closeConversation(_:)), key: "w", target: commands))
        menu.addItem(.separator())
        menu.addItem(item("Bring Loki to Front", #selector(AppCommands.bringToFront(_:)), target: commands))
        return menu
    }

    private static func helpMenu(_ commands: AppCommands) -> NSMenu {
        let menu = NSMenu(title: "Help")
        menu.addItem(item("Loki Help", #selector(AppCommands.openHelp(_:)), target: commands))
        return menu
    }

    // MARK: - Helpers

    private static func submenuItem(_ menu: NSMenu) -> NSMenuItem {
        let item = NSMenuItem(title: menu.title, action: nil, keyEquivalent: "")
        item.submenu = menu
        return item
    }

    static func item(
        _ title: String,
        _ action: Selector?,
        key: String = "",
        modifiers: NSEvent.ModifierFlags = [.command],
        target: AnyObject? = nil
    ) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        if !key.isEmpty { item.keyEquivalentModifierMask = modifiers }
        item.target = target
        return item
    }
}
