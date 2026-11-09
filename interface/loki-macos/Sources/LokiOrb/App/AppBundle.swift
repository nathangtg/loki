import Foundation

/// Whether Loki runs from a Loki.app bundle. Unbundled (`swift run`) there is no Info.plist, so
/// Login Items, the Services menu and microphone attribution are unavailable and must be skipped.
enum AppBundle {
    static let isBundled: Bool = {
        Bundle.main.bundleIdentifier != nil && Bundle.main.bundleURL.pathExtension == "app"
    }()
}
