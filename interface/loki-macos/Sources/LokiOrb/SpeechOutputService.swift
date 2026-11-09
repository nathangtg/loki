import AVFoundation
import Foundation

final class SpeechOutputService: NSObject, AVSpeechSynthesizerDelegate, @unchecked Sendable {
    private let synthesizer = AVSpeechSynthesizer()
    private var selectedVoice: AVSpeechSynthesisVoice?
    private var meterTimer: Timer?
    private var currentWordEnergy: CGFloat = 0.0
    private var phase: Double = 0.0
    private var continuation: CheckedContinuation<Void, Never>?

    var onAmplitudeUpdate: ((CGFloat) -> Void)?
    var onSpeakingStateChanged: ((Bool) -> Void)?

    override init() {
        super.init()
        synthesizer.delegate = self
        selectedVoice = Self.pickBestVoice()
    }

    var isSpeaking: Bool {
        synthesizer.isSpeaking
    }

    @MainActor
    func speak(_ text: String) async {
        let cleaned = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleaned.isEmpty else { return }

        stop()

        let utterance = AVSpeechUtterance(string: cleaned)
        utterance.voice = selectedVoice ?? AVSpeechSynthesisVoice(language: "en-US")
        utterance.rate = 0.51
        utterance.pitchMultiplier = 1.03
        utterance.postUtteranceDelay = 0.02

        onSpeakingStateChanged?(true)
        startEnvelopeMeter()

        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            self.continuation = cont
            self.synthesizer.speak(utterance)
        }
    }

    func stop() {
        if synthesizer.isSpeaking || synthesizer.isPaused {
            synthesizer.stopSpeaking(at: .immediate)
        }
        finishSpeaking()
    }

    private func startEnvelopeMeter() {
        meterTimer?.invalidate()
        currentWordEnergy = 0.45
        phase = 0.0
        meterTimer = Timer.scheduledTimer(withTimeInterval: 1.0 / 60.0, repeats: true) { [weak self] _ in
            guard let self else { return }
            guard self.synthesizer.isSpeaking else {
                self.onAmplitudeUpdate?(0.0)
                return
            }
            self.phase += 0.28
            let wave1 = abs(sin(self.phase))
            let wave2 = abs(sin(self.phase * 1.7 + 0.9)) * 0.6
            let syllablePulse = CGFloat((wave1 + wave2) / 1.6)
            let level = min(1.0, max(0.12, self.currentWordEnergy * 0.65 + syllablePulse * 0.45))
            self.onAmplitudeUpdate?(level)
            self.currentWordEnergy = max(0.25, self.currentWordEnergy * 0.95)
        }
    }

    private func finishSpeaking() {
        meterTimer?.invalidate()
        meterTimer = nil
        currentWordEnergy = 0.0
        DispatchQueue.main.async { [weak self] in
            self?.onAmplitudeUpdate?(0.0)
            self?.onSpeakingStateChanged?(false)
        }
        if let cont = continuation {
            continuation = nil
            cont.resume()
        }
    }

    func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer,
        willSpeakRangeOfSpeechString characterRange: NSRange,
        utterance: AVSpeechUtterance
    ) {
        let nsStr = utterance.speechString as NSString
        guard NSMaxRange(characterRange) <= nsStr.length else { return }
        let word = nsStr.substring(with: characterRange)
        let vowelCount = word.lowercased().filter { "aeiouy".contains($0) }.count
        let energyBoost = min(1.0, 0.42 + CGFloat(vowelCount) * 0.14 + CGFloat(min(word.count, 8)) * 0.04)
        currentWordEnergy = energyBoost
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        finishSpeaking()
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        finishSpeaking()
    }

    private static func pickBestVoice() -> AVSpeechSynthesisVoice? {
        let voices = AVSpeechSynthesisVoice.speechVoices().filter {
            $0.language.hasPrefix("en")
        }
        if let premium = voices.first(where: { $0.quality == .premium }) {
            return premium
        }
        let preferredNames = ["Samantha", "Ava", "Allison", "Zoe", "Siri", "Tom", "Evan"]
        for name in preferredNames {
            if let enhanced = voices.first(where: { $0.name.localizedCaseInsensitiveContains(name) && $0.quality == .enhanced }) {
                return enhanced
            }
        }
        if let anyEnhanced = voices.first(where: { $0.quality == .enhanced }) {
            return anyEnhanced
        }
        for name in preferredNames {
            if let match = voices.first(where: { $0.name.localizedCaseInsensitiveContains(name) }) {
                return match
            }
        }
        return AVSpeechSynthesisVoice(language: "en-US")
    }
}
