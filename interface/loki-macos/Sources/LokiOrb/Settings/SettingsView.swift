import AppKit
import SwiftUI

/// Drives the "Check Server" button.
@MainActor
final class ServerCheckModel: ObservableObject {
    enum State: Equatable {
        case unchecked
        case checking
        case reachable
        case unreachable
    }

    @Published private(set) var state: State = .unchecked
    private let client = OrchestrationClient()

    func check() {
        guard state != .checking else { return }
        state = .checking
        Task {
            let ok = await client.checkHealth()
            state = ok ? .reachable : .unreachable
        }
    }
}

struct SettingsView: View {
    @ObservedObject var settings: LokiSettings
    @ObservedObject var hotkeys: HotkeyManager
    let serverAddress: String

    var body: some View {
        TabView {
            GeneralSettingsTab(settings: settings)
                .tabItem { Label("General", systemImage: "gearshape") }
            ShortcutsSettingsTab(hotkeys: hotkeys)
                .tabItem { Label("Shortcuts", systemImage: "keyboard") }
            AccessibilitySettingsTab(settings: settings)
                .tabItem { Label("Accessibility", systemImage: "accessibility") }
            AboutSettingsTab(serverAddress: serverAddress)
                .tabItem { Label("About", systemImage: "info.circle") }
        }
        .frame(width: 500, height: 560)
    }
}

// MARK: - General

private struct GeneralSettingsTab: View {
    @ObservedObject var settings: LokiSettings
    @StateObject private var launchAtLogin = LaunchAtLogin()

    var body: some View {
        Form {
            Section {
                Toggle("Show the Loki bubble when the app starts", isOn: $settings.showBubbleAtLaunch)
                Toggle("Play a sound when listening starts and stops", isOn: $settings.playListeningSounds)
                Toggle("Open the conversation when Loki asks a question", isOn: $settings.openConversationForQuestions)
                Toggle("Open on the display where the pointer is", isOn: $settings.summonOnActiveDisplay)
            } footer: {
                Text("If the bubble is hidden, use the Loki icon in the menu bar or press ⌘⇧Space to bring it back. When the conversation stays closed, a question is shown on the bubble and kept in the conversation. With several displays, ⌘⇧Space and ⌘⇧V bring the bubble to the display you are working on, at the same relative position. Clicking the bubble never moves it.")
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.leading)
            }
            Section {
                Picker("Conversation appearance", selection: $settings.appearance) {
                    ForEach(LokiAppearance.allCases) { appearance in
                        Text(appearance.title).tag(appearance)
                    }
                }
            } header: {
                Text("Appearance")
            } footer: {
                Text("Match System follows Light and Dark in System Settings. The bubble itself looks the same in both.")
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.leading)
            }
            Section {
                Toggle("Launch Loki at login", isOn: Binding(
                    get: { launchAtLogin.isOn },
                    set: { launchAtLogin.setEnabled($0) }
                ))
                .disabled(!launchAtLogin.isAvailable)
                if launchAtLogin.state == .requiresApproval {
                    HStack {
                        Text("Allow Loki in System Settings > General > Login Items.")
                            .font(.callout)
                            .foregroundColor(.secondary)
                        Spacer()
                        Button("Open Login Items") { launchAtLogin.openLoginItemsSettings() }
                    }
                }
                if let message = launchAtLogin.errorMessage {
                    Text(message)
                        .font(.callout)
                        .foregroundColor(.red)
                }
            } header: {
                Text("Startup")
            } footer: {
                Text(launchAtLogin.isAvailable
                    ? "macOS keeps this setting. You can also change it in System Settings > General > Login Items."
                    : "Available when running Loki.app")
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .formStyle(.grouped)
        .onAppear { launchAtLogin.refresh() }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            launchAtLogin.refresh()
        }
    }
}

// MARK: - Accessibility

private struct AccessibilitySettingsTab: View {
    @ObservedObject var settings: LokiSettings

    var body: some View {
        Form {
            Section {
                Picker("When VoiceOver is running", selection: $settings.spokenRepliesWithVoiceOver) {
                    ForEach(VoiceOverSpeechPolicy.allCases) { policy in
                        Text(policy.title).tag(policy)
                    }
                }
                .pickerStyle(.radioGroup)
            } header: {
                Text("Spoken replies")
            } footer: {
                Text("By default Loki stays quiet while VoiceOver is running, so two voices never talk at once. VoiceOver announces each reply, question and error instead, and the full text stays in the conversation. Choose Use Loki voice as well to hear both. Without VoiceOver, Loki speaks replies to voice commands as usual.")
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.leading)
            }
            Section {
                Text("Loki follows Reduce Motion, Increase Contrast, Reduce Transparency and Differentiate Without Color from System Settings > Accessibility > Display. There is nothing to configure here for those.")
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.leading)
            } header: {
                Text("Display")
            }
        }
        .formStyle(.grouped)
    }
}

// MARK: - Shortcuts

private struct ShortcutsSettingsTab: View {
    @ObservedObject var hotkeys: HotkeyManager

    var body: some View {
        Form {
            Section("Global shortcuts") {
                ShortcutRow(glyphs: "⌘⇧V", title: "Start or stop listening", status: hotkeys.voiceStatus)
                ShortcutRow(glyphs: "⌘⇧Space", title: "Open or close the conversation", status: hotkeys.textStatus)
            }
            Section("While the conversation is open") {
                InAppShortcutRow(glyphs: "⎋", title: "Close the conversation")
                InAppShortcutRow(glyphs: "⌘W", title: "Close the conversation")
                InAppShortcutRow(glyphs: "⌘L", title: "Focus the input field")
                InAppShortcutRow(glyphs: "⌘K", title: "Clear the conversation")
                InAppShortcutRow(glyphs: "⌘.", title: "Stop speaking")
                InAppShortcutRow(glyphs: "⌘,", title: "Open Settings")
            }
        }
        .formStyle(.grouped)
    }
}

private struct ShortcutRow: View {
    let glyphs: String
    let title: String
    let status: HotkeyStatus

    private var statusText: String {
        switch status {
        case .active: return "Active"
        case .notRegistered: return "Not registered yet"
        case .inUse: return "Unavailable: another app already uses this shortcut"
        case .failed(let code): return "Unavailable: the system refused this shortcut (error \(code))"
        }
    }

    private var isActive: Bool { status == .active }

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                HStack(spacing: 4) {
                    Image(systemName: isActive ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                        .foregroundColor(isActive ? .green : .orange)
                        .accessibilityHidden(true)
                    Text(statusText)
                        .font(.callout)
                        .foregroundColor(.secondary)
                }
            }
            Spacer()
            Text(glyphs)
                .font(.system(.body, design: .rounded).weight(.medium))
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(RoundedRectangle(cornerRadius: 6).fill(Color.secondary.opacity(0.15)))
                .accessibilityHidden(true)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(title), \(spokenShortcut)")
        .accessibilityValue(statusText)
    }

    private var spokenShortcut: String {
        glyphs.replacingOccurrences(of: "⌘", with: "Command ")
            .replacingOccurrences(of: "⇧", with: "Shift ")
    }
}

private struct InAppShortcutRow: View {
    let glyphs: String
    let title: String

    var body: some View {
        HStack {
            Text(title)
            Spacer()
            Text(glyphs)
                .font(.system(.body, design: .rounded))
                .foregroundColor(.secondary)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(title), \(spoken)")
    }

    private var spoken: String {
        switch glyphs {
        case "⎋": return "Escape"
        case "⌘.": return "Command period"
        case "⌘,": return "Command comma"
        default: return glyphs.replacingOccurrences(of: "⌘", with: "Command ")
        }
    }
}

// MARK: - About

private struct AboutSettingsTab: View {
    let serverAddress: String
    @StateObject private var check = ServerCheckModel()

    private var version: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? AboutPanel.version
    }

    var body: some View {
        Form {
            Section {
                LabeledContent("Loki", value: "Version \(version)")
                LabeledContent("Server", value: serverAddress)
                HStack {
                    Button("Check Server") { check.check() }
                        .disabled(check.state == .checking)
                    Spacer()
                    statusView
                }
            } footer: {
                Text("Loki talks to the orchestration server on this address. Set LOKI_ORCHESTRATION_BASE to change it.")
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.leading)
            }
        }
        .formStyle(.grouped)
    }

    @ViewBuilder
    private var statusView: some View {
        switch check.state {
        case .unchecked:
            EmptyView()
        case .checking:
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text("Checking…").foregroundColor(.secondary)
            }
        case .reachable:
            Label("Reachable", systemImage: "checkmark.circle.fill")
                .foregroundColor(.green)
        case .unreachable:
            Label("Unreachable", systemImage: "xmark.octagon.fill")
                .foregroundColor(.red)
        }
    }
}
