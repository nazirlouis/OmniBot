// ConversationVM.swift — orchestrates a full chat turn.
//
// Flow on each turn:
//   1. User holds mic button → SpeechIn.start()
//   2. User releases       → final transcript captured
//   3. Send chat[user] over BLE → Box-3 LCD shows it
//   4. Call Groq → AI reply text
//   5. Split reply on sentence boundaries
//   6. Send first chunk as chat[ai] (replace), rest as chat[ai]+append
//   7. Hand the cleaned reply to SpeechOut.speak()
//
// Markdown stage-directions like *waves hand* or `face_animation` get
// stripped before TTS (they sound weird) but kept in the LCD overlay
// so the user can read the model's full output.

import Foundation
import SwiftUI

@MainActor
final class ConversationVM: ObservableObject {

    // MARK: - Published state for UI

    @Published private(set) var transcript: [Turn] = []
    @Published private(set) var status: String = "Idle"
    @Published var apiKey: String = ""

    struct Turn: Identifiable, Equatable {
        let id = UUID()
        let role: String   // "user" | "ai"
        let text: String
    }

    // MARK: - Owned components

    let ble = VykoBLE()
    let stt = SpeechIn()
    let tts = SpeechOut()

    private var llm: GroqClient?

    // MARK: - Lifecycle

    /// Called from the App `.task`. Loads API key from Keychain, builds
    /// the LLM client, and ensures BLE starts scanning.
    func start() async {
        if let key = Keychain.get("groq_api_key"), !key.isEmpty {
            apiKey = key
            llm = GroqClient(apiKey: key)
        }
        _ = await SpeechIn.requestPermissions()
        ble.startScanning()
    }

    /// Called from SettingsView when the user pastes a new key.
    func saveApiKey(_ key: String) {
        let trimmed = key.trimmingCharacters(in: .whitespacesAndNewlines)
        Keychain.set(trimmed, forKey: "groq_api_key")
        apiKey = trimmed
        llm = trimmed.isEmpty ? nil : GroqClient(apiKey: trimmed)
    }

    // MARK: - Mic interaction

    func micPressed() {
        do {
            try stt.start()
            status = "Listening…"
        } catch {
            status = "Mic error: \(error.localizedDescription)"
        }
    }

    func micReleased() {
        let text = stt.stop().trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            status = "Idle"
            return
        }
        Task { await runTurn(userText: text) }
    }

    func stopSpeaking() {
        tts.stop()
        status = "Idle"
    }

    // MARK: - Core turn

    private func runTurn(userText: String) async {
        // 1. Push user text to LCD + UI.
        transcript.append(Turn(role: "user", text: userText))
        status = "Sending to Box…"
        do {
            try await ble.sendCommand(["type": "chat",
                                       "role": "user",
                                       "text": clampForBLE(userText)])
        } catch {
            status = "BLE: \(error.localizedDescription)"
            // Continue anyway — the AI reply still needs to happen.
        }

        // 2. LLM call.
        guard let llm else {
            status = "Set Groq API key in Settings"
            return
        }
        status = "Thinking…"
        let reply: String
        do {
            reply = try await llm.chat(userText)
        } catch {
            status = "LLM error: \(error.localizedDescription)"
            return
        }

        // 3. UI gets the full reply; LCD/TTS get cleaned variants.
        transcript.append(Turn(role: "ai", text: reply))
        let lcdText = reply           // Keep formatting on the LCD too.
        let spokenText = stripForTTS(reply)

        // 4. Chunk the LCD reply over BLE.
        status = "Sending reply…"
        let chunks = chunkSentencesForBLE(lcdText)
        for (i, chunk) in chunks.enumerated() {
            var payload: [String: Any] = [
                "type": "chat",
                "role": "ai",
                "text": chunk,
            ]
            if i > 0 { payload["append"] = true }
            do {
                try await ble.sendCommand(payload)
            } catch {
                // Don't abort the rest of the chunks — just log.
                print("[VM] chunk \(i) BLE write failed:", error)
            }
        }

        // 5. Speak.
        status = "Speaking…"
        tts.speak(spokenText)
    }

    // MARK: - Helpers

    /// BLE single-write text cap. With MTU 247 minus ATT/JSON overhead
    /// the safe text payload is ~200 chars. Used for the (rare) too-
    /// long user message — usually transcripts are short enough.
    private func clampForBLE(_ s: String) -> String {
        let limit = 200
        return s.count <= limit ? s : String(s.prefix(limit))
    }

    /// Split text on sentence boundaries (. ! ?), packing as many full
    /// sentences as fit under the per-chunk char limit. Falls back to
    /// hard splits if a single sentence exceeds the limit.
    func chunkSentencesForBLE(_ text: String) -> [String] {
        let perChunk = 200
        // Split keeping the punctuation on its sentence.
        let pattern = #"[^.!?\n]+[.!?\n]+|[^.!?\n]+$"#
        let regex = try! NSRegularExpression(pattern: pattern)
        let ns = text as NSString
        let matches = regex.matches(in: text, range: NSRange(location: 0, length: ns.length))
        let sentences: [String] = matches.compactMap {
            let s = ns.substring(with: $0.range).trimmingCharacters(in: .whitespaces)
            return s.isEmpty ? nil : s
        }
        if sentences.isEmpty { return [String(text.prefix(perChunk))] }

        var chunks: [String] = []
        var current = ""
        for s in sentences {
            // Hard-split absurdly long sentences.
            if s.count > perChunk {
                if !current.isEmpty { chunks.append(current); current = "" }
                var rest = s
                while rest.count > perChunk {
                    chunks.append(String(rest.prefix(perChunk)))
                    rest = String(rest.dropFirst(perChunk))
                }
                if !rest.isEmpty { current = rest }
                continue
            }
            if current.count + 1 + s.count > perChunk {
                chunks.append(current)
                current = s
            } else {
                if current.isEmpty { current = s }
                else { current += " " + s }
            }
        }
        if !current.isEmpty { chunks.append(current) }
        return chunks
    }

    /// Remove markdown stage directions / code spans before TTS so
    /// "*face_animation* hello" doesn't read literally as "asterisk
    /// face animation asterisk hello".
    private func stripForTTS(_ s: String) -> String {
        var out = s
        // *...* stage directions
        out = out.replacingOccurrences(of: #"\*[^*]+\*"#,
                                       with: "",
                                       options: .regularExpression)
        // `...` code spans
        out = out.replacingOccurrences(of: #"`[^`]+`"#,
                                       with: "",
                                       options: .regularExpression)
        // Collapse double-spaces left behind.
        out = out.replacingOccurrences(of: #"\s{2,}"#,
                                       with: " ",
                                       options: .regularExpression)
        return out.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
