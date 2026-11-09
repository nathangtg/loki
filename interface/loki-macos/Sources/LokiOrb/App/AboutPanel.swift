import AppKit

enum AboutPanel {
    static let version = "0.1.0"
    static let helpURL = URL(string: "https://github.com/nathangtg/loki/blob/master/interface/README.md")

    @MainActor
    static func show() {
        let credits = NSAttributedString(
            string: "A voice and text assistant that works the apps on your Mac for you.\n"
                + "Press ⌘⇧V to talk or ⌘⇧Space to type.",
            attributes: [
                .font: NSFont.systemFont(ofSize: NSFont.smallSystemFontSize),
                .foregroundColor: NSColor.secondaryLabelColor,
            ]
        )
        NSApp.activate(ignoringOtherApps: true)
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "Loki",
            .applicationVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? version,
            .version: "",
            .credits: credits,
        ])
    }
}
