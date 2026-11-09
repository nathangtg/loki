import Foundation
import ServiceManagement

/// Launch at Login through `SMAppService.mainApp`. The system owns the state, so nothing is stored
/// in `UserDefaults`: the toggle always reflects what System Settings > Login Items shows.
@MainActor
final class LaunchAtLogin: ObservableObject {
    enum State: Equatable {
        /// Running unbundled (`swift run`): there is no app to register.
        case unavailable
        case enabled
        case disabled
        /// Registered, but the user must allow it in System Settings > General > Login Items.
        case requiresApproval
    }

    @Published private(set) var state: State
    @Published private(set) var errorMessage: String?

    init() {
        state = Self.currentState()
    }

    var isAvailable: Bool { state != .unavailable }

    /// The toggle is on when the app is registered, including when approval is still pending.
    var isOn: Bool { state == .enabled || state == .requiresApproval }

    func refresh() {
        state = Self.currentState()
    }

    func setEnabled(_ enabled: Bool) {
        guard AppBundle.isBundled else { return }
        errorMessage = nil
        do {
            if enabled {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
        } catch {
            errorMessage = error.localizedDescription
        }
        state = Self.currentState()
    }

    func openLoginItemsSettings() {
        guard AppBundle.isBundled else { return }
        SMAppService.openSystemSettingsLoginItems()
    }

    private static func currentState() -> State {
        guard AppBundle.isBundled else { return .unavailable }
        switch SMAppService.mainApp.status {
        case .enabled: return .enabled
        case .requiresApproval: return .requiresApproval
        case .notRegistered, .notFound: return .disabled
        @unknown default: return .disabled
        }
    }
}
