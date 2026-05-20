// SpeechIn.swift — push-to-talk speech-to-text via Apple's Speech framework.
//
// Uses SFSpeechRecognizer with on-device recognition where supported
// (iPhone 11+ for most locales). The session runs as long as the user
// holds the mic button; stop() returns the final transcript.

import Foundation
import Speech
import AVFoundation

@MainActor
final class SpeechIn: ObservableObject {

    @Published private(set) var partialTranscript: String = ""
    @Published private(set) var isListening: Bool = false

    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let audioEngine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?

    /// Request mic + speech-recognition permissions. Returns true if
    /// both are granted. ContentView should call this on first launch.
    static func requestPermissions() async -> Bool {
        let mic: Bool = await withCheckedContinuation { cont in
            AVAudioApplication.requestRecordPermission { ok in
                cont.resume(returning: ok)
            }
        }
        let speech: Bool = await withCheckedContinuation { cont in
            SFSpeechRecognizer.requestAuthorization { status in
                cont.resume(returning: status == .authorized)
            }
        }
        return mic && speech
    }

    /// Start a streaming recognition session. Throws if mic/recognizer
    /// not available. Updates `partialTranscript` continuously.
    func start() throws {
        guard let recognizer, recognizer.isAvailable else {
            throw SpeechError.unavailable
        }
        // Reset any prior session.
        try? stopAudioEngine()

        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .measurement, options: .duckOthers)
        try session.setActive(true, options: .notifyOthersOnDeactivation)

        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        if recognizer.supportsOnDeviceRecognition {
            req.requiresOnDeviceRecognition = true
        }
        self.request = req

        let input = audioEngine.inputNode
        let format = input.outputFormat(forBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buf, _ in
            self?.request?.append(buf)
        }

        audioEngine.prepare()
        try audioEngine.start()
        isListening = true
        partialTranscript = ""

        task = recognizer.recognitionTask(with: req) { [weak self] result, error in
            guard let self else { return }
            Task { @MainActor in
                if let result {
                    self.partialTranscript = result.bestTranscription.formattedString
                }
                if error != nil || (result?.isFinal ?? false) {
                    try? self.stopAudioEngine()
                }
            }
        }
    }

    /// Stop the current session and return the final transcript
    /// (whatever was last seen on partialTranscript).
    @discardableResult
    func stop() -> String {
        try? stopAudioEngine()
        let final = partialTranscript
        partialTranscript = ""
        return final
    }

    private func stopAudioEngine() throws {
        if audioEngine.isRunning {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
        }
        request?.endAudio()
        request = nil
        task?.cancel()
        task = nil
        try? AVAudioSession.sharedInstance().setActive(false,
                                                       options: .notifyOthersOnDeactivation)
        isListening = false
    }

    enum SpeechError: LocalizedError {
        case unavailable
        var errorDescription: String? { "Speech recognizer unavailable on this device." }
    }
}
