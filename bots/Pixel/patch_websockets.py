# PlatformIO pre-script: Links2004 WebSockets.h uses unconditional
# `#define WEBSOCKETS_MAX_DATA_SIZE (15 * 1024)` for ESP32, which overrides
# `-DWEBSOCKETS_MAX_DATA_SIZE=...` from build_flags and triggers redefinition
# warnings. Wrap the 15KB default so the command-line value wins.
Import("env")
from pathlib import Path


def apply_patch():
    root = Path(env["PROJECT_DIR"])
    header = root / ".pio" / "libdeps" / env["PIOENV"] / "WebSockets" / "src" / "WebSockets.h"
    if not header.is_file():
        print("patch_websockets: WebSockets.h not found yet (first lib install?); skip.")
        return

    needle = "#define WEBSOCKETS_MAX_DATA_SIZE (15 * 1024)"
    replacement = (
        "#ifndef WEBSOCKETS_MAX_DATA_SIZE\n"
        "#define WEBSOCKETS_MAX_DATA_SIZE (15 * 1024)\n"
        "#endif"
    )

    text = header.read_text(encoding="utf-8", errors="replace")
    if replacement in text:
        return
    if needle not in text:
        print("patch_websockets: expected define not found; skip.")
        return

    header.write_text(text.replace(needle, replacement), encoding="utf-8")
    print("patch_websockets: patched", header)


apply_patch()
