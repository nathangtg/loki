import SwiftUI

/// View-local boolean state (hover, copied, expanded details, ...).
///
/// Uses `@StateObject` rather than `@State`: with the toolchain/SDK this project builds against,
/// `@State` is declared as an external macro whose `SwiftUIMacros` plugin is not installed for
/// SwiftPM command-line builds, so `swift build` fails on any `@State` property.
final class LocalFlag: ObservableObject {
    @Published var isOn: Bool

    init(_ initial: Bool = false) {
        isOn = initial
    }
}
