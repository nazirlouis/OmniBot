# VykoPhone — iPhone-as-brain iOS app (v2)

The iOS half of the v2 architecture: iPhone captures voice, calls Groq for
the LLM reply, speaks the answer through the iPhone speaker, and writes
chat overlays over BLE to the Vyko Box-3 so the LCD still shows the
conversation. No Mac, no Wi-Fi for the Box-3.

## What's in this folder

Just Swift source files — there is **no Xcode project committed** yet.
You'll create the Xcode project once and drag these sources in.

```
VykoPhone/
├── VykoPhoneApp.swift              # @main entry point
├── BLE/VykoBLE.swift               # CoreBluetooth client
├── Speech/SpeechIn.swift           # SFSpeechRecognizer (mic → text)
├── Speech/SpeechOut.swift          # AVSpeechSynthesizer (text → speaker)
├── LLM/GroqClient.swift            # Groq API client
├── ViewModel/ConversationVM.swift  # full turn orchestration
├── UI/ContentView.swift            # main screen
├── UI/SettingsView.swift           # API key entry
└── Util/Keychain.swift             # API key persistence
```

## One-time Xcode setup

1. **Open Xcode** → File → New → Project → **iOS App**.
2. Settings:
   - Product Name: `VykoPhone`
   - Team: your free Apple ID (sign in via Xcode → Settings → Accounts if needed)
   - Bundle Identifier: `com.<yourname>.vykophone`
   - Interface: **SwiftUI**
   - Language: **Swift**
   - Storage: **None**, Tests: off
   - Minimum deployment: **iOS 17.0**
3. Save the project **anywhere** (NOT this folder — Xcode creates lots of files).
4. In the new project, delete Xcode's auto-generated `ContentView.swift` and `<ProductName>App.swift`.
5. In Finder, drag the `VykoPhone/` folder from this repo into the Xcode project navigator.
   - In the "Add Files" dialog: **Create groups**, **Copy items if needed** off.
6. **Add usage description keys** to the project's Info tab (Signing & Capabilities → Info):
   - `NSBluetoothAlwaysUsageDescription` → "Vyko uses Bluetooth to talk to the Box-3 display."
   - `NSMicrophoneUsageDescription` → "Vyko uses the mic to capture your voice for the assistant."
   - `NSSpeechRecognitionUsageDescription` → "Vyko transcribes your speech on-device."
7. **Signing**: pick your Personal Team. The bundle identifier may need a unique suffix on first run.
8. Plug in a real iPhone (BLE doesn't work in the Simulator), select it from Xcode's destination dropdown, hit ⌘R to build & run.

## First run

1. Allow Bluetooth, Microphone, and Speech permissions when prompted.
2. Tap the **gear icon** → paste your Groq API key from
   <https://console.groq.com/keys> (free tier — no credit card).
3. Watch the status pill at the top — it should go
   *Scanning for VYKO…* → *Connecting VYKO_44BE…* → *Connected · VYKO_44BE*.
4. **Hold** the big mic button, speak a sentence, release. Within ~1 s the
   Box-3 LCD should show "You: …" and the iPhone should speak the AI reply
   while the LCD fills in "AI: …" sentence by sentence.

## Wire protocol (matches firmware)

The app writes JSON to the Box-3's BLE Command characteristic:

| Field | Type | Notes |
|---|---|---|
| `type` | `"chat"` | Always `chat` for this path |
| `role` | `"user"` \| `"ai"` | Which line on the LCD to update |
| `text` | string | Up to ~210 chars per write |
| `append` | bool (optional) | `false` = replace (default), `true` = append for chunks |

Firmware reference: `firmware/main/command_parser/command_parser.c::handle_chat`.

## Troubleshooting

- **"Scanning for VYKO…" forever**: Box-3 not advertising. Power-cycle it. Also
  ensure no other device is currently connected to it (firmware allows one BLE
  central at a time).
- **Build error "No such module 'Speech'"**: target deployment is < iOS 17, bump
  it to 17.0 in the project's General tab.
- **Groq returns 401**: API key invalid or revoked. Generate a new one and
  paste it via Settings.
- **App expires after 7 days**: that's the free-Apple-ID sideload limit. Plug
  in and rebuild from Xcode to renew. A $99/yr paid Developer account removes
  this.
