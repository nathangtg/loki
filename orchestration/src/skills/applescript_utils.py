from __future__ import annotations

import subprocess


def run_applescript(script: str, *args: str, timeout: float = 15.0) -> str:
    try:
        cmd = ["osascript", "-e", script, *(str(a) for a in args)]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"AppleScript timed out after {timeout}s") from exc

    if result.returncode != 0:
        err = (result.stderr or "").strip() or "unknown AppleScript error"
        raise RuntimeError(f"AppleScript failed: {err}")

    return (result.stdout or "").strip()


def escape_as_string(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace('"', '\\"')
