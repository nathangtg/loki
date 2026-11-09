from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CaptureResult:

    path: Path
    window_x: int
    window_y: int
    window_w: int
    window_h: int
    is_logical: bool
    scale_factor: float


def _get_frontmost_window_info() -> dict[str, Any] | None:
    if platform.system() != "Darwin":
        return None
    try:
        import Quartz
        import AppKit as _AppKit

        NSWorkspace = getattr(_AppKit, 'NSWorkspace')

        active = NSWorkspace.sharedWorkspace().activeApplication()
        if active is None:
            return None
        pid = int(active.get("NSApplicationProcessIdentifier", 0))
        if pid == 0:
            return None

        opts = (
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements
        )
        windows = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID)
        if not windows:
            return None

        for w in windows:
            if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer", 99) == 0:
                bounds = w.get("kCGWindowBounds", {})
                return {
                    "window_id": int(w["kCGWindowNumber"]),
                    "pid": pid,
                    "x": float(bounds.get("X", 0)),
                    "y": float(bounds.get("Y", 0)),
                    "width": float(bounds.get("Width", 0)),
                    "height": float(bounds.get("Height", 0)),
                    "owner": w.get("kCGWindowOwnerName", ""),
                }
    except Exception as exc:
        logger.debug("Could not get frontmost window info: %s", exc)
    return None


def downscale_to_logical(path: Path) -> float:
    try:
        import pyautogui
        from PIL import Image

        logical_w, _ = pyautogui.size()
        with Image.open(path) as img:
            physical_w, physical_h = img.size

        if physical_w <= logical_w * 1.5:
            return 1.0

        scale = physical_w / logical_w
        new_w = round(physical_w / scale)
        new_h = round(physical_h / scale)

        with Image.open(path) as img:
            resized = img.resize((new_w, new_h), Image.LANCZOS)
            resized.save(path)

        logger.debug(
            "Downscaled %s from %dx%d → %dx%d (%.1f×)",
            path.name,
            physical_w,
            physical_h,
            new_w,
            new_h,
            scale,
        )
        return scale
    except Exception as exc:
        logger.debug("Downscale failed, keeping original: %s", exc)
        return 1.0


def capture_focused_window(path: Path) -> CaptureResult | None:
    info = _get_frontmost_window_info()
    if info is None:
        return None

    wid = info["window_id"]

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["screencapture", "-x", "-o", "-l", str(wid), str(path)],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except Exception as exc:
        logger.debug("screencapture -l failed: %s", exc)
        return None

    if not path.exists():
        return None

    scale = downscale_to_logical(path)

    return CaptureResult(
        path=path,
        window_x=int(info["x"]),
        window_y=int(info["y"]),
        window_w=int(info["width"]),
        window_h=int(info["height"]),
        is_logical=scale > 1.0,
        scale_factor=1.0,
    )
