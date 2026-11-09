from __future__ import annotations

import logging
import subprocess
from typing import Any

from src.skills.applescript_utils import run_applescript
from src.skills.base import Skill, SkillIntent
from src.skills.registry import register_skill

LOGGER = logging.getLogger(__name__)

_SETTINGS_PANE_URLS: dict[str, str] = {
    "wifi": "x-apple.systempreferences:com.apple.wifi-settings-extension",
    "wi-fi": "x-apple.systempreferences:com.apple.wifi-settings-extension",
    "network": "x-apple.systempreferences:com.apple.Network-Settings.extension",
    "bluetooth": "x-apple.systempreferences:com.apple.BluetoothSettings",
    "sound": "x-apple.systempreferences:com.apple.Sound-Settings.extension",
    "audio": "x-apple.systempreferences:com.apple.Sound-Settings.extension",
    "displays": "x-apple.systempreferences:com.apple.Displays-Settings.extension",
    "display": "x-apple.systempreferences:com.apple.Displays-Settings.extension",
    "wallpaper": "x-apple.systempreferences:com.apple.Wallpaper-Settings.extension",
    "battery": "x-apple.systempreferences:com.apple.Battery-Settings.extension",
    "keyboard": "x-apple.systempreferences:com.apple.Keyboard-Settings.extension",
    "trackpad": "x-apple.systempreferences:com.apple.Trackpad-Settings.extension",
    "notifications": "x-apple.systempreferences:com.apple.Notifications-Settings.extension",
    "focus": "x-apple.systempreferences:com.apple.Focus-Settings.extension",
    "privacy": "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension",
    "security": "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension",
    "accessibility": "x-apple.systempreferences:com.apple.Accessibility-Settings.extension",
    "appearance": "x-apple.systempreferences:com.apple.Appearance-Settings.extension",
    "general": "x-apple.systempreferences:com.apple.systempreferences.GeneralSettings",
}


def _open_settings_pane(params: dict[str, Any]) -> str:
    raw_pane = (params.get("pane") or "").strip().lower()
    if not raw_pane:
        raise ValueError("Missing 'pane' parameter")

    uri = _SETTINGS_PANE_URLS.get(raw_pane)
    if uri is None:
        for key, candidate_uri in _SETTINGS_PANE_URLS.items():
            if key in raw_pane or raw_pane in key:
                uri = candidate_uri
                break

    if uri is None:
        uri = "x-apple.systempreferences:"

    res = subprocess.run(
        ["/usr/bin/open", uri],
        capture_output=True,
        text=True,
        timeout=5.0,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"Failed to open System Settings pane '{raw_pane}'")
    return f"Opened System Settings pane '{raw_pane}'."


def _get_system_volume(params: dict[str, Any]) -> str:
    script = (
        "set vs to get volume settings\n"
        "return (output volume of vs as string) & \"|\" & (output muted of vs as string)"
    )
    raw = run_applescript(script)
    parts = [p.strip() for p in raw.split("|")]
    vol = parts[0] if parts else "?"
    muted = parts[1] if len(parts) > 1 else "false"
    return f"System output volume is {vol}% (muted: {muted})."


def _set_system_volume(params: dict[str, Any]) -> str:
    raw_level = params.get("level") if params.get("level") is not None else params.get("volume")
    if raw_level is None:
        raise ValueError("Missing 'level' parameter (0-100)")
    try:
        level = max(0, min(100, int(float(str(raw_level).replace("%", "").strip()))))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid volume level: {raw_level}") from exc

    run_applescript(f"set volume output volume {level}")
    return f"Set macOS system volume to {level}%."


def _set_mute(params: dict[str, Any]) -> str:
    raw_muted = str(params.get("muted", "true")).strip().lower()
    is_muted = raw_muted in ("true", "1", "yes", "on", "mute")
    val_str = "true" if is_muted else "false"
    run_applescript(f"set volume output muted {val_str}")
    return "System audio muted." if is_muted else "System audio unmuted."


def _toggle_dark_mode(params: dict[str, Any]) -> str:
    raw_state = str(params.get("state", "toggle")).strip().lower()
    if raw_state in ("on", "true", "dark", "enable", "enabled"):
        target_expr = "true"
    elif raw_state in ("off", "false", "light", "disable", "disabled"):
        target_expr = "false"
    else:
        target_expr = "not dark mode"

    script = f'''
    tell application "System Events"
        tell appearance preferences
            set dark mode to {target_expr}
            return dark mode as string
        end tell
    end tell
    '''
    new_state = run_applescript(script).strip().lower()
    mode_label = "Dark Mode" if new_state == "true" else "Light Mode"
    return f"Switched macOS appearance to {mode_label}."


def _get_clipboard(params: dict[str, Any]) -> str:
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeString

        pb = NSPasteboard.generalPasteboard()
        content = pb.stringForType_(NSPasteboardTypeString)
        text = str(content) if content is not None else ""
    except Exception:
        res = subprocess.run(["/usr/bin/pbpaste"], capture_output=True, text=True, timeout=3.0, check=False)
        text = res.stdout or ""

    if not text.strip():
        return "Clipboard is currently empty."
    return f"Clipboard content ({len(text)} chars):\n{text[:1500]}"


def _set_clipboard(params: dict[str, Any]) -> str:
    text = str(params.get("text") or "")
    if not text:
        raise ValueError("Missing 'text' parameter for set_clipboard")
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeString

        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)
    except Exception:
        subprocess.run(["/usr/bin/pbcopy"], input=text, text=True, timeout=3.0, check=True)
    return f"Copied {len(text)} character(s) to the macOS clipboard."


def _get_network_status(params: dict[str, Any]) -> str:
    wifi_line = "Wi-Fi status unavailable"
    try:
        res = subprocess.run(
            ["/usr/sbin/networksetup", "-getairportnetwork", "en0"],
            capture_output=True,
            text=True,
            timeout=4.0,
            check=False,
        )
        if res.returncode == 0 and (res.stdout or "").strip():
            wifi_line = res.stdout.strip()
    except Exception:
        pass

    ip_line = ""
    try:
        res_ip = subprocess.run(
            ["/usr/sbin/ipconfig", "getifaddr", "en0"],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        if res_ip.returncode == 0 and (res_ip.stdout or "").strip():
            ip_line = f" | Local IP (en0): {res_ip.stdout.strip()}"
    except Exception:
        pass

    return f"{wifi_line}{ip_line}"


_system_control_skill = Skill(
    name="system_control",
    description=(
        "Control macOS System Settings panes, system audio volume/mute, "
        "Dark Mode appearance, NSPasteboard clipboard, and Wi-Fi network status"
    ),
    app_names={
        "system settings",
        "system settings.app",
        "system preferences",
        "settings",
        "volume",
        "dark mode",
        "appearance",
        "clipboard",
        "wi-fi",
        "wifi",
        "bluetooth",
    },
)

_system_control_skill.add_intent(
    SkillIntent(
        name="open_settings_pane",
        description="Open a specific macOS System Settings pane (wifi, bluetooth, sound, displays, battery, privacy, accessibility, keyboard, appearance)",
        params=["pane"],
        handler=_open_settings_pane,
    )
)
_system_control_skill.add_intent(
    SkillIntent(
        name="get_system_volume",
        description="Get the current macOS system audio output volume and mute status",
        params=[],
        handler=_get_system_volume,
    )
)
_system_control_skill.add_intent(
    SkillIntent(
        name="set_system_volume",
        description="Set the macOS system audio output volume (0 to 100)",
        params=["level"],
        handler=_set_system_volume,
    )
)
_system_control_skill.add_intent(
    SkillIntent(
        name="set_mute",
        description="Mute or unmute macOS system audio ('true' or 'false')",
        params=["muted"],
        handler=_set_mute,
    )
)
_system_control_skill.add_intent(
    SkillIntent(
        name="toggle_dark_mode",
        description="Toggle or set macOS Dark Mode ('on', 'off', or 'toggle')",
        params=[],
        handler=_toggle_dark_mode,
    )
)
_system_control_skill.add_intent(
    SkillIntent(
        name="get_clipboard",
        description="Read the current text contents of the macOS system clipboard",
        params=[],
        handler=_get_clipboard,
    )
)
_system_control_skill.add_intent(
    SkillIntent(
        name="set_clipboard",
        description="Copy the given text string to the macOS system clipboard",
        params=["text"],
        handler=_set_clipboard,
    )
)
_system_control_skill.add_intent(
    SkillIntent(
        name="get_network_status",
        description="Get the current Wi-Fi network name (SSID) and local IP address on en0",
        params=[],
        handler=_get_network_status,
    )
)

register_skill(_system_control_skill)
