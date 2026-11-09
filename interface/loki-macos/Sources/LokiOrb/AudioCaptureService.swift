import AVFoundation
import Foundation

final class AudioCaptureService {
    private static let targetSampleRate: Double = 16_000
    private let engine = AVAudioEngine()
    private let lock = NSLock()
    private var samples: [Float] = []
    private var inputSampleRate: Double = 16_000
    private var isRecording = false
    /// Frames still to drop from the start of a recording (see `startRecording(discardingFirst:)`).
    private var framesToDiscard = 0

    var onAmplitudeUpdate: ((CGFloat) -> Void)?

    /// - Parameter discardingFirst: seconds of audio to drop from the start of the recording, so a
    ///   cue sound played just before capture begins is not sent to the server as speech.
    func startRecording(discardingFirst seconds: TimeInterval = 0) throws {
        lock.lock()
        if isRecording {
            lock.unlock()
            return
        }
        samples.removeAll(keepingCapacity: true)
        isRecording = true
        lock.unlock()

        let inputNode = engine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw NSError(
                domain: "LokiAudioCapture",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "No valid microphone input format available"]
            )
        }
        inputSampleRate = format.sampleRate
        lock.lock()
        framesToDiscard = max(0, Int(seconds * format.sampleRate))
        lock.unlock()

        inputNode.removeTap(onBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            self?.processBuffer(buffer)
        }

        engine.prepare()
        try engine.start()
    }

    func stopRecording() -> Data? {
        lock.lock()
        guard isRecording else {
            lock.unlock()
            return nil
        }
        isRecording = false
        lock.unlock()

        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        DispatchQueue.main.async { [weak self] in
            self?.onAmplitudeUpdate?(0.0)
        }

        lock.lock()
        let captured = samples
        let sourceRate = inputSampleRate
        samples.removeAll(keepingCapacity: false)
        lock.unlock()

        guard !captured.isEmpty else { return nil }
        let resampled = Self.resampleTo16kHz(samples: captured, sourceRate: sourceRate)
        return Self.encodePCM16WAV(samples: resampled, sampleRate: Int(Self.targetSampleRate))
    }

    private static func resampleTo16kHz(samples: [Float], sourceRate: Double) -> [Float] {
        guard sourceRate > 0, abs(sourceRate - targetSampleRate) > 1.0 else {
            return samples
        }
        let ratio = sourceRate / targetSampleRate
        let outputLength = max(1, Int(Double(samples.count) / ratio))
        var output = [Float](repeating: 0, count: outputLength)
        let filterRadius = max(1, Int(ceil(ratio)))

        for i in 0..<outputLength {
            let center = Double(i) * ratio
            let startIdx = max(0, Int(floor(center)) - filterRadius)
            let endIdx = min(samples.count - 1, Int(ceil(center)) + filterRadius)
            var sum: Float = 0
            var weightSum: Float = 0
            for j in startIdx...endIdx {
                let dist = abs(Double(j) - center) / ratio
                let weight = Float(max(0.0, 1.0 - dist))
                sum += samples[j] * weight
                weightSum += weight
            }
            output[i] = weightSum > 0 ? (sum / weightSum) : samples[min(samples.count - 1, Int(center))]
        }
        return output
    }

    private func processBuffer(_ buffer: AVAudioPCMBuffer) {
        guard let channelData = buffer.floatChannelData else { return }
        let frameLength = Int(buffer.frameLength)
        let channels = Int(buffer.format.channelCount)
        guard frameLength > 0, channels > 0 else { return }

        var monoChunk = [Float](repeating: 0, count: frameLength)
        var sumSquares: Float = 0

        for frame in 0..<frameLength {
            var sample: Float = 0
            for ch in 0..<channels {
                sample += channelData[ch][frame]
            }
            sample /= Float(channels)
            monoChunk[frame] = sample
            sumSquares += sample * sample
        }

        let rms = sqrt(sumSquares / Float(frameLength))
        let normalized = min(1.0, max(0.0, CGFloat(rms * 6.5)))

        lock.lock()
        if isRecording {
            let dropped = min(framesToDiscard, monoChunk.count)
            framesToDiscard -= dropped
            samples.append(contentsOf: dropped > 0 ? monoChunk[dropped...] : monoChunk[...])
        }
        lock.unlock()

        DispatchQueue.main.async { [weak self] in
            self?.onAmplitudeUpdate?(normalized)
        }
    }

    private static func encodePCM16WAV(samples: [Float], sampleRate: Int) -> Data {
        let numSamples = samples.count
        let byteRate = sampleRate * 2
        let dataSize = numSamples * 2
        let riffSize = 36 + dataSize

        var data = Data(capacity: 44 + dataSize)
        data.append(contentsOf: "RIFF".utf8)
        data.append(littleEndianUInt32(UInt32(riffSize)))
        data.append(contentsOf: "WAVE".utf8)
        data.append(contentsOf: "fmt ".utf8)
        data.append(littleEndianUInt32(16))
        data.append(littleEndianUInt16(1))
        data.append(littleEndianUInt16(1))
        data.append(littleEndianUInt32(UInt32(sampleRate)))
        data.append(littleEndianUInt32(UInt32(byteRate)))
        data.append(littleEndianUInt16(2))
        data.append(littleEndianUInt16(16))
        data.append(contentsOf: "data".utf8)
        data.append(littleEndianUInt32(UInt32(dataSize)))

        for sample in samples {
            let clamped = max(-1.0, min(1.0, sample))
            let intSample = Int16(clamped * 32767.0)
            var le = intSample.littleEndian
            withUnsafeBytes(of: &le) { data.append(contentsOf: $0) }
        }
        return data
    }

    private static func littleEndianUInt32(_ value: UInt32) -> Data {
        var le = value.littleEndian
        return Data(bytes: &le, count: 4)
    }

    private static func littleEndianUInt16(_ value: UInt16) -> Data {
        var le = value.littleEndian
        return Data(bytes: &le, count: 2)
    }
}
