// ContentView.swift — main screen.
//
// Single screen, push-to-talk paradigm:
//   - Connection status chip up top (BLE state)
//   - Scrollable transcript in the middle
//   - Big round mic button at the bottom (hold to talk, release to send)
//   - Settings gear to set / paste the Groq API key

import SwiftUI

struct ContentView: View {
    @EnvironmentObject var vm: ConversationVM
    @State private var showSettings = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                StatusBar()
                    .padding(.horizontal)
                    .padding(.top, 8)

                Transcript()
                    .padding(.horizontal)

                MicArea()
                    .padding(.bottom, 24)
            }
            .navigationTitle("Vyko")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: {
                        Image(systemName: "gearshape")
                    }
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsView()
                    .environmentObject(vm)
            }
        }
    }
}

// MARK: - Subviews

private struct StatusBar: View {
    @EnvironmentObject var vm: ConversationVM

    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(dotColor)
                .frame(width: 10, height: 10)
            Text(bleLabel)
                .font(.footnote)
                .foregroundStyle(.secondary)
            Spacer()
            Text(vm.status)
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 12)
        .background(.regularMaterial, in: Capsule())
    }

    private var dotColor: Color {
        switch vm.ble.state {
        case .connected: return .green
        case .connecting, .scanning: return .orange
        default: return .red
        }
    }

    private var bleLabel: String {
        switch vm.ble.state {
        case .poweredOff: return "Bluetooth off"
        case .unauthorized: return "Bluetooth not allowed"
        case .idle: return "BLE idle"
        case .scanning: return "Scanning for VYKO…"
        case .connecting(let name): return "Connecting \(name)…"
        case .connected(let name): return "Connected · \(name)"
        }
    }
}

private struct Transcript: View {
    @EnvironmentObject var vm: ConversationVM

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    if vm.transcript.isEmpty {
                        Text("Hold the mic and say something.")
                            .foregroundStyle(.secondary)
                            .padding(.top, 80)
                            .frame(maxWidth: .infinity, alignment: .center)
                    }
                    ForEach(vm.transcript) { turn in
                        TurnView(turn: turn).id(turn.id)
                    }
                }
                .padding(.vertical, 12)
            }
            .onChange(of: vm.transcript.count) { _, _ in
                if let last = vm.transcript.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
    }
}

private struct TurnView: View {
    let turn: ConversationVM.Turn

    var body: some View {
        HStack {
            if turn.role == "user" { Spacer(minLength: 40) }
            VStack(alignment: turn.role == "user" ? .trailing : .leading, spacing: 2) {
                Text(turn.role == "user" ? "You" : "AI")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Text(turn.text)
                    .padding(10)
                    .background(turn.role == "user" ? Color.accentColor.opacity(0.18) : Color.gray.opacity(0.15),
                                in: RoundedRectangle(cornerRadius: 14))
            }
            if turn.role == "ai" { Spacer(minLength: 40) }
        }
    }
}

private struct MicArea: View {
    @EnvironmentObject var vm: ConversationVM
    @GestureState private var pressed: Bool = false

    var body: some View {
        VStack(spacing: 16) {
            // Stop-speech (⏹) button — only visible while speaking
            if vm.tts.isSpeaking {
                Button { vm.stopSpeaking() } label: {
                    Image(systemName: "stop.circle.fill")
                        .font(.system(size: 32))
                        .foregroundStyle(.red)
                }
            }

            Circle()
                .fill(pressed ? Color.red : Color.accentColor)
                .frame(width: 120, height: 120)
                .overlay(
                    Image(systemName: pressed ? "waveform" : "mic.fill")
                        .font(.system(size: 44))
                        .foregroundStyle(.white)
                )
                .shadow(radius: pressed ? 12 : 4)
                .scaleEffect(pressed ? 1.05 : 1.0)
                .animation(.easeInOut(duration: 0.12), value: pressed)
                .gesture(
                    DragGesture(minimumDistance: 0)
                        .updating($pressed) { _, state, _ in state = true }
                        .onChanged { _ in
                            if !vm.stt.isListening { vm.micPressed() }
                        }
                        .onEnded { _ in vm.micReleased() }
                )

            Text(pressed ? "Listening…" : "Hold to talk")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }
}
