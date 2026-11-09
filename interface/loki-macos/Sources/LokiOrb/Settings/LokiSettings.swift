import AppKit
import Foundation

/// Which appearance the floating assistant uses.
enum LokiAppearance: String, CaseIterable, Identifiable {
    case system
    case light
    case dark

    var id: String { rawValue }

    var title: String {
        switch self {
        case .system: return "Match System"
        case .light: return "Light"
        case .dark: return "Dark"
        }
    }

    /// The `NSAppearance` to force on the panel, or nil to follow the system. A forced appearance
    /// does not pick up Increase Contrast by itself, so the matching variant is chosen here.
    func nsAppearance(increaseContrast: Bool) -> NSAppearance? {
        switch self {
        case .system: return nil
        case .light: return NSAppearance(named: increaseContrast ? .accessibilityHighContrastAqua : .aqua) ?? NSAppearance(named: .aqua)
        case .dark: return NSAppearance(named: increaseContrast ? .accessibilityHighContrastDarkAqua : .darkAqua) ?? NSAppearance(named: .darkAqua)
        }
    }
}

/// What to do with spoken replies while VoiceOver is running.
enum VoiceOverSpeechPolicy: String, CaseIterable, Identifiable {
    /// Loki stays silent and VoiceOver announces the reply, so two voices never overlap.
    case letVoiceOverRead
    case alsoUseLokiVoice

    var id: String { rawValue }

    var title: String {
        switch self {
        case .letVoiceOverRead: return "Let VoiceOver read replies"
        case .alsoUseLokiVoice: return "Use Loki voice as well"
        }
    }
}

/// User preferences, persisted in `UserDefaults` under a `loki.` key prefix.
///
/// Unbundled (`swift run`) the defaults domain is the executable name (`LokiOrb`); inside a
/// Loki.app bundle it is the bundle identifier.
@MainActor
final class LokiSettings: ObservableObject {
    static let shared = LokiSettings()

    enum Key {
        static let showBubbleAtLaunch = "loki.showBubbleAtLaunch"
        static let playListeningSounds = "loki.playListeningSounds"
        static let openConversationForQuestions = "loki.openConversationForQuestions"
        static let appearance = "loki.appearance"
        static let spokenRepliesWithVoiceOver = "loki.spokenRepliesWithVoiceOver"
        static let summonOnActiveDisplay = "loki.summonOnActiveDisplay"
        static let panelPosition = "loki.panelPosition"
    }

    private let defaults: UserDefaults

    /// Show the floating bubble when the app starts. Also records the last explicit Show/Hide Bubble
    /// choice made from a menu.
    @Published var showBubbleAtLaunch: Bool {
        didSet { defaults.set(showBubbleAtLaunch, forKey: Key.showBubbleAtLaunch) }
    }

    /// Play a short sound when listening starts and stops.
    @Published var playListeningSounds: Bool {
        didSet { defaults.set(playListeningSounds, forKey: Key.playListeningSounds) }
    }

    /// Expand the conversation automatically when Loki asks a follow-up question.
    @Published var openConversationForQuestions: Bool {
        didSet { defaults.set(openConversationForQuestions, forKey: Key.openConversationForQuestions) }
    }

    /// Light, dark, or follow the system.
    @Published var appearance: LokiAppearance {
        didSet { defaults.set(appearance.rawValue, forKey: Key.appearance) }
    }

    /// Whether Loki also speaks replies aloud while VoiceOver is running.
    @Published var spokenRepliesWithVoiceOver: VoiceOverSpeechPolicy {
        didSet { defaults.set(spokenRepliesWithVoiceOver.rawValue, forKey: Key.spokenRepliesWithVoiceOver) }
    }

    /// Move the panel to the display the pointer is on when a shortcut opens it.
    @Published var summonOnActiveDisplay: Bool {
        didSet { defaults.set(summonOnActiveDisplay, forKey: Key.summonOnActiveDisplay) }
    }

    /// Where the user last left the panel, or nil for the default bottom-center. Not published:
    /// no view shows it, and it changes while the panel is dragged.
    var panelPosition: PanelPosition? {
        get { (defaults.dictionary(forKey: Key.panelPosition)).flatMap(PanelPosition.init(dictionary:)) }
        set {
            if let newValue {
                defaults.set(newValue.dictionary, forKey: Key.panelPosition)
            } else {
                defaults.removeObject(forKey: Key.panelPosition)
            }
        }
    }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        defaults.register(defaults: [
            Key.showBubbleAtLaunch: true,
            Key.playListeningSounds: true,
            Key.openConversationForQuestions: true,
            Key.appearance: LokiAppearance.system.rawValue,
            Key.spokenRepliesWithVoiceOver: VoiceOverSpeechPolicy.letVoiceOverRead.rawValue,
            Key.summonOnActiveDisplay: true,
        ])
        summonOnActiveDisplay = defaults.bool(forKey: Key.summonOnActiveDisplay)
        showBubbleAtLaunch = defaults.bool(forKey: Key.showBubbleAtLaunch)
        playListeningSounds = defaults.bool(forKey: Key.playListeningSounds)
        openConversationForQuestions = defaults.bool(forKey: Key.openConversationForQuestions)
        appearance = defaults.string(forKey: Key.appearance).flatMap(LokiAppearance.init(rawValue:)) ?? .system
        spokenRepliesWithVoiceOver = defaults.string(forKey: Key.spokenRepliesWithVoiceOver)
            .flatMap(VoiceOverSpeechPolicy.init(rawValue:)) ?? .letVoiceOverRead
    }
}
