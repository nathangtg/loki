from __future__ import annotations

import logging
import subprocess
import time
from typing import Any

from langchain_core.tools import BaseTool

LOGGER = logging.getLogger(__name__)


class TypeTextTool(BaseTool):
    name: str = "TypeTextTool"
    description: str = (
        "Type literal characters using the keyboard into the currently focused element. "
        "The argument must be the actual text to type."
    )

    _FIELD_DESCRIPTIONS = [
        "the address bar",
        "the search bar",
        "the input field",
        "username field",
        "password field",
        "the username",
        "the password",
        "search field",
        "text field",
        "input box",
        "the url bar",
    ]

    def _run(self, input: Any) -> str:
        text = str(input).strip()
        if not text:
            return "Error: nothing to type."

        lower = text.lower()
        for desc in self._FIELD_DESCRIPTIONS:
            if lower == desc or lower == f"the {desc}":
                return (
                    f"Error: '{text}' looks like a field description, not text to type. "
                    "Focus the target field first, then call TypeTextTool with the value."
                )

        try:
            escaped = text.replace("\\", "\\\\").replace('"', '\\"')
            script = f'tell application "System Events" to keystroke "{escaped}"'
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return f"Error typing: {result.stderr.strip()}"
            return f"Typed: '{text}'"
        except Exception as exc:
            return f"Error typing text: {exc}"


class KeyComboTool(BaseTool):
    name: str = "KeyComboTool"
    description: str = (
        "Press a keyboard shortcut like 'command+t', 'enter', 'escape', 'command+shift+n'."
    )

    _MOD_APPLESCRIPT = {
        "command": "command down",
        "cmd": "command down",
        "shift": "shift down",
        "alt": "option down",
        "option": "option down",
        "ctrl": "control down",
        "control": "control down",
    }

    _KEY_CODES = {
        "return": 36,
        "enter": 36,
        "escape": 53,
        "esc": 53,
        "tab": 48,
        "space": 49,
        "delete": 51,
        "backspace": 51,
        "forwarddelete": 117,
        "up": 126,
        "down": 125,
        "left": 123,
        "right": 124,
        "f1": 122,
        "f2": 120,
        "f3": 99,
        "f4": 118,
        "f5": 96,
        "f6": 97,
        "f7": 98,
        "f8": 100,
        "f9": 101,
        "f10": 109,
        "f11": 103,
        "f12": 111,
    }

    def _run(self, input: Any) -> str:
        combo = str(input).strip().lower()
        if not combo:
            return "Error: no key combo provided."

        parts = [k.strip() for k in combo.split("+")]
        modifiers = []
        key_char = None

        for p in parts:
            if p in self._MOD_APPLESCRIPT:
                modifiers.append(self._MOD_APPLESCRIPT[p])
            else:
                key_char = p

        if key_char is None:
            return f"Error: no key found in combo '{combo}'."

        using_clause = ""
        if modifiers:
            using_clause = " using {" + ", ".join(modifiers) + "}"

        if key_char in self._KEY_CODES:
            script = f'tell application "System Events" to key code {self._KEY_CODES[key_char]}{using_clause}'
        else:
            script = f'tell application "System Events" to keystroke "{key_char}"{using_clause}'

        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return f"Error pressing {combo}: {result.stderr.strip()}"
            return f"Pressed: {combo}"
        except Exception as exc:
            return f"Error pressing keys: {exc}"


class WaitTool(BaseTool):
    name: str = "WaitTool"
    description: str = "Wait for a specified number of seconds (0.5 to 30)."

    def _run(self, input: Any) -> str:
        try:
            seconds = min(max(float(str(input).strip()), 0.5), 30.0)
        except (ValueError, TypeError):
            seconds = 2.0
        time.sleep(seconds)
        return f"Waited {seconds:.1f}s."


class ScrollTool(BaseTool):
    name: str = "ScrollTool"
    description: str = "Scroll the screen up or down by a given click count."

    def _run(self, input: Any) -> str:
        parts = str(input).strip().lower().split()
        direction = parts[0] if parts else "down"
        try:
            clicks = int(parts[1]) if len(parts) > 1 else 3
        except ValueError:
            clicks = 3

        amount = clicks if direction == "up" else -clicks
        try:
            import pyautogui

            pyautogui.scroll(amount)
            return f"Scrolled {direction} {clicks} clicks."
        except Exception as exc:
            return f"Error scrolling: {exc}"
