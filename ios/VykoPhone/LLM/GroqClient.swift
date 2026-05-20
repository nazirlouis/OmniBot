// GroqClient.swift — minimal OpenAI-compatible client for the Groq API.
//
// Groq exposes an OpenAI-compatible `/openai/v1/chat/completions`
// endpoint with very fast inference (~500 tokens/sec) and a free tier
// (~30 req/min) that's more than enough for casual chat. Default
// model: `llama-3.1-8b-instant` — quick, capable.
//
// Get an API key from https://console.groq.com/keys (free signup).
// The key is stored in iOS Keychain via Util/Keychain.swift, never in
// UserDefaults or source.

import Foundation

actor GroqClient {

    struct Message: Codable {
        let role: String     // "system" | "user" | "assistant"
        let content: String
    }

    private static let endpoint =
        URL(string: "https://api.groq.com/openai/v1/chat/completions")!

    /// Default model. Other free options on Groq:
    ///   - llama-3.3-70b-versatile   (slower, smarter)
    ///   - mixtral-8x7b-32768
    ///   - gemma2-9b-it
    var model: String = "llama-3.1-8b-instant"

    var apiKey: String

    /// Rolling chat history. Kept short — at ~6 turns we drop the oldest
    /// pair (user+assistant) to bound the prompt size and latency.
    private var history: [Message] = [
        .init(role: "system",
              content: """
              You are a helpful, concise voice assistant running on a small \
              hardware companion. Answer in 1–3 short sentences. Avoid \
              markdown, bullet points, code blocks, or stage directions \
              like *waves* — your reply is spoken aloud and shown on a \
              tiny LCD, so plain conversational prose only.
              """)
    ]

    init(apiKey: String) { self.apiKey = apiKey }

    /// Send `userText`, return the assistant's reply. Updates history.
    func chat(_ userText: String) async throws -> String {
        history.append(.init(role: "user", content: userText))
        trimHistory()

        var req = URLRequest(url: Self.endpoint)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")

        struct Body: Codable {
            let model: String
            let messages: [Message]
            let temperature: Double
            let max_tokens: Int
        }
        let body = Body(model: model,
                        messages: history,
                        temperature: 0.7,
                        max_tokens: 400)
        req.httpBody = try JSONEncoder().encode(body)

        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let snippet = String(data: data, encoding: .utf8)?.prefix(300) ?? ""
            throw NSError(domain: "GroqClient", code: -1, userInfo: [
                NSLocalizedDescriptionKey: "Groq HTTP \((resp as? HTTPURLResponse)?.statusCode ?? -1): \(snippet)"
            ])
        }

        struct GroqResp: Decodable {
            struct Choice: Decodable { let message: Message }
            let choices: [Choice]
        }
        let parsed = try JSONDecoder().decode(GroqResp.self, from: data)
        guard let reply = parsed.choices.first?.message.content,
              !reply.isEmpty else {
            throw NSError(domain: "GroqClient", code: -2, userInfo: [
                NSLocalizedDescriptionKey: "empty reply"
            ])
        }

        history.append(.init(role: "assistant", content: reply))
        trimHistory()
        return reply
    }

    /// Forget the conversation context (does not touch the system prompt).
    func resetHistory() {
        history = Array(history.prefix(1))
    }

    private func trimHistory() {
        // Keep system + last 6 user/assistant turns.
        let nonSystem = history.dropFirst()
        let maxPairs = 6
        if nonSystem.count > maxPairs * 2 {
            let keep = Array(nonSystem.suffix(maxPairs * 2))
            history = [history[0]] + keep
        }
    }
}
