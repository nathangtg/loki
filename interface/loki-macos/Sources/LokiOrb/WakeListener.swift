import AVFoundation
import Foundation
import LokiOrbCore
import Speech

/// Detects the "Hey Loki" wake phrase entirely on device.
///
/// A fresh `AVAudioEngine` is created per armed period. Tap buffers pass an RMS energy gate; only audio
/// at or above the threshold (plus a short hangover and pre-roll) reaches an on-device
/// `SFSpeechAudioBufferRecognitionRequest`. Audio is never written to disk.
@MainActor
final class WakeListener {
    enum State: Equatable {
        case stopped
        case starting
        case armed
        case retrying(reason: String)
        case failed(reason: String)
    }

    static let microphoneDeniedReason =
        "Microphone access is off. Allow Loki in System Settings → Privacy & Security → Microphone."
    static let speechDeniedReason =
        "Speech Recognition access is off. Allow Loki in System Settings → Privacy & Security → Speech Recognition."

    private(set) var state: State = .stopped {
        didSet { if state != oldValue { onStateChanged?(state) } }
    }

    var onWakePhraseDetected: (() -> Void)?
    var onStateChanged: ((State) -> Void)?

    private var engine: AVAudioEngine?
    private var pipeline: WakeAudioPipeline?
    private var recognizer: SFSpeechRecognizer?
    private var renewTimer: Timer?
    private var configObserver: NSObjectProtocol?
    /// Incremented on every start/stop so stale async continuations can detect cancellation.
    private var generation = 0

    /// Recognition tasks are renewed this often while armed (Apple ends long-running tasks).
    private static let renewInterval: TimeInterval = 55

    func start() {
        guard state == .stopped || isRetryingOrFailed else { return }
        generation += 1
        let current = generation
        state = .starting
        Task { [weak self] in
            await self?.startAsync(generation: current)
        }
    }

    func stop() {
        generation += 1
        teardown()
        state = .stopped
    }

    private var isRetryingOrFailed: Bool {
        switch state {
        case .retrying, .failed: return true
        default: return false
        }
    }

    // MARK: - Startup

    private func startAsync(generation current: Int) async {
        // Microphone first; Speech Recognition only after the microphone is granted.
        guard await Self.requestMicrophoneAccess() else {
            fail(Self.microphoneDeniedReason, generation: current)
            return
        }
        guard current == generation else { return }
        guard await Self.requestSpeechAccess() else {
            fail(Self.speechDeniedReason, generation: current)
            return
        }
        guard current == generation else { return }

        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US")),
            recognizer.isAvailable
        else {
            fail("Speech Recognition is not available right now.", generation: current)
            return
        }
        guard recognizer.supportsOnDeviceRecognition else {
            fail(
                "On-device Speech Recognition is not supported for English on this Mac.",
                generation: current)
            return
        }
        self.recognizer = recognizer

        let engine = AVAudioEngine()
        let format = engine.inputNode.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            fail("No valid microphone input available.", generation: current)
            return
        }

        let pipeline = WakeAudioPipeline(recognizer: recognizer) { [weak self] in
            Task { @MainActor [weak self] in self?.handleDetection(generation: current) }
        }
        engine.inputNode.removeTap(onBus: 0)
        engine.inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            pipeline.process(buffer)
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            engine.inputNode.removeTap(onBus: 0)
            fail(
                "Could not start the microphone: \(error.localizedDescription)", generation: current
            )
            return
        }

        self.engine = engine
        self.pipeline = pipeline
        configObserver = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in self?.handleUnexpectedStop(generation: current) }
        }
        renewTimer = Timer.scheduledTimer(withTimeInterval: Self.renewInterval, repeats: true) {
            [weak pipeline] _ in
            pipeline?.renewRecognition()
        }
        state = .armed
    }

    private func fail(_ reason: String, generation current: Int) {
        guard current == generation else { return }
        teardown()
        state = .failed(reason: reason)
    }

    // MARK: - Events

    private func handleDetection(generation current: Int) {
        guard current == generation, state == .armed else { return }
        // The engine stays armed unless the owner calls stop(); a fresh recognition session starts
        // lazily on the next loud buffer.
        pipeline?.renewRecognition()
        onWakePhraseDetected?()
    }

    private func handleUnexpectedStop(generation current: Int) {
        guard current == generation, state == .armed else { return }
        teardown()
        state = .retrying(reason: "The microphone stopped unexpectedly.")
    }

    // MARK: - Teardown

    private func teardown() {
        renewTimer?.invalidate()
        renewTimer = nil
        if let configObserver {
            NotificationCenter.default.removeObserver(configObserver)
            self.configObserver = nil
        }
        if let engine {
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
        }
        engine = nil
        pipeline?.shutdown()
        pipeline = nil
        recognizer = nil
    }

    // MARK: - Permissions

    private static func requestMicrophoneAccess() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return true
        case .notDetermined: return await AVCaptureDevice.requestAccess(for: .audio)
        default: return false
        }
    }

    private static func requestSpeechAccess() async -> Bool {
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized: return true
        case .notDetermined:
            return await withCheckedContinuation { continuation in
                SFSpeechRecognizer.requestAuthorization {
                    continuation.resume(returning: $0 == .authorized)
                }
            }
        default: return false
        }
    }
}

/// Audio-thread half of the listener: energy gate, bounded pre-roll, and the recognition session.
/// All state is guarded by `lock`; buffers are never persisted.
private final class WakeAudioPipeline: @unchecked Sendable {
    /// Keep feeding the recognizer this long after the level last reached the threshold.
    private static let hangover: TimeInterval = 1.0

    private let recognizer: SFSpeechRecognizer
    private let onMatch: () -> Void
    private let lock = NSLock()

    private var preRoll: [AVAudioPCMBuffer] = []
    private var preRollDurations = WakeAudioRingBuffer()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var hangoverRemaining: TimeInterval = 0
    private var isShutDown = false

    init(recognizer: SFSpeechRecognizer, onMatch: @escaping () -> Void) {
        self.recognizer = recognizer
        self.onMatch = onMatch
    }

    func process(_ buffer: AVAudioPCMBuffer) {
        guard let level = Self.rms(buffer), buffer.format.sampleRate > 0 else { return }
        let duration = Double(buffer.frameLength) / buffer.format.sampleRate

        lock.lock()
        defer { lock.unlock() }
        guard !isShutDown else { return }

        let open = WakeEnergyGate.shouldForward(level: level)
        if open { hangoverRemaining = Self.hangover }

        if request == nil && !open {
            // Gate closed and no active session: keep at most 2s of pre-roll, drop the rest.
            guard let copy = Self.copy(buffer) else { return }
            preRoll.append(copy)
            preRollDurations.append(durationSeconds: duration)
            while preRoll.count > preRollDurations.chunks.count { preRoll.removeFirst() }
            return
        }

        if request == nil { beginSession() }
        guard let request else { return }

        if hangoverRemaining > 0 {
            request.append(buffer)
            hangoverRemaining -= duration
            if hangoverRemaining <= 0 { endSession() }
        }
    }

    /// Ends the current recognition session; the next loud buffer starts a new one.
    func renewRecognition() {
        lock.lock()
        endSession()
        lock.unlock()
    }

    func shutdown() {
        lock.lock()
        isShutDown = true
        task?.cancel()
        task = nil
        request = nil
        preRoll.removeAll()
        preRollDurations.removeAll()
        lock.unlock()
    }

    // Must be called with `lock` held.
    private func beginSession() {
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = true
        if #available(macOS 13.0, *) { request.addsPunctuation = false }
        for buffered in preRoll { request.append(buffered) }
        preRoll.removeAll()
        preRollDurations.removeAll()
        self.request = request
        task = recognizer.recognitionTask(with: request) {
            [weak self, weak request] result, error in
            guard let self else { return }
            if let result, WakePhraseMatcher.matches(result.bestTranscription.formattedString) {
                self.lock.lock()
                let isCurrent = self.request === request
                self.lock.unlock()
                if isCurrent { self.onMatch() }
                return
            }
            if error != nil || result?.isFinal == true {
                self.lock.lock()
                if self.request === request { self.endSession() }
                self.lock.unlock()
            }
        }
    }

    // Must be called with `lock` held.
    private func endSession() {
        request?.endAudio()
        task?.cancel()
        request = nil
        task = nil
        hangoverRemaining = 0
    }

    private static func rms(_ buffer: AVAudioPCMBuffer) -> Float? {
        guard let data = buffer.floatChannelData else { return nil }
        let frames = Int(buffer.frameLength)
        let channels = Int(buffer.format.channelCount)
        guard frames > 0, channels > 0 else { return nil }
        var sumSquares: Float = 0
        for frame in 0..<frames {
            var sample: Float = 0
            for ch in 0..<channels { sample += data[ch][frame] }
            sample /= Float(channels)
            sumSquares += sample * sample
        }
        return sqrt(sumSquares / Float(frames))
    }

    private static func copy(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard
            let out = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameLength),
            let src = buffer.floatChannelData, let dst = out.floatChannelData
        else { return nil }
        out.frameLength = buffer.frameLength
        for ch in 0..<Int(buffer.format.channelCount) {
            dst[ch].update(from: src[ch], count: Int(buffer.frameLength))
        }
        return out
    }
}
