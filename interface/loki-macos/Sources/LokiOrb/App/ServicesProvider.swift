import AppKit

/// Handles the "Ask Loki" entry that macOS shows in the Services menu (and the context menu) for
/// selected text in any app. It is declared under `NSServices` in Info.plist, so it only exists in
/// Loki.app. The text is put into the input field and never sent: the user presses Return.
@MainActor
final class ServicesProvider: NSObject {
    /// Called with the selected text, collapsed to one line, ready for the input field.
    private let onText: (String) -> Void

    init(onText: @escaping (String) -> Void) {
        self.onText = onText
        super.init()
    }

    /// Makes this object the app's services provider. Does nothing unbundled, where no service
    /// is declared and registering one would have no effect.
    func register() {
        guard AppBundle.isBundled else { return }
        NSApp.servicesProvider = self
        NSUpdateDynamicServices()
    }

    @objc func askLoki(_ pboard: NSPasteboard, userData: String, error: AutoreleasingUnsafeMutablePointer<NSString>) {
        guard let selection = pboard.string(forType: .string) else {
            error.pointee = "Loki could not read the selected text." as NSString
            return
        }
        let text = DroppedContent.singleLine(selection)
        guard !text.isEmpty else {
            error.pointee = "The selection is empty." as NSString
            return
        }
        onText(text)
    }
}
