import Carbon
import Combine
import Foundation

/// Whether a global shortcut was registered with the system.
enum HotkeyStatus: Equatable {
    case notRegistered
    case active
    /// Another app (or the system) already owns the key combination.
    case inUse
    case failed(OSStatus)
}

final class HotkeyManager: ObservableObject {
    /// Result of the last registration attempt for each shortcut.
    @Published private(set) var voiceStatus: HotkeyStatus = .notRegistered
    @Published private(set) var textStatus: HotkeyStatus = .notRegistered

    private var voiceHotKeyRef: EventHotKeyRef?
    private var textHotKeyRef: EventHotKeyRef?
    private var eventHandlerRef: EventHandlerRef?

    var onToggleVoice: (() -> Void)?
    var onToggleTextBar: (() -> Void)?

    func registerHotkeys() {
        var eventType = EventTypeSpec(
            eventClass: OSType(kEventClassKeyboard),
            eventKind: UInt32(kEventHotKeyPressed)
        )

        let selfPtr = Unmanaged.passUnretained(self).toOpaque()
        InstallEventHandler(
            GetApplicationEventTarget(),
            { _, event, userData -> OSStatus in
                guard let event, let userData else { return noErr }
                let manager = Unmanaged<HotkeyManager>.fromOpaque(userData).takeUnretainedValue()
                var hotKeyID = EventHotKeyID()
                GetEventParameter(
                    event,
                    EventParamName(kEventParamDirectObject),
                    EventParamType(typeEventHotKeyID),
                    nil,
                    MemoryLayout<EventHotKeyID>.size,
                    nil,
                    &hotKeyID
                )
                DispatchQueue.main.async {
                    if hotKeyID.id == 1 {
                        manager.onToggleVoice?()
                    } else if hotKeyID.id == 2 {
                        manager.onToggleTextBar?()
                    }
                }
                return noErr
            },
            1,
            &eventType,
            selfPtr,
            &eventHandlerRef
        )

        let modifiers = UInt32(cmdKey | shiftKey)
        let voiceID = EventHotKeyID(signature: OSType(0x4C4F4B49), id: 1) // 'LOKI', 1 -> Cmd+Shift+V (keyCode 9)
        let voiceResult = RegisterEventHotKey(
            UInt32(kVK_ANSI_V), modifiers, voiceID, GetApplicationEventTarget(), 0, &voiceHotKeyRef
        )
        voiceStatus = Self.status(for: voiceResult)

        let textID = EventHotKeyID(signature: OSType(0x4C4F4B49), id: 2) // 'LOKI', 2 -> Cmd+Shift+Space (keyCode 49)
        let textResult = RegisterEventHotKey(
            UInt32(kVK_Space), modifiers, textID, GetApplicationEventTarget(), 0, &textHotKeyRef
        )
        textStatus = Self.status(for: textResult)
    }

    private static func status(for result: OSStatus) -> HotkeyStatus {
        switch result {
        case noErr: return .active
        case OSStatus(eventHotKeyExistsErr): return .inUse
        default: return .failed(result)
        }
    }

    deinit {
        if let voiceHotKeyRef {
            UnregisterEventHotKey(voiceHotKeyRef)
        }
        if let textHotKeyRef {
            UnregisterEventHotKey(textHotKeyRef)
        }
        if let eventHandlerRef {
            RemoveEventHandler(eventHandlerRef)
        }
    }
}
