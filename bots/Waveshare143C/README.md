# Waveshare ESP32-S3-Touch-AMOLED-1.43C

ESP-IDF firmware for the round 466×466 AMOLED board with ES8311 speaker and ES7210 microphone. The robot face displays animated eyes only. Idle glances, blinks/winks, recording, thinking, and speaking poses run independently of audio at 20 fps. No status labels or BOOT hints are drawn.

## Build and flash

Use **ESP-IDF 5.5.1**, ESP32-S3, 8 MB flash and octal PSRAM. The checked-in `sdkconfig` and `partitions.csv` describe the tested board. Dependencies are vendored so component resolution does not change the build.

```sh
cd bots/Waveshare143C
IDF_COMPONENT_MANAGER=0 idf.py build
idf.py -p YOUR_SERIAL_PORT flash
```

Confirm the physical board identity and preserve its flash/settings before flashing. The custom partition table uses a 3 MiB factory application at `0x10000`; NVS remains at `0x9000` and PHY data at `0xf000`. For later app-only updates, retain the matching partition table. This is a development configuration with unencrypted NVS. Touch, OTA provisioning, and full-duplex speech interruption are outside this port.

The panel uses QSPI clock GPIO14, data GPIO9–12, CS15, reset13, and row gap8. Microphone data is GPIO42. Audio is 16 kHz stereo at the codecs and 16 kHz mono on the network.

## Pairing and hub configuration

Generate a random pairing token (for example, `python -c 'import secrets; print(secrets.token_hex(32))'`). Keep it out of source control. Set these in the hub process environment:

```dotenv
OMNIBOT_BOARD_TOKEN=<your-random-pairing-token>
OMNIBOT_BOARD_WAKE_MODEL=/absolute/path/to/your-model.onnx
OMNIBOT_BOARD_WAKE_PHRASE="hey Jillian"
# Optional: restrict all non-board HTTP/WebSocket routes to loopback.
# Leave unset to preserve existing Pixel/dashboard LAN behavior.
OMNIBOT_BOARD_LOCAL_ADMIN=1
```

Configure the device over its 115200-baud USB serial port by sending one newline-terminated JSON object:

```json
{"ssid":"YOUR_WIFI","password":"YOUR_WIFI_PASSWORD","url":"ws://YOUR_HUB:8000/ws/board","token":"YOUR_RANDOM_PAIRING_TOKEN"}
```

The settings persist in NVS; no rebuild is required to change hubs. The board adapter currently supports one paired identity, `waveshare-143c`. The WebSocket uses a shared token on a trusted LAN; do not expose an unencrypted `ws://` endpoint to the Internet. `OMNIBOT_BOARD_LOCAL_ADMIN=1` restricts *all* other routes, including existing Pixel sockets, to loopback. Use a suitable local proxy for the dashboard when enabling it.

Without a valid wake model, BOOT starts/finishes a recording. With a model, the Pi continuously checks microphone audio locally and forwards an utterance to Gemini only after activation. BOOT remains available and cancels thinking/playback. Recording ends after 600 ms of silence, or 15 seconds. After the device acknowledges drained playback, a three-second speech-start window allows a follow-up without another wake phrase; once speech begins, recording continues until the normal endpoint. Silence sends no request. Cancellation does not arm follow-up listening.

Speaker replies use stateful 24→16 kHz resampling, scheduled network packets with an 800 ms lead, and a 350 ms device prebuffer. The microphone uplink pauses during playback. The protocol bounds input/output sizes and rejects stale turns, duplicate identities, and bad credentials.

## Spoken voice selection

Ask the robot to list its voices, change its voice to Kore (or another Gemini prebuilt voice), or restore its default voice. The `voice_control` tool binds changes to the requesting device, saves `live_voice_name` atomically, and preserves other settings/history. The confirmation uses the existing voice; the next reply uses the selected voice. This changes the speaking voice, not the intelligence model.

## Wake-word training

[`scripts/wake_training/hey-jillian.ipynb`](../../scripts/wake_training/hey-jillian.ipynb) is a standalone Colab CPU recipe using openWakeWord and Piper. It uses the standard name pronunciation of **Jillian**, prefixed with **hey**. The target can be adapted to another phrase. The training data has non-commercial/personal-use restrictions; review the dataset terms before other uses. Trained weights and personal recordings are intentionally excluded from this repository.

The notebook isolates Python 3.12 dependencies, checks subprocess failures, exports ONNX with a hash/metrics manifest, and streams the complete overlapping validation set in batches instead of materializing one enormous tensor. This preserves validation coverage while fitting the free Colab RAM limit. Optional TFLite conversion stays disabled.

The tested starter model used 1,000 synthetic positive examples, 500 held-out positives, confusing-name negatives, 10,000 initial steps and two 1,000-step tuning passes. Its measured validation accuracy was 74.5%, recall 50%, and false positives 0.44/hour. All five independent synthetic positive checks triggered; six of seven negative checks stayed quiet. **“Hey Julian” also triggered.** These offline metrics do not guarantee performance in another room or for another speaker.

## Validation

From the repository root, after installing backend requirements plus `httpx`:

```sh
for test in app/backend/tests/test_*.py; do python "$test" || exit; done
python scripts/wake_training/test_bounded_validation.py
python -m compileall -q app/backend
```

Host C checks (Clang or GCC):

```sh
fw=bots/Waveshare143C
cc -fsanitize=address,undefined -I"$fw/main" "$fw/tests/test_face.c" "$fw/main/face_ui.c" "$fw/main/pixel_eyes.c" -lm -o /tmp/omnibot-face-test
/tmp/omnibot-face-test
cc -fsanitize=address,undefined -I"$fw/main" "$fw/tests/test_stream.c" "$fw/main/wake_stream.c" -lm -o /tmp/omnibot-stream-test
/tmp/omnibot-stream-test
cc -fsanitize=address,undefined -I"$fw/main" "$fw/tests/test_audio_process.c" "$fw/main/audio_process.c" -lm -o /tmp/omnibot-audio-test
/tmp/omnibot-audio-test
```

Physical acceptance on the 1.43C included clear microphone/speaker audio, smooth Pi replies, cancellation and the next request, automatic reconnection, wake activation, three-second spoken follow-ups, silent expiry, persistent audible voice changes, and animated eyes-only positioning. No long-duration soak or full Pi power-cycle acceptance is claimed.

## Attribution

Eye animation is adapted from OmniBot Pixel under the included `main/Pixel-LICENSE`. The panel driver retains its Apache-2.0 notice/license. Espressif codec/WebSocket sources retain their included notices. No private configuration, device recordings, provider keys, firmware backups, or compiled images are included.
