from __future__ import annotations

import logging
import platform
from pathlib import Path

from .constants import SCREENSHOTS_DIR, VALID_IMAGE_EXTENSIONS

LOGGER = logging.getLogger(__name__)


def latest_screenshot() -> Path | None:
    if not SCREENSHOTS_DIR.exists():
        return None
    candidates = [
        p
        for p in SCREENSHOTS_DIR.iterdir()
        if p.suffix.lower() in VALID_IMAGE_EXTENSIONS
    ]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def get_scale_factor(img_path: Path) -> float:
    if platform.system() != "Darwin":
        return 1.0
    try:
        import pyautogui
        from PIL import Image

        logical_w, _ = pyautogui.size()
        with Image.open(img_path) as img:
            physical_w, _ = img.size
        return 2.0 if physical_w > logical_w * 1.5 else 1.0
    except Exception:
        return 2.0


def run_ocr(img_path: Path) -> list[str]:
    try:
        from src.graph.llm import call_llm_vision

        raw = call_llm_vision(
            img_path=str(img_path),
            prompt=(
                "List every piece of visible text on this screen, one per line. "
                "Include menu items, buttons, labels, headings, body text, and "
                "status bar text. Output ONLY the text, no descriptions or "
                "commentary. Do not number the lines."
            ),
            system="You are a precise screen reader. Extract all visible text.",
            num_predict=2048,
            temperature=0.0,
        )
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        LOGGER.info("ocr: vision extracted %d text lines from %s", len(lines), img_path)
        return lines
    except Exception as exc:
        LOGGER.warning("ocr: vision-based OCR failed: %s", exc)
        return []
