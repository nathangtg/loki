import SwiftUI
import UniformTypeIdentifiers

/// Turns what the user dropped on Loki into text for the input field.
enum DroppedContent {
    static let types: [UTType] = [.fileURL, .plainText]

    /// A file path as it can be pasted after a command: untouched when it only has characters a
    /// shell treats literally, otherwise in single quotes.
    static func shellQuoted(_ path: String) -> String {
        let safe = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_@%+=:,./-")
        if !path.isEmpty, path.unicodeScalars.allSatisfy(safe.contains) { return path }
        return "'" + path.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    /// Dropped text on one line, the way a single-line field takes a paste.
    static func singleLine(_ text: String) -> String {
        text.split(whereSeparator: { $0.isNewline })
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
            .joined(separator: " ")
    }

    /// The text to insert for the dropped items, in drop order, or nil when none could be read.
    static func text(from providers: [NSItemProvider]) async -> String? {
        var parts: [String] = []
        for provider in providers {
            if provider.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) {
                if let url = await fileURL(from: provider), url.isFileURL {
                    parts.append(shellQuoted(url.path))
                }
            } else if provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) {
                if let text = await string(from: provider) {
                    let line = singleLine(text)
                    if !line.isEmpty { parts.append(line) }
                }
            }
        }
        return parts.isEmpty ? nil : parts.joined(separator: " ")
    }

    private static func fileURL(from provider: NSItemProvider) async -> URL? {
        await withCheckedContinuation { continuation in
            provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
                switch item {
                case let url as URL:
                    continuation.resume(returning: url)
                case let data as Data:
                    continuation.resume(returning: URL(dataRepresentation: data, relativeTo: nil))
                default:
                    continuation.resume(returning: nil)
                }
            }
        }
    }

    private static func string(from provider: NSItemProvider) async -> String? {
        await withCheckedContinuation { continuation in
            provider.loadItem(forTypeIdentifier: UTType.plainText.identifier, options: nil) { item, _ in
                switch item {
                case let string as String:
                    continuation.resume(returning: string)
                case let data as Data:
                    continuation.resume(returning: String(data: data, encoding: .utf8))
                default:
                    continuation.resume(returning: nil)
                }
            }
        }
    }
}

/// Makes a view accept dropped text and files, shows an accent-colored ring while something is
/// dragged over it, and hands the result to the controller's input field. Nothing is sent.
private struct LokiDropTarget<Highlight: InsettableShape>: ViewModifier {
    @ObservedObject var controller: LokiController
    let highlight: Highlight
    @StateObject private var targeted = LocalFlag()
    @Environment(\.colorSchemeContrast) private var contrast

    func body(content: Content) -> some View {
        let isTargeted = Binding(get: { targeted.isOn }, set: { targeted.isOn = $0 })
        return content
            .overlay(
                highlight
                    .fill(Color.accentColor.opacity(targeted.isOn ? 0.14 : 0))
                    .overlay(
                        highlight
                            .strokeBorder(Color.accentColor, lineWidth: contrast == .increased ? 4 : 3)
                            .opacity(targeted.isOn ? 1 : 0)
                    )
                    .allowsHitTesting(false)
            )
            .animation(LokiDesign.Motion.focus, value: targeted.isOn)
            .onDrop(of: DroppedContent.types, isTargeted: isTargeted) { providers in
                Task { @MainActor in
                    if let text = await DroppedContent.text(from: providers) {
                        controller.appendToInput(text)
                    }
                }
                return true
            }
    }
}

extension View {
    /// Accepts dropped text and files into the assistant's input field.
    func lokiDropTarget<Highlight: InsettableShape>(controller: LokiController, highlight: Highlight) -> some View {
        modifier(LokiDropTarget(controller: controller, highlight: highlight))
    }
}
