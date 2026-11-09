from __future__ import annotations

from pathlib import Path

_ORCHESTRATION_ROOT = Path(__file__).resolve().parents[3]

SCREENSHOTS_DIR = _ORCHESTRATION_ROOT / "data" / "screenshots"
SCREEN_MAP_PATH = _ORCHESTRATION_ROOT / "data" / "screen_map.json"
VALID_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg"})
MAX_CLICK_RETRIES = 3
CLICK_RETRY_DELAY = 0.1
MIN_ACCESSIBILITY_ELEMENTS = 3
