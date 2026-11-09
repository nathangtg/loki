from __future__ import annotations

import platform
import subprocess
from pathlib import Path


def capture_screen(path: Path) -> str | None:
    if platform.system() == "Darwin":
        try:
            subprocess.run(
                ["screencapture", "-x", str(path)],
                check=True,
                capture_output=True,
                timeout=10,
            )
            if path.exists():
                return None
        except Exception:
            pass

    try:
        import mss
    except ImportError:
        return "Error: mss is not installed. Run 'pip install mss'."
    try:
        with mss.mss() as sct:
            sct.shot(output=str(path))
    except Exception as exc:
        return f"Error capturing screenshot: {exc}"
    return None
