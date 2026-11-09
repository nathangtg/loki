import SwiftUI

/// The expanded assistant: header, conversation, input. Glass is used only on the card itself;
/// message surfaces stay solid enough to read.
struct ExpandedCardView: View {
    @ObservedObject var controller: LokiController
    let bubbleSpace: Namespace.ID
    @Environment(\.colorSchemeContrast) private var contrast

    var body: some View {
        VStack(spacing: 0) {
            CardHeader(controller: controller, bubbleSpace: bubbleSpace)
            Rectangle()
                .fill(LokiDesign.Colors.hairline)
                .frame(height: 1)
            ConversationView(controller: controller)
            InputBar(controller: controller)
                .padding(.horizontal, LokiDesign.Spacing.m)
                .padding(.bottom, LokiDesign.Spacing.m)
                .padding(.top, LokiDesign.Spacing.xs)
        }
        .background(CardBackground(phase: controller.phase))
        .clipShape(RoundedRectangle(cornerRadius: LokiDesign.Radius.card, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: LokiDesign.Radius.card, style: .continuous)
                .stroke(cardStroke, lineWidth: contrast == .increased ? 1.5 : 1)
        )
        .lokiDropTarget(
            controller: controller,
            highlight: RoundedRectangle(cornerRadius: LokiDesign.Radius.card, style: .continuous)
        )
        .shadow(color: LokiDesign.Colors.shadow, radius: 18, x: 0, y: 8)
        .animation(LokiDesign.Motion.stateChange, value: controller.phase)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Loki conversation")
        .accessibilityHint("Drop text or files here to add them to your message. Nothing is sent until you press Return.")
    }
}

extension ExpandedCardView {
    /// A soft gradient outline, or a solid line with Increase Contrast.
    fileprivate var cardStroke: AnyShapeStyle {
        if contrast == .increased { return AnyShapeStyle(LokiDesign.Colors.cardStrokeSolid) }
        return AnyShapeStyle(LinearGradient(
            colors: [
                LokiDesign.Colors.cardStrokeLeading,
                LokiDesign.Colors.cardStrokeMiddle,
                LokiDesign.Colors.tint(controller.phase.tint, opacity: 0.25),
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        ))
    }
}

/// Glass with a faint, state-tinted light at the top that echoes the bubble: dark glass in the Dark
/// appearance, a light wash over the material in Light, and a solid window fill when Reduce
/// Transparency is on.
private struct CardBackground: View {
    let phase: AssistantPhase
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    var body: some View {
        ZStack {
            if reduceTransparency {
                Rectangle().fill(Color(nsColor: .windowBackgroundColor))
                if scheme == .dark {
                    Rectangle().fill(LokiDesign.Palette.abyss.color(opacity: 0.55))
                }
            } else {
                Rectangle().fill(.regularMaterial)
                Rectangle().fill(LokiDesign.Colors.cardWash)
            }
            RadialGradient(
                colors: [phase.tint.color(opacity: scheme == .dark ? 0.20 : 0.12), phase.tint.color(opacity: 0)],
                center: .topLeading,
                startRadius: 0,
                endRadius: 320
            )
            RadialGradient(
                colors: [LokiDesign.Palette.violet.color(opacity: scheme == .dark ? 0.08 : 0.05), .clear],
                center: .bottomTrailing,
                startRadius: 0,
                endRadius: 280
            )
        }
    }
}

// MARK: - Header

private struct CardHeader: View {
    @ObservedObject var controller: LokiController
    let bubbleSpace: Namespace.ID

    var body: some View {
        HStack(spacing: LokiDesign.Spacing.m) {
            BubbleView(model: controller.bubble, mini: true)
                .matchedGeometryEffect(id: "bubble", in: bubbleSpace)
                .frame(width: LokiDesign.Bubble.miniFrame, height: LokiDesign.Bubble.miniFrame)
                .contentShape(Circle())
                .onTapGesture { controller.toggleVoice() }
                .help(controller.phase == .listening ? "Stop listening (⌘⇧V)" : "Start listening (⌘⇧V)")
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(controller.phase == .listening ? "Stop listening" : "Start listening")
                .accessibilityValue(controller.statusLine)
                .accessibilityAddTraits(.isButton)
                .accessibilityAction { controller.toggleVoice() }

            VStack(alignment: .leading, spacing: 1) {
                Text("LOKI")
                    .font(LokiDesign.Typography.wordmark)
                    .tracking(2.4)
                    .foregroundColor(LokiDesign.Colors.textPrimary)

                HStack(spacing: 5) {
                    Image(systemName: controller.phase.symbol)
                        .font(LokiDesign.Typography.symbolSmall)
                        .foregroundColor(LokiDesign.Colors.tint(controller.phase.tint))
                    Text(controller.statusLine)
                        .font(LokiDesign.Typography.status)
                        .foregroundColor(LokiDesign.Colors.textSecondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                        .id(controller.statusLine)
                        .transition(.opacity)
                }
                .animation(LokiDesign.Motion.focus, value: controller.statusLine)
                .accessibilityElement(children: .combine)
                .accessibilityLabel("Status: \(controller.statusLine)")
            }

            Spacer(minLength: 0)

            Menu {
                Button("Settings…", action: controller.showSettings)
                    .keyboardShortcut(",", modifiers: .command)
                    .disabled(!controller.canShowSettings)
                Button("About Loki", action: AboutPanel.show)
                Divider()
                Button("Clear Conversation", action: controller.clearConversation)
                    .disabled(controller.messages.isEmpty)
                Button("Reset Bubble Position", action: controller.resetPosition)
                Divider()
                Text("Server: \(controller.serverAddress)")
                Divider()
                Button("Quit LOKI") { NSApp.terminate(nil) }
            } label: {
                Image(systemName: "gearshape")
                    .font(LokiDesign.Typography.symbolGear)
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .foregroundColor(LokiDesign.Colors.textSecondary)
            .help("Settings")
            .accessibilityLabel("Settings")

            HeaderButton(symbol: "chevron.down", label: "Collapse conversation (Esc)") {
                controller.dismissExpanded()
            }
            .keyboardShortcut(.cancelAction)
        }
        .padding(.leading, LokiDesign.Spacing.m)
        .padding(.trailing, LokiDesign.Spacing.l)
        .padding(.vertical, LokiDesign.Spacing.s)
    }
}

private struct HeaderButton: View {
    let symbol: String
    let label: String
    let action: () -> Void
    @StateObject private var hover = LocalFlag()

    var body: some View {
        let hovering = hover.isOn
        return Button(action: action) {
            Image(systemName: symbol)
                .font(LokiDesign.Typography.symbolSmall)
                .foregroundColor(LokiDesign.Colors.textSecondary)
                .frame(width: 24, height: 24)
                .background(Circle().fill(hovering ? LokiDesign.Colors.surfaceRaised : LokiDesign.Colors.surface))
        }
        .buttonStyle(.plain)
        .onHover { hover.isOn = $0 }
        .help(label)
        .accessibilityLabel(label)
    }
}

// MARK: - Input

struct InputBar: View {
    @ObservedObject var controller: LokiController
    @FocusState private var focused: Bool
    @StateObject private var flash = LocalFlag()
    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    private var submitFlash: Bool { flash.isOn }

    private var trimmedEmpty: Bool {
        controller.inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var placeholder: String {
        controller.pendingQuestion != nil ? "Reply to LOKI…" : "Ask LOKI…"
    }

    var body: some View {
        HStack(spacing: LokiDesign.Spacing.s) {
            TextField(placeholder, text: $controller.inputText)
                .textFieldStyle(.plain)
                .font(LokiDesign.Typography.input)
                .foregroundColor(LokiDesign.Colors.textPrimary)
                .focused($focused)
                .onSubmit(submit)
                .onExitCommand { controller.dismissExpanded() }
                .accessibilityLabel(placeholder)
                .padding(.leading, LokiDesign.Spacing.s)

            micButton
            sendButton
        }
        .padding(.horizontal, 6)
        .padding(.vertical, 6)
        .background(
            RoundedRectangle(cornerRadius: LokiDesign.Radius.input, style: .continuous)
                .fill(LokiDesign.Colors.inputFill(focused: focused))
        )
        .background(
            RoundedRectangle(cornerRadius: LokiDesign.Radius.input, style: .continuous)
                .fill(reduceTransparency ? AnyShapeStyle(Color(nsColor: .controlBackgroundColor)) : AnyShapeStyle(.ultraThinMaterial))
        )
        .overlay(
            RoundedRectangle(cornerRadius: LokiDesign.Radius.input, style: .continuous)
                .stroke(inputStroke, lineWidth: inputStrokeWidth)
        )
        .shadow(color: LokiDesign.Palette.cyan.color(opacity: focused && scheme == .dark ? 0.20 : 0), radius: 10)
        .scaleEffect(submitFlash ? 0.992 : 1)
        .animation(LokiDesign.Motion.focus, value: focused)
        .animation(LokiDesign.Motion.focus, value: submitFlash)
        .onAppear {
            // The panel becomes key shortly after the window grows; focus once it can accept it.
            Task {
                try? await Task.sleep(nanoseconds: 180_000_000)
                focused = true
            }
        }
        .onChange(of: controller.pendingQuestion) { question in
            if question != nil { focused = true }
        }
        .onChange(of: controller.inputFocusToken) { _ in
            focused = true
        }
    }

    private var isHighlighted: Bool { focused || submitFlash }

    /// The pastel focus gradient has too little contrast on a light surface or with Increase
    /// Contrast, so those use a solid ring (2 pt with Increase Contrast).
    private var inputStroke: AnyShapeStyle {
        guard isHighlighted else { return AnyShapeStyle(LokiDesign.Colors.hairline) }
        if contrast == .increased || scheme == .light { return AnyShapeStyle(LokiDesign.Colors.focusSolid) }
        return AnyShapeStyle(LokiDesign.Colors.focusGradient)
    }

    private var inputStrokeWidth: CGFloat {
        if contrast == .increased { return isHighlighted ? 2 : 1.5 }
        return isHighlighted ? 1.4 : 1
    }

    private var micButton: some View {
        let listening = controller.phase == .listening
        return Button {
            controller.toggleVoice()
        } label: {
            Image(systemName: listening ? "stop.fill" : "mic.fill")
                .font(LokiDesign.Typography.symbolControl)
                .foregroundColor(listening ? .white : LokiDesign.Colors.textSecondary)
                .frame(width: 30, height: 30)
                .background(
                    Circle().fill(
                        listening
                            ? AnyShapeStyle(LokiDesign.Colors.listeningFill)
                            : AnyShapeStyle(LokiDesign.Colors.surface)
                    )
                )
        }
        .buttonStyle(.plain)
        .help(listening ? "Stop and send (⌘⇧V)" : "Speak to LOKI (⌘⇧V)")
        .accessibilityLabel(listening ? "Stop listening and send" : "Start voice input")
    }

    private var sendButton: some View {
        let enabled = !trimmedEmpty
        return Button(action: submit) {
            Image(systemName: "arrow.up")
                .font(LokiDesign.Typography.symbolControl)
                .foregroundColor(enabled ? .white : LokiDesign.Colors.textTertiary)
                .frame(width: 30, height: 30)
                .background(
                    Circle().fill(
                        enabled
                            ? AnyShapeStyle(LokiDesign.Colors.accentGradient)
                            : AnyShapeStyle(LokiDesign.Colors.surface)
                    )
                )
                .scaleEffect(submitFlash ? 0.88 : 1)
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .help("Send (Return)")
        .accessibilityLabel("Send message")
        .animation(LokiDesign.Motion.focus, value: enabled)
    }

    private func submit() {
        guard !trimmedEmpty else { return }
        controller.submitText()
        flash.isOn = true
        Task {
            try? await Task.sleep(nanoseconds: 260_000_000)
            flash.isOn = false
        }
    }
}
