import AppKit
import SwiftUI

struct ConversationView: View {
    @ObservedObject var controller: LokiController

    private static let bottomID = "conversation-bottom"

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.vertical, showsIndicators: true) {
                if controller.messages.isEmpty {
                    EmptyConversation()
                        .frame(maxWidth: .infinity)
                        .padding(.top, 90)
                } else {
                    LazyVStack(alignment: .leading, spacing: LokiDesign.Spacing.m) {
                        ForEach(controller.messages) { message in
                            MessageRow(message: message)
                                .transition(.asymmetric(
                                    insertion: .move(edge: .bottom).combined(with: .opacity),
                                    removal: .opacity
                                ))
                        }
                        Color.clear.frame(height: 1).id(Self.bottomID)
                    }
                    .padding(.horizontal, LokiDesign.Spacing.l)
                    .padding(.vertical, LokiDesign.Spacing.m)
                    .accessibilityElement(children: .contain)
                    .accessibilityLabel("Conversation")
                }
            }
            .frame(maxHeight: .infinity)
            .onChange(of: controller.messages.count) { _ in
                withAnimation(LokiDesign.Motion.message) {
                    proxy.scrollTo(Self.bottomID, anchor: .bottom)
                }
            }
            // While a reply is being revealed, follow it without per-tick animation.
            .onChange(of: controller.messages.last?.text) { _ in
                if controller.messages.last?.isStreaming == true {
                    proxy.scrollTo(Self.bottomID, anchor: .bottom)
                }
            }
            .onAppear {
                proxy.scrollTo(Self.bottomID, anchor: .bottom)
            }
        }
    }
}

private struct EmptyConversation: View {
    var body: some View {
        VStack(spacing: 8) {
            Text("What can I do for you?")
                .font(LokiDesign.Typography.emptyTitle)
                .foregroundColor(LokiDesign.Colors.textPrimary)
            Text("Type below, or press ⌘⇧V to speak.")
                .font(LokiDesign.Typography.caption)
                .foregroundColor(LokiDesign.Colors.textTertiary)
        }
        .multilineTextAlignment(.center)
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Messages

struct MessageRow: View {
    let message: ChatMessage
    @Environment(\.accessibilityDifferentiateWithoutColor) private var differentiateWithoutColor

    var body: some View {
        switch message.role {
        case .user:
            HStack {
                Spacer(minLength: 56)
                VStack(alignment: .trailing, spacing: 3) {
                    if differentiateWithoutColor {
                        RoleLabel(symbol: "person.fill", title: "You")
                    }
                    Text(message.text)
                        .font(LokiDesign.Typography.body)
                        .foregroundColor(LokiDesign.Colors.textPrimary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(
                            RoundedRectangle(cornerRadius: LokiDesign.Radius.message, style: .continuous)
                                .fill(LokiDesign.Colors.userMessage)
                        )
                        .overlay(
                            RoundedRectangle(cornerRadius: LokiDesign.Radius.message, style: .continuous)
                                .stroke(LokiDesign.Colors.userMessageStroke, lineWidth: differentiateWithoutColor ? 1.5 : 1)
                        )
                }
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel("You: \(message.text)")

        case .assistant:
            HStack(alignment: .top, spacing: 10) {
                Capsule()
                    .fill(LokiDesign.Colors.accentGradient)
                    .frame(width: differentiateWithoutColor ? 3.5 : 2.5)
                    .opacity(0.85)
                VStack(alignment: .leading, spacing: 4) {
                    if differentiateWithoutColor {
                        RoleLabel(symbol: "sparkles", title: "Loki")
                    }
                    if message.text.isEmpty {
                        Text(" ").font(LokiDesign.Typography.body)
                    } else {
                        MarkdownContent(text: message.text)
                    }
                    if message.isStreaming {
                        Circle()
                            .fill(LokiDesign.Colors.tint(LokiDesign.Palette.cyan))
                            .frame(width: 6, height: 6)
                            .shadow(color: LokiDesign.Palette.cyan.color(opacity: 0.8), radius: 4)
                            .accessibilityHidden(true)
                    }
                }
                Spacer(minLength: 20)
            }
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityElement(children: .combine)
            .accessibilityLabel("LOKI: \(message.text)")

        case .activity:
            ActivityRow(text: message.text)

        case .error:
            ErrorRow(message: message)
        }
    }
}

/// Small "icon + name" tag that tells speakers apart without relying on color.
private struct RoleLabel: View {
    let symbol: String
    let title: String

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: symbol)
                .font(LokiDesign.Typography.symbolTiny)
            Text(title)
                .font(LokiDesign.Typography.label)
        }
        .foregroundColor(LokiDesign.Colors.textSecondary)
        .accessibilityHidden(true)
    }
}

/// A real progress entry from the backend (plan or executed step), deliberately quiet.
private struct ActivityRow: View {
    let text: String

    var body: some View {
        let lines = text.split(whereSeparator: \.isNewline).map(String.init)
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: lines.count > 1 ? "list.bullet" : "chevron.right")
                .font(LokiDesign.Typography.symbolTiny)
                .foregroundColor(LokiDesign.Colors.tint(LokiDesign.Palette.cyan, opacity: 0.9))
                .frame(width: 10)
            VStack(alignment: .leading, spacing: 2) {
                ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                    Text(line)
                        .font(LokiDesign.Typography.caption)
                        .foregroundColor(LokiDesign.Colors.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(.leading, 12)
        .accessibilityElement(children: .combine)
    }
}

private struct ErrorRow: View {
    let message: ChatMessage
    @StateObject private var detail = LocalFlag()
    @Environment(\.accessibilityDifferentiateWithoutColor) private var differentiateWithoutColor

    var body: some View {
        let showDetail = detail.isOn
        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(LokiDesign.Typography.symbolSmall)
                    .foregroundColor(LokiDesign.Colors.tint(LokiDesign.Palette.ember))
                    .accessibilityHidden(true)
                if differentiateWithoutColor {
                    Text("Error")
                        .font(LokiDesign.Typography.label)
                        .foregroundColor(LokiDesign.Colors.textPrimary)
                        .accessibilityHidden(true)
                }
                Text(message.text)
                    .font(LokiDesign.Typography.body)
                    .foregroundColor(LokiDesign.Colors.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            if let detailText = message.detail, !detailText.isEmpty {
                Button {
                    withAnimation(LokiDesign.Motion.message) { detail.isOn.toggle() }
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: showDetail ? "chevron.down" : "chevron.right")
                            .font(LokiDesign.Typography.symbolTiny)
                        Text(showDetail ? "Hide details" : "Details")
                            .font(LokiDesign.Typography.caption)
                    }
                    .foregroundColor(LokiDesign.Colors.textSecondary)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(showDetail ? "Hide error details" : "Show error details")

                if showDetail {
                    Text(detailText)
                        .font(LokiDesign.Typography.code)
                        .foregroundColor(LokiDesign.Colors.textSecondary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: LokiDesign.Radius.message, style: .continuous)
                .fill(LokiDesign.Colors.errorTint)
        )
        .overlay(
            RoundedRectangle(cornerRadius: LokiDesign.Radius.message, style: .continuous)
                .stroke(
                    LokiDesign.Colors.errorStroke,
                    style: StrokeStyle(lineWidth: differentiateWithoutColor ? 1.5 : 1, dash: differentiateWithoutColor ? [5, 3] : [])
                )
        )
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Error: \(message.text)")
    }
}

// MARK: - Markdown

/// Lightweight Markdown: fenced code blocks, headings, bullets and inline styles (bold, italic,
/// code, links). Plain text stays plain.
struct MarkdownContent: View {
    let text: String

    private enum Block {
        case prose(String)
        case heading(String)
        case code(language: String, body: String)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(Self.parse(text).enumerated()), id: \.offset) { _, block in
                switch block {
                case .prose(let s):
                    Text(Self.inline(s))
                        .font(LokiDesign.Typography.body)
                        .foregroundColor(LokiDesign.Colors.textPrimary)
                        .lineSpacing(3)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                case .heading(let s):
                    Text(Self.inline(s))
                        .font(LokiDesign.Typography.heading)
                        .foregroundColor(LokiDesign.Colors.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                case .code(let language, let body):
                    CodeBlockView(language: language, code: body)
                }
            }
        }
    }

    private static func inline(_ s: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(
            interpretedSyntax: .inlineOnlyPreservingWhitespace
        )
        return (try? AttributedString(markdown: s, options: options)) ?? AttributedString(s)
    }

    private static func parse(_ text: String) -> [Block] {
        var blocks: [Block] = []
        var prose: [String] = []
        var code: [String] = []
        var language = ""
        var inFence = false

        func flushProse() {
            let joined = prose.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty { blocks.append(.prose(joined)) }
            prose.removeAll()
        }

        for line in text.components(separatedBy: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("```") {
                if inFence {
                    blocks.append(.code(language: language, body: code.joined(separator: "\n")))
                    code.removeAll()
                    inFence = false
                } else {
                    flushProse()
                    language = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                    inFence = true
                }
                continue
            }
            if inFence {
                code.append(line)
            } else if trimmed.hasPrefix("#") {
                flushProse()
                let title = trimmed.drop(while: { $0 == "#" }).trimmingCharacters(in: .whitespaces)
                if !title.isEmpty { blocks.append(.heading(title)) }
            } else if trimmed.hasPrefix("- ") || trimmed.hasPrefix("* ") {
                prose.append("•  " + trimmed.dropFirst(2))
            } else {
                prose.append(line)
            }
        }
        // An unterminated fence is still shown as code, so streaming never flashes raw backticks.
        if inFence { blocks.append(.code(language: language, body: code.joined(separator: "\n"))) }
        flushProse()
        return blocks
    }
}

struct CodeBlockView: View {
    let language: String
    let code: String
    @StateObject private var copyFlag = LocalFlag()

    var body: some View {
        let copied = copyFlag.isOn
        return VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(language.isEmpty ? "code" : language)
                    .font(LokiDesign.Typography.label)
                    .foregroundColor(LokiDesign.Colors.textTertiary)
                Spacer()
                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(code, forType: .string)
                    copyFlag.isOn = true
                    Task {
                        try? await Task.sleep(nanoseconds: 1_400_000_000)
                        copyFlag.isOn = false
                    }
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: copied ? "checkmark" : "doc.on.doc")
                        Text(copied ? "Copied" : "Copy")
                    }
                    .font(LokiDesign.Typography.label)
                    .foregroundColor(LokiDesign.Colors.textSecondary)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(copied ? "Copied" : "Copy code")
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)

            ScrollView(.horizontal, showsIndicators: true) {
                Text(code)
                    .font(LokiDesign.Typography.code)
                    .foregroundColor(LokiDesign.Colors.textPrimary)
                    .textSelection(.enabled)
                    .fixedSize()
                    .padding(.horizontal, 10)
                    .padding(.bottom, 10)
            }
        }
        .background(
            RoundedRectangle(cornerRadius: LokiDesign.Radius.code, style: .continuous)
                .fill(LokiDesign.Colors.codeSurface)
        )
        .overlay(
            RoundedRectangle(cornerRadius: LokiDesign.Radius.code, style: .continuous)
                .stroke(LokiDesign.Colors.hairline, lineWidth: 1)
        )
    }
}
