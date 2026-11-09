from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .constants import (
    SCREEN_MAP_PATH,
    SCREENSHOTS_DIR,
    VALID_IMAGE_EXTENSIONS,
)


def coerce_input(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("input", "path", "value"):
            if key in value and isinstance(value[key], str):
                return value[key].strip()
        return json.dumps(value)
    return str(value).strip()


def timestamped_screenshot_path() -> Path:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    return SCREENSHOTS_DIR / f"screenshot_{ts}.png"


def safe_screenshot_path(raw: str) -> Path:
    path = Path(raw)
    if path.suffix.lower() not in VALID_IMAGE_EXTENSIONS:
        path = path.with_suffix(".png")
    if path.parent == Path("."):
        path = SCREENSHOTS_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def validate_coordinates(x: Any, y: Any) -> tuple[int, int]:
    try:
        xi, yi = int(x), int(y)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Coordinates must be integers, got x={x!r}, y={y!r}") from exc
    if xi < 0 or yi < 0:
        raise ValueError(f"Coordinates must be non-negative, got ({xi}, {yi})")
    return xi, yi


def save_screen_map(screen_map: dict[str, Any]) -> None:
    try:
        SCREEN_MAP_PATH.write_text(
            json.dumps(screen_map, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        raise RuntimeError(f"Failed to save screen map: {exc}") from exc


def load_screen_map() -> dict[str, Any]:
    if not SCREEN_MAP_PATH.exists():
        return {}
    try:
        with SCREEN_MAP_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Failed to load screen map: {exc}") from exc


def debug_log(message: str) -> None:
    try:
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        with (SCREENSHOTS_DIR / "screenshot_tool_debug.log").open(
            "a", encoding="utf-8"
        ) as fh:
            fh.write(f"{datetime.now().isoformat()} {message}\n")
    except Exception:
        pass
