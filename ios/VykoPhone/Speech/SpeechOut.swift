// SpeechOut.swift — iOS built-in TTS via AVSpeechSynthesizer.
//
// Free, on-device, instant. Quality is decent though not as natural as
// neural TTS (ElevenLabs / Piper). Once the rest of the v2 pipeline is
// stable we can swap this module for a cloud TTS client behind the same
// `speak(_:)` / `stop()` API.

import Foundation
import AVFoundation

@MainActor
final class SpeechOut: NSObject, ObservableObject {

    @Published private(set) var isSpeaking: Bool = false

    private let synth = AVSpeechSynthesizer()

    override init() {
        super.init()
        synth.delegate = self
    }

    /// Speak `text` immediately. Cancels any in-progress utterance so
    /// rapid back-to-back replies don't queue up.
    func speak(_ text: String) {
        guard !text.isEmpty else { return }
        // Configure audio session for playback over the speaker even if
        // a Bluetooth headset is paired but not in earpiece mode.
        try? AVAudioSession.sharedInstance()
            .setCategory(.playback, mode: .spokenAudio, options: .duckOthers)
        try? AVAudioSession.sharedInstance().setActive(true)

        if synth.isSpeaking {
            synth.stopSpeaking(at: .immediate)
        }
        let utt = AVSpeechUtterance(string: text)
        utt.voice = AVSpeechSynthesisVoice(language: "en-US")
        utt.rate = AVSpeechUtteranceDefaultSpeechRate
        utt.pitchMultiplier = 1.0
        utt.volume = 1.0
        synth.speak(utt)
    }

    /// Stop any current utterance immediately (the ⏹ button).
    func stop() {
        if synth.isSpeaking {
            synth.stopSpeaking(at: .immediate)
        }
    }
}

extension SpeechOut: AVSpeechSynthesizerDelegate {
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didStart utterance: AVSpeechUtterance) {
        Task { @MainActor in self.isSpeaking = true }
    }
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in self.isSpeaking = false }
    }
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in self.isSpeaking = false }
    }
}
