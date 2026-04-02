"""Cross-platform Wi-Fi SSID listing (best-effort; often requires manual SSID entry)."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from typing import Any


def scan_wifi_ssids() -> dict[str, Any]:
    """Return ``{"networks": [...], "message": str|None}``."""
    plat = sys.platform
    if plat == "win32":
        return _scan_windows()
    if plat == "linux":
        return _scan_linux_nmcli()
    if plat == "darwin":
        return _scan_macos()
    return {
        "networks": [],
        "message": "Automatic Wi-Fi scan is not implemented for this platform; enter SSID manually.",
    }


def _scan_windows() -> dict[str, Any]:
    try:
        out = subprocess.run(
            ["netsh", "wlan", "show", "networks"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
        networks: list[str] = []
        for line in out.split("\n"):
            if "SSID" in line and ":" in line:
                ssid = line.split(":", 1)[1].strip()
                if ssid and ssid not in networks:
                    networks.append(ssid)
        return {"networks": networks, "message": None}
    except Exception as e:
        return {
            "networks": [],
            "message": f"Windows Wi-Fi scan failed ({e}). Enter SSID manually.",
        }


def _scan_linux_nmcli() -> dict[str, Any]:
    nmcli = shutil.which("nmcli")
    if not nmcli:
        return {
            "networks": [],
            "message": "Install NetworkManager (`nmcli`) for automatic SSID scan, or enter SSID manually.",
        }
    try:
        r = subprocess.run(
            [nmcli, "-t", "-f", "SSID", "dev", "wifi"],
            capture_output=True,
            text=True,
            timeout=45,
        )
        if r.returncode != 0:
            return {
                "networks": [],
                "message": (r.stderr or r.stdout or "nmcli failed").strip()[:200],
            }
        networks: list[str] = []
        for line in r.stdout.splitlines():
            s = line.strip()
            if s and s not in networks:
                networks.append(s)
        return {"networks": networks, "message": None}
    except Exception as e:
        return {
            "networks": [],
            "message": f"Linux Wi-Fi scan failed ({e}). Enter SSID manually.",
        }


def _scan_macos() -> dict[str, Any]:
    """Try ``airport -s`` if present; otherwise prompt for manual SSID."""
    airport = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
    import os

    if not os.path.isfile(airport):
        return {
            "networks": [],
            "message": "Automatic Wi-Fi scan unavailable. Enter your Wi-Fi network name (SSID) manually.",
        }
    try:
        r = subprocess.run(
            [airport, "-s"],
            capture_output=True,
            text=True,
            timeout=45,
        )
        if r.returncode != 0:
            return {
                "networks": [],
                "message": "Could not list Wi-Fi networks. Enter SSID manually.",
            }
        # First column is SSID (may contain spaces); airport uses fixed-ish columns — split on two+ spaces.
        networks: list[str] = []
        for line in r.stdout.splitlines()[1:]:
            line = line.rstrip()
            if not line:
                continue
            m = re.match(r"^(.+?)\s{2,}", line)
            if m:
                ssid = m.group(1).strip()
            else:
                ssid = line.split()[0] if line.split() else ""
            if ssid and ssid not in networks:
                networks.append(ssid)
        return {"networks": networks, "message": None if networks else "No networks found; enter SSID manually."}
    except Exception as e:
        return {
            "networks": [],
            "message": f"macOS Wi-Fi scan failed ({e}). Enter SSID manually.",
        }
