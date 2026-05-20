// SettingsView.swift — paste the Groq API key.
//
// The key is stored in Keychain via Util/Keychain.swift, not Defaults.

import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var vm: ConversationVM
    @Environment(\.dismiss) private var dismiss
    @State private var draft: String = ""
    @State private var saved: Bool = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Groq API Key") {
                    SecureField("gsk_…", text: $draft)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button {
                        vm.saveApiKey(draft)
                        saved = true
                    } label: {
                        Text(draft.isEmpty ? "Clear" : "Save")
                    }
                    if saved {
                        Text("Saved.").font(.footnote).foregroundStyle(.green)
                    }
                }
                Section {
                    Link("Get a free key at console.groq.com",
                         destination: URL(string: "https://console.groq.com/keys")!)
                }
                Section("About") {
                    Text("v2 — iPhone-as-brain. Voice in via Apple Speech, " +
                         "LLM via Groq, voice out via iOS TTS, display via " +
                         "BLE to the Vyko Box-3.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                }
            }
            .onAppear { draft = vm.apiKey }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
