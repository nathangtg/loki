import Foundation

struct AcceptedJobResponse: Decodable {
    let status: String
    let jobId: String
    let transcription: String?

    enum CodingKeys: String, CodingKey {
        case status
        case jobId = "job_id"
        case transcription
    }
}

struct OrchestrateResultPayload: Decodable {
    let status: String
    let message: String?
    let spokenMessage: String?

    enum CodingKeys: String, CodingKey {
        case status
        case message
        case spokenMessage = "spoken_message"
    }
}

/// One entry of the server's per-job message log (`transcription`, `plan`, `thinking`, `step`,
/// `question`, `answer`).
struct JobMessage: Decodable {
    let type: String
    let content: String

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = (try? c.decode(String.self, forKey: .type)) ?? ""
        content = (try? c.decode(String.self, forKey: .content)) ?? ""
    }

    enum CodingKeys: String, CodingKey {
        case type
        case content
    }
}

struct JobPollResponse: Decodable {
    let status: String
    let result: OrchestrateResultPayload?
    let question: String?
    /// Full, append-only message log for the job. Older servers may omit it.
    let messages: [JobMessage]
    /// Top-level error text (e.g. "Job not found"), present on non-job error responses.
    let message: String?

    enum CodingKeys: String, CodingKey {
        case status
        case result
        case question
        case messages
        case message
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        status = try c.decode(String.self, forKey: .status)
        result = try? c.decodeIfPresent(OrchestrateResultPayload.self, forKey: .result)
        question = try? c.decodeIfPresent(String.self, forKey: .question)
        messages = (try? c.decodeIfPresent([JobMessage].self, forKey: .messages)) ?? []
        message = try? c.decodeIfPresent(String.self, forKey: .message)
    }
}

final class OrchestrationClient {
    private let baseURL: URL
    private let session: URLSession

    var baseURLString: String { baseURL.absoluteString }

    init() {
        let baseStr = ProcessInfo.processInfo.environment["LOKI_ORCHESTRATION_BASE"] ?? "http://127.0.0.1:9820"
        self.baseURL = URL(string: baseStr)!
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        self.session = URLSession(configuration: config)
    }

    func sendTextCommand(_ command: String) async throws -> AcceptedJobResponse {
        var req = URLRequest(url: baseURL.appendingPathComponent("assist"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("1", forHTTPHeaderField: "X-Loki-Client-TTS")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["command": command])

        let (data, _) = try await session.data(for: req)
        return try JSONDecoder().decode(AcceptedJobResponse.self, from: data)
    }

    func sendVoiceAudio(_ wavData: Data) async throws -> AcceptedJobResponse {
        var req = URLRequest(url: baseURL.appendingPathComponent("assist/audio"))
        req.httpMethod = "POST"
        req.setValue("audio/wav", forHTTPHeaderField: "Content-Type")
        req.setValue("1", forHTTPHeaderField: "X-Loki-Client-TTS")
        req.httpBody = wavData

        let (data, response) = try await session.data(for: req)
        if let http = response as? HTTPURLResponse, http.statusCode >= 400 {
            let errText = String(data: data, encoding: .utf8) ?? "Audio upload failed"
            throw NSError(domain: "LokiClient", code: http.statusCode, userInfo: [NSLocalizedDescriptionKey: errText])
        }
        return try JSONDecoder().decode(AcceptedJobResponse.self, from: data)
    }

    func pollResult(jobId: String, waitMs: Int = 400) async throws -> JobPollResponse {
        var comps = URLComponents(url: baseURL.appendingPathComponent("result/\(jobId)"), resolvingAgainstBaseURL: false)!
        if waitMs > 0 {
            comps.queryItems = [URLQueryItem(name: "wait_ms", value: String(waitMs))]
        }
        var req = URLRequest(url: comps.url!)
        req.timeoutInterval = 5
        let (data, _) = try await session.data(for: req)
        return try JSONDecoder().decode(JobPollResponse.self, from: data)
    }

    func sendReply(jobId: String, reply: String) async throws {
        var req = URLRequest(url: baseURL.appendingPathComponent("reply/\(jobId)"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["reply": reply])
        _ = try await session.data(for: req)
    }

    /// Read-only reachability check against `GET /health`.
    func checkHealth() async -> Bool {
        var req = URLRequest(url: baseURL.appendingPathComponent("health"))
        req.timeoutInterval = 5
        guard let (_, response) = try? await session.data(for: req),
              let http = response as? HTTPURLResponse
        else { return false }
        return (200..<300).contains(http.statusCode)
    }

    func stopServerSpeech() {
        var req = URLRequest(url: baseURL.appendingPathComponent("tts/stop"))
        req.httpMethod = "POST"
        req.timeoutInterval = 2
        session.dataTask(with: req).resume()
    }
}
