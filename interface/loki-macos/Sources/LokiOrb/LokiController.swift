import AppKit
import Combine
import Foundation
import LokiOrbCore
import SwiftUI

struct ChatMessage: Identifiable, Equatable {
    enum Role: Equatable {
        case user
        case assistant
        /// A real backend progress entry (plan or executed step).
        case activity
        case error
    }

    let id = UUID()
    var role: Role
    var text: String
    /// True while text is still being revealed.
    var isStreaming = false
    /// Underlying error text, preserved for display.
    var detail: String? = nil
}

@MainActor
final class LokiController: ObservableObject {
    @Published private(set) var phase: AssistantPhase = .idle {
        didSet {
            guard phase != oldValue else { return }
            bubble.phase = phase
            settleTask?.cancel()
            settleTask = nil
            if phase == .idle {
                captureOrigin = .manual
                commandTimer = nil
                graceSecondsRemaining = 0
                startWakeListenerIfNeeded()
            } else if wakeListener.state != .stopped {
                // Not idle (capturing, working or speaking): the microphone must stay free.
                wakeListener.stop()
            }
        }
    }

    /// How the current voice capture was started.
    enum CaptureOrigin: Equatable {
        case manual
        case wake
    }
    private(set) var captureOrigin: CaptureOrigin = .manual
    /// Drives the Grace Window for a wake-triggered capture. Nil for manual captures.
    private var commandTimer: CommandCaptureTimer?
    private var graceStartedAt: TimeInterval?
    private let captureConfig = CommandCaptureConfig()
    /// Level (0...1, as produced by `AudioCaptureService`) at or above which a buffer counts as speech.
    private static let speechLevelThreshold: CGFloat = 0.12
    /// Whole seconds left in the Grace Window, shown in the status line and the cancel control.
    @Published private(set) var graceSecondsRemaining: Int = 0
    /// True while the wake listener is armed (mirrors `bubble.wakeListenerArmed`).
    @Published private(set) var wakeListenerArmed: Bool = false
    @Published private(set) var isExpanded: Bool = false
    @Published private(set) var messages: [ChatMessage] = []
    /// The action LOKI is performing right now, straight from the backend's step log.
    @Published private(set) var activityLabel: String? = nil
    @Published var inputText: String = ""
    @Published var pendingQuestion: String? = nil
    /// Incremented to ask the input field to take keyboard focus (menu: Focus Input).
    @Published private(set) var inputFocusToken = 0

    /// Drives the bubble; observed only by bubble views.
    let bubble = BubbleModel()
    let serverAddress: String

    private let audioCapture = AudioCaptureService()
    private let speechOutput = SpeechOutputService()
    private let client = OrchestrationClient()
    private let settings = LokiSettings.shared
    private let announcer = AccessibilityAnnouncer()
    private let wakeListener = WakeListener()
    private var settingsCancellables = Set<AnyCancellable>()
    /// Cue sounds are kept so they are not released while playing.
    private var cueSounds: [NSSound] = []
    private var activeJobId: String? = nil
    private var pollTask: Task<Void, Never>? = nil
    private var settleTask: Task<Void, Never>? = nil
    /// Pending single restart of the wake listener after an unexpected microphone stop.
    private var wakeRetryTask: Task<Void, Never>? = nil
    private var wakeRetryUsed = false
    private static let wakeRetryDelayNanoseconds: UInt64 = 5_000_000_000

    private static let maxMessages = 300

    var onFocusRequest: ((Bool) -> Void)?
    var onResetPosition: (() -> Void)?
    /// Opens the Settings window. Unset until a settings window exists; menus disable Settings then.
    var onShowSettings: (() -> Void)?

    var canShowSettings: Bool { onShowSettings != nil }

    /// True while Loki is speaking a reply aloud.
    var isSpeaking: Bool { speechOutput.isSpeaking }

    /// True while a command is in flight (not while merely listening).
    var isWorking: Bool {
        phase == .thinking || phase == .executing || phase == .responding || phase == .awaitingReply
    }

    /// One-line description of what is happening, shared by the header and the compact caption.
    var statusLine: String {
        switch phase {
        case .executing, .thinking:
            if let label = activityLabel, !label.isEmpty { return label }
            return phase.title
        case .error:
            return messages.last(where: { $0.role == .error })?.text ?? phase.title
        case .awaitingReply where !isExpanded:
            return "Loki has a question. Open the conversation (⌘⇧Space) to answer."
        case .confirming:
            return "Sending in \(max(1, graceSecondsRemaining))s. Cancel to stop."
        default:
            return phase.title
        }
    }

    init() {
        serverAddress = client.baseURLString

        audioCapture.onAmplitudeUpdate = { [weak self] level in
            guard let self, self.phase == .listening || self.phase == .confirming else { return }
            self.bubble.meter.push(level)
            self.feedCaptureTimer(level: level)
        }
        speechOutput.onAmplitudeUpdate = { [weak self] level in
            guard let self, self.phase == .responding else { return }
            self.bubble.meter.push(level)
        }
        speechOutput.onSpeakingStateChanged = { [weak self] speaking in
            guard let self else { return }
            if !speaking && self.phase == .responding {
                self.bubble.meter.reset()
                self.phase = self.pendingQuestion != nil ? .awaitingReply : .idle
            }
        }

        wakeListener.onWakePhraseDetected = { [weak self] in
            self?.handleWakeDetection()
        }
        wakeListener.onStateChanged = { [weak self] state in
            self?.handleWakeListenerState(state)
        }
        settings.$wakeWordEnabled
            .removeDuplicates()
            .sink { [weak self] enabled in
                self?.wakeWordSettingChanged(enabled)
            }
            .store(in: &settingsCancellables)
    }

    // MARK: - Wake word

    private func wakeWordSettingChanged(_ enabled: Bool) {
        if enabled {
            settings.wakeWordFailureReason = nil
            wakeRetryUsed = false
            startWakeListenerIfNeeded()
        } else {
            wakeRetryTask?.cancel()
            wakeRetryTask = nil
            wakeRetryUsed = false
            wakeListener.stop()
        }
    }

    private func startWakeListenerIfNeeded() {
        guard settings.wakeWordEnabled, phase == .idle else { return }
        wakeListener.start()
    }

    /// Acts on a detected wake phrase only while idle. The listener is stopped first so the
    /// microphone is released before `AudioCaptureService` starts.
    private func handleWakeDetection() {
        guard phase == .idle else { return }
        wakeListener.stop()
        captureOrigin = .wake
        startVoiceRecording()
    }

    private func handleWakeListenerState(_ state: WakeListener.State) {
        wakeListenerArmed = (state == .armed)
        bubble.wakeListenerArmed = wakeListenerArmed
        switch state {
        case .armed:
            wakeRetryTask?.cancel()
            wakeRetryTask = nil
            wakeRetryUsed = false
        case .retrying(let reason):
            guard !wakeRetryUsed else {
                disableWakeWord(reason: reason)
                return
            }
            wakeRetryTask?.cancel()
            wakeRetryTask = Task { [weak self] in
                try? await Task.sleep(nanoseconds: Self.wakeRetryDelayNanoseconds)
                guard !Task.isCancelled, let self else { return }
                self.wakeRetryTask = nil
                guard self.settings.wakeWordEnabled else { return }
                self.wakeRetryUsed = true
                if self.phase == .idle {
                    self.wakeListener.start()
                } else {
                    self.wakeListener.stop()
                }
            }
        case .failed(let reason):
            disableWakeWord(reason: reason)
        case .stopped, .starting:
            break
        }
    }

    /// Turns the setting off and records why, so Settings can explain it.
    private func disableWakeWord(reason: String) {
        wakeRetryTask?.cancel()
        wakeRetryTask = nil
        wakeRetryUsed = false
        settings.wakeWordFailureReason = reason
        settings.wakeWordEnabled = false
    }

    deinit {
        pollTask?.cancel()
        settleTask?.cancel()
        wakeRetryTask?.cancel()
    }

    // MARK: - Window / UI intents

    func toggleVoice() {
        bubble.kick()
        if speechOutput.isSpeaking {
            speechOutput.stop()
        }
        client.stopServerSpeech()

        if phase == .listening || phase == .confirming {
            if commandTimer != nil {
                // Wake-triggered capture: the hotkey submits immediately.
                sendCaptureEvent(.hotkeyOverride(at: Self.now))
            } else {
                stopAndSubmitVoice()
            }
        } else {
            if wakeListener.state != .stopped { wakeListener.stop() }
            captureOrigin = .manual
            commandTimer = nil
            startVoiceRecording()
        }
    }

    /// Cancels a wake-triggered capture or its Grace Window. Nothing is sent.
    func cancelGraceWindow() {
        guard commandTimer != nil else { return }
        sendCaptureEvent(.cancelRequested(at: Self.now))
    }

    private static var now: TimeInterval { ProcessInfo.processInfo.systemUptime }

    private func sendCaptureEvent(_ event: CaptureEvent) {
        guard var timer = commandTimer else { return }
        let outcome = timer.handle(event)
        commandTimer = timer
        applyCaptureOutcome(outcome)
    }

    private func feedCaptureTimer(level: CGFloat) {
        guard captureOrigin == .wake, commandTimer != nil else { return }
        sendCaptureEvent(.audioLevel(isSpeech: level >= Self.speechLevelThreshold, at: Self.now))
    }

    private func applyCaptureOutcome(_ outcome: CaptureOutcome) {
        switch outcome {
        case .continueCapturing:
            if phase == .confirming, let started = graceStartedAt {
                let left = Int(
                    (captureConfig.graceWindowDuration - (Self.now - started)).rounded(.up))
                let clamped = max(1, left)
                if clamped != graceSecondsRemaining { graceSecondsRemaining = clamped }
            }
        case .enteredGraceWindow:
            graceStartedAt = Self.now
            graceSecondsRemaining = max(1, Int(captureConfig.graceWindowDuration.rounded(.up)))
            phase = .confirming
            announcer.announce(
                "Sending in \(graceSecondsRemaining) seconds. Press Cancel or Escape to stop.",
                priority: .high)
        case .resumedCapturing:
            graceStartedAt = nil
            graceSecondsRemaining = 0
            phase = .listening
        case .submit:
            commandTimer = nil
            graceStartedAt = nil
            stopAndSubmitVoice()
        case .cancelled, .timedOutNoSpeech:
            abandonCapture()
        }
    }

    /// Discards the recorded audio without sending it and returns to idle.
    private func abandonCapture() {
        commandTimer = nil
        graceStartedAt = nil
        _ = audioCapture.stopRecording()
        _ = playListeningCue(named: "Pop")
        announcer.announce("Stopped listening")
        bubble.meter.reset()
        phase = .idle
    }

    func toggleExpanded() {
        setExpanded(!isExpanded)
    }

    func setExpanded(_ expanded: Bool) {
        guard expanded != isExpanded else {
            onFocusRequest?(expanded || pendingQuestion != nil)
            return
        }
        bubble.kick()
        withAnimation(LokiDesign.Motion.window(expanding: expanded)) {
            isExpanded = expanded
        }
        onFocusRequest?(expanded || pendingQuestion != nil)
    }

    /// Escape / collapse. Dismissing also drops an unanswered follow-up question.
    func dismissExpanded() {
        if commandTimer != nil {
            // Escape cancels a wake-triggered capture or its Grace Window.
            cancelGraceWindow()
            return
        }
        if pendingQuestion != nil {
            pendingQuestion = nil
            if phase == .awaitingReply { phase = .idle }
        }
        setExpanded(false)
        onFocusRequest?(false)
    }

    /// Stops spoken output (local and server side). The speech service reports the end of speech,
    /// which moves a `.responding` phase back to idle or awaiting-reply.
    func stopSpeaking() {
        speechOutput.stop()
        client.stopServerSpeech()
    }

    /// Opens the conversation if needed and moves keyboard focus to the input field.
    func focusInput() {
        if isExpanded {
            onFocusRequest?(true)
        } else {
            setExpanded(true)
        }
        inputFocusToken += 1
    }

    /// Adds dropped text or file paths to the input and opens the conversation with the field
    /// focused. The text is never sent: the user decides what to ask and presses Return.
    func appendToInput(_ text: String) {
        let addition = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !addition.isEmpty else { return }
        if inputText.isEmpty || inputText.last?.isWhitespace == true {
            inputText += addition
        } else {
            inputText += " " + addition
        }
        focusInput()
    }

    func showSettings() {
        onShowSettings?()
    }

    func clearConversation() {
        withAnimation(LokiDesign.Motion.message) {
            messages.removeAll()
        }
    }

    func resetPosition() {
        onResetPosition?()
    }

    // MARK: - Speech and announcements

    /// Loki's own voice stays off while VoiceOver is running, unless the user asked for both. Replies
    /// are then announced to VoiceOver and remain available as text in the conversation.
    private var speaksAloud: Bool {
        !(announcer.isVoiceOverRunning && settings.spokenRepliesWithVoiceOver == .letVoiceOverRead)
    }

    /// Speaks `text` unless VoiceOver is handling it. Returns when speech has finished (or at once
    /// when skipped); callers own the phase transition when nothing was spoken.
    private func speakIfAppropriate(_ text: String) async {
        guard speaksAloud else { return }
        await speechOutput.speak(text)
    }

    // MARK: - Voice

    /// How long the "Listening" announcement may take, so the microphone does not record it.
    private static let listeningAnnouncementAllowance: TimeInterval = 0.9

    private func startVoiceRecording() {
        do {
            announcer.beginJob()
            let announcesListening = announcer.isVoiceOverRunning
            var discard = playListeningCue(named: "Tink")
            if announcesListening {
                discard = max(discard, Self.listeningAnnouncementAllowance)
            }
            // Drop the start of the capture so the cue and any announcement are never sent as speech.
            try audioCapture.startRecording(discardingFirst: discard)
            bubble.meter.reset()
            activityLabel = nil
            commandTimer =
                captureOrigin == .wake
                ? CommandCaptureTimer(startedAt: Self.now, config: captureConfig) : nil
            graceStartedAt = nil
            phase = .listening
            announcer.announce("Listening", priority: .high)
        } catch {
            NSSound.beep()
            fail("Couldn't start the microphone.", detail: error.localizedDescription)
        }
    }

    private func stopAndSubmitVoice() {
        let recorded = audioCapture.stopRecording()
        _ = playListeningCue(named: "Pop")
        announcer.announce("Stopped listening")
        guard let wavData = recorded else {
            bubble.meter.reset()
            fail("No audio was captured.", detail: nil)
            return
        }
        bubble.meter.reset()
        activityLabel = nil
        phase = .thinking

        pollTask?.cancel()
        pollTask = Task {
            do {
                let accepted = try await client.sendVoiceAudio(wavData)
                activeJobId = accepted.jobId
                if let heard = accepted.transcription?.trimmingCharacters(
                    in: .whitespacesAndNewlines), !heard.isEmpty
                {
                    append(ChatMessage(role: .user, text: heard))
                }
                await pollUntilFinished(jobId: accepted.jobId, speakFinalReply: true)
            } catch {
                if Task.isCancelled { return }
                fail("Couldn't reach the Loki server.", detail: Self.describe(error))
                await speakIfAppropriate("Sorry, I couldn't reach the Loki server.")
            }
        }
    }

    /// Plays a built-in macOS sound when listening cues are enabled. Returns how many seconds of
    /// microphone input the cue may contaminate (0 when nothing was played).
    private func playListeningCue(named name: String) -> TimeInterval {
        guard settings.playListeningSounds, let sound = NSSound(named: NSSound.Name(name)) else {
            return 0
        }
        sound.volume = 0.4
        cueSounds.removeAll { !$0.isPlaying }
        cueSounds.append(sound)
        sound.play()
        return min(0.5, sound.duration + 0.1)
    }

    // MARK: - Text

    func submitText() {
        let trimmed = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        inputText = ""
        append(ChatMessage(role: .user, text: trimmed))
        bubble.kick()

        if pendingQuestion != nil, let jobId = activeJobId {
            pendingQuestion = nil
            speechOutput.stop()
            activityLabel = nil
            phase = .thinking
            Task {
                do {
                    try await client.sendReply(jobId: jobId, reply: trimmed)
                } catch {
                    fail("Your reply couldn't be delivered.", detail: Self.describe(error))
                }
            }
            return
        }

        speechOutput.stop()
        client.stopServerSpeech()
        announcer.beginJob()
        activityLabel = nil
        phase = .thinking

        pollTask?.cancel()
        pollTask = Task {
            do {
                let accepted = try await client.sendTextCommand(trimmed)
                activeJobId = accepted.jobId
                await pollUntilFinished(jobId: accepted.jobId, speakFinalReply: false)
            } catch {
                if Task.isCancelled { return }
                fail("Couldn't reach the Loki server.", detail: Self.describe(error))
            }
        }
    }

    // MARK: - Job lifecycle

    private func pollUntilFinished(jobId: String, speakFinalReply: Bool) async {
        var lastQuestion: String? = nil
        var seenMessages = 0
        var hasExecuted = false
        let deadline = Date().addingTimeInterval(300)
        var isFirstPoll = true

        while !Task.isCancelled && Date() < deadline {
            if !isFirstPoll {
                try? await Task.sleep(nanoseconds: 50_000_000)
            }
            isFirstPoll = false
            if Task.isCancelled { return }

            guard let poll = try? await client.pollResult(jobId: jobId, waitMs: 400) else {
                try? await Task.sleep(nanoseconds: 250_000_000)
                continue
            }

            // Real progress from the backend: plan, thinking and executed-step entries.
            if poll.messages.count > seenMessages {
                for entry in poll.messages[seenMessages...] {
                    if consume(entry) { hasExecuted = true }
                }
                seenMessages = poll.messages.count
            }

            if poll.question != lastQuestion {
                lastQuestion = poll.question
                if let q = lastQuestion, !q.isEmpty {
                    pendingQuestion = q
                    append(ChatMessage(role: .assistant, text: q))
                    if settings.openConversationForQuestions {
                        setExpanded(true)
                    }
                    announcer.announce(q, priority: .high)
                    if speaksAloud {
                        phase = .responding
                        await speechOutput.speak(q)
                    } else {
                        // Nothing is spoken, so no speech-end event will leave `.responding`.
                        bubble.meter.reset()
                        phase = .awaitingReply
                    }
                }
            }

            switch poll.status {
            case "running":
                if phase != .responding && phase != .listening && pendingQuestion == nil {
                    let busy: AssistantPhase = hasExecuted ? .executing : .thinking
                    if phase != busy { phase = busy }
                }
            case "done":
                pendingQuestion = nil
                let payload = poll.result
                if payload?.status == "error" {
                    await finishWithError(
                        payload?.message ?? "Something went wrong.", detail: nil,
                        speak: speakFinalReply
                    )
                    return
                }
                let display = payload?.message ?? payload?.spokenMessage ?? ""
                let spoken = payload?.spokenMessage ?? payload?.message ?? ""
                let delivered = await deliverReply(
                    display: display, spoken: spoken, speak: speakFinalReply)
                completeJob()
                // A reply announces itself; "Done" is only needed when there was no reply text.
                if !delivered { announcer.announce("Done") }
                return
            case "error":
                pendingQuestion = nil
                let errMsg = poll.result?.message ?? poll.message ?? "Something went wrong."
                await finishWithError(errMsg, detail: nil, speak: speakFinalReply)
                return
            default:
                completeJob(success: false)
                return
            }
        }
        if !Task.isCancelled {
            fail("Loki took too long to respond.", detail: "No result after 5 minutes.")
        }
    }

    /// Applies one backend log entry. Returns true when it marks the start of real execution.
    private func consume(_ entry: JobMessage) -> Bool {
        let content = entry.content.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !content.isEmpty else { return false }
        switch entry.type {
        case "plan":
            append(ChatMessage(role: .activity, text: content))
        case "thinking":
            if phase != .executing { activityLabel = Self.firstLine(content) }
        case "step":
            activityLabel = Self.firstLine(content)
            append(ChatMessage(role: .activity, text: content))
            announcer.announceStep(Self.firstLine(content))
            return true
        default:
            // `transcription` is shown from the accept response; `question` is handled via
            // `poll.question`; `answer` is ambiguous (user echo or chat reply) and the final
            // reply arrives in the result, so none of these are added twice.
            break
        }
        return false
    }

    /// Shows the final reply. Spoken replies keep the bubble in `.responding` for the whole
    /// utterance; silent replies are revealed progressively while the bubble responds.
    /// Returns whether there was reply text to show.
    @discardableResult
    private func deliverReply(display: String, spoken: String, speak: Bool) async -> Bool {
        let text = display.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return false }
        activityLabel = nil
        announcer.announce(text, priority: .high)

        // Without speech (VoiceOver is reading it) the reply is shown at once and `completeJob`
        // leaves `.responding`, since no speech-end event will arrive.
        let willSpeak = speak && speaksAloud
        if phase != .listening { phase = .responding }

        if speak {
            append(ChatMessage(role: .assistant, text: text))
            let toSpeak = spoken.trimmingCharacters(in: .whitespacesAndNewlines)
            if willSpeak, !toSpeak.isEmpty, phase == .responding {
                await speechOutput.speak(toSpeak)
            }
        } else {
            await reveal(text)
        }
        return true
    }

    /// Progressive reveal of an already-complete reply (the backend returns whole results, not
    /// tokens). Duration scales with length but is capped so it never makes the user wait.
    private func reveal(_ text: String) async {
        let words = text.split(separator: " ", omittingEmptySubsequences: false).map(String.init)
        var message = ChatMessage(role: .assistant, text: "", isStreaming: true)
        let id = message.id
        append(message)

        let duration = min(4.0, max(0.9, Double(words.count) * 0.045))
        let tick: UInt64 = 35_000_000
        let ticks = max(1, Int(duration / 0.035))
        let chunk = max(1, Int((Double(words.count) / Double(ticks)).rounded(.up)))

        var shown = 0
        while shown < words.count, !Task.isCancelled {
            shown = min(words.count, shown + chunk)
            message.text = words[0..<shown].joined(separator: " ")
            update(id) { $0.text = message.text }
            try? await Task.sleep(nanoseconds: tick)
        }
        update(id) {
            $0.text = text
            $0.isStreaming = false
        }
    }

    private func completeJob(success: Bool = true) {
        activityLabel = nil
        bubble.meter.reset()
        guard phase != .listening else { return }
        if success {
            phase = .completed
            bubble.celebrate()
            settle(after: LokiDesign.Motion.completedHold)
        } else {
            phase = .idle
        }
    }

    private func finishWithError(_ message: String, detail: String?, speak: Bool) async {
        fail(message, detail: detail)
        if speak {
            await speakIfAppropriate(message)
        }
    }

    /// Error feedback: the state is visible and the full error text stays in the conversation,
    /// but the bubble recovers to idle on its own.
    private func fail(_ message: String, detail: String?) {
        activityLabel = nil
        bubble.meter.reset()
        append(ChatMessage(role: .error, text: message, detail: detail))
        announcer.announce(message, priority: .high)
        if phase != .listening {
            phase = .error
            settle(after: LokiDesign.Motion.errorHold)
        }
    }

    /// Returns to idle after a hold, unless another state change happens first.
    private func settle(after seconds: TimeInterval) {
        settleTask?.cancel()
        settleTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
            guard !Task.isCancelled, let self else { return }
            self.phase = .idle
        }
    }

    // MARK: - Conversation helpers

    private func append(_ message: ChatMessage) {
        withAnimation(LokiDesign.Motion.message) {
            messages.append(message)
            if messages.count > Self.maxMessages {
                messages.removeFirst(messages.count - Self.maxMessages)
            }
        }
    }

    private func update(_ id: UUID, _ change: (inout ChatMessage) -> Void) {
        guard let index = messages.lastIndex(where: { $0.id == id }) else { return }
        change(&messages[index])
    }

    private static func firstLine(_ text: String) -> String {
        let line = text.split(whereSeparator: \.isNewline).first.map(String.init) ?? text
        return line.count > 90 ? String(line.prefix(88)) + "…" : line
    }

    private static func describe(_ error: Error) -> String {
        let ns = error as NSError
        return "\(ns.localizedDescription) (\(ns.domain) \(ns.code))"
    }
}
