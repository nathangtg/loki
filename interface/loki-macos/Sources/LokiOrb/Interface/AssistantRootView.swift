import SwiftUI

/// Root of the floating panel. Hosts either the compact bubble or the expanded conversation card;
/// the bubble itself is shared between them through a matched-geometry transition, so it travels
/// (rather than cross-fades) from the floating bubble to the header avatar and back.
struct AssistantRootView: View {
    @ObservedObject var controller: LokiController
    @Namespace private var bubbleSpace
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack(alignment: .bottom) {
            if controller.isExpanded {
                ExpandedCardView(controller: controller, bubbleSpace: bubbleSpace)
                    .padding(LokiDesign.Window.cardInset)
                    .transition(cardTransition)
            } else {
                CompactBubbleView(controller: controller, bubbleSpace: bubbleSpace)
                    .padding(.bottom, LokiDesign.Window.compactBottomPadding)
                    .transition(.opacity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
    }

    private var cardTransition: AnyTransition {
        if reduceMotion || LokiDesign.Motion.reduceMotion { return .opacity }
        return .scale(scale: 0.88, anchor: .bottom).combined(with: .opacity)
    }
}

// MARK: - Compact

/// The floating bubble with a caption describing the current activity.
struct CompactBubbleView: View {
    @ObservedObject var controller: LokiController
    let bubbleSpace: Namespace.ID
    @StateObject private var hover = LocalFlag()
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    var body: some View {
        let hovering = hover.isOn
        return VStack(spacing: 4) {
            if controller.phase != .idle {
                StatusCaption(phase: controller.phase, text: controller.statusLine)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }

            ZStack {
                BubbleView(model: controller.bubble)
                    .matchedGeometryEffect(id: "bubble", in: bubbleSpace)
                    .frame(width: LokiDesign.Bubble.compactFrame, height: LokiDesign.Bubble.compactFrame)
                    .contentShape(Circle().inset(by: LokiDesign.Bubble.compactFrame * 0.22))
                    .onTapGesture { controller.toggleVoice() }
                    .contextMenu {
                        Button("Open Conversation") { controller.setExpanded(true) }
                        Button(controller.phase == .listening ? "Stop Listening" : "Start Listening") {
                            controller.toggleVoice()
                        }
                        Divider()
                        Button("Reset Position") { controller.resetPosition() }
                    }
                    // One VoiceOver button for the bubble. The hover button below stays a separate element.
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("Loki")
                    .accessibilityValue(controller.statusLine)
                    .accessibilityHint("Starts or stops voice input. You can also drop text or files here to add them to your message.")
                    .accessibilityAddTraits(.isButton)
                    .accessibilityAction { controller.toggleVoice() }
                    .accessibilityAction(named: "Toggle voice input") { controller.toggleVoice() }
                    .accessibilityAction(named: "Open conversation") { controller.setExpanded(true) }

                Button {
                    controller.setExpanded(true)
                } label: {
                    Image(systemName: "text.bubble")
                        .font(LokiDesign.Typography.symbolControl)
                        .foregroundColor(LokiDesign.Colors.textPrimary)
                        .frame(width: 28, height: 28)
                        .background(
                            reduceTransparency
                                ? AnyShapeStyle(Color(nsColor: .windowBackgroundColor))
                                : AnyShapeStyle(.ultraThinMaterial),
                            in: Circle()
                        )
                        .overlay(Circle().stroke(LokiDesign.Colors.hairline, lineWidth: 1))
                }
                .buttonStyle(.plain)
                .offset(x: 70)
                // Invisible until hover for sighted users, but always in the accessibility tree and
                // activatable, so VoiceOver and keyboard users can reach it.
                .opacity(hovering ? 1 : 0)
                .accessibilityHidden(false)
                .accessibilityLabel("Open conversation")
                .help("Open conversation (⌘⇧Space)")
            }
            .frame(width: 200, height: LokiDesign.Bubble.compactFrame)
            .lokiDropTarget(
                controller: controller,
                highlight: Circle().inset(by: LokiDesign.Bubble.compactFrame * 0.2)
            )
            .onHover { inside in
                withAnimation(LokiDesign.Motion.focus) { hover.isOn = inside }
            }
        }
        .animation(LokiDesign.Motion.message, value: controller.phase)
    }
}

/// Small glass capsule under/over the bubble. Pairs a glyph and text with the color so state is
/// never conveyed by color alone.
struct StatusCaption: View {
    let phase: AssistantPhase
    let text: String
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: phase.symbol)
                .font(LokiDesign.Typography.symbolSmall)
                .foregroundColor(LokiDesign.Colors.tint(phase.tint))
            Text(text)
                .font(LokiDesign.Typography.captionStrong)
                .foregroundColor(LokiDesign.Colors.textPrimary)
                .lineLimit(1)
                .truncationMode(.tail)
        }
        .padding(.horizontal, 11)
        .padding(.vertical, 6)
        .frame(maxWidth: 290)
        .background(
            reduceTransparency
                ? AnyShapeStyle(Color(nsColor: .windowBackgroundColor))
                : AnyShapeStyle(.ultraThinMaterial),
            in: Capsule()
        )
        .background(Capsule().fill(reduceTransparency ? Color.clear : LokiDesign.Colors.captionFill))
        .overlay(Capsule().stroke(LokiDesign.Colors.hairline, lineWidth: 1))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(text)
    }
}
