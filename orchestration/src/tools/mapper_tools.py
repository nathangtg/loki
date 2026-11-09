from __future__ import annotations

import difflib
import json
import logging
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool
from src.memory import SessionMemory

from .utils import (
    CLICK_RETRY_DELAY,
    MAX_CLICK_RETRIES,
    VALID_IMAGE_EXTENSIONS,
    build_locator_tree,
    capture_screen,
    coerce_input,
    debug_log,
    get_all_locators,
    latest_screenshot,
    load_screen_map,
    run_ocr,
    safe_screenshot_path,
    save_screen_map,
    timestamped_screenshot_path,
    validate_coordinates,
)

LOGGER = logging.getLogger(__name__)
_memory = SessionMemory()


class ScreenshotTool(BaseTool):
    name: str = "ScreenshotTool"
    description: str = (
        "Capture the current screen and save it to the screenshots/ folder. "
        "Call with an empty string to auto-generate a timestamped filename "
        "(e.g. screenshots/screenshot_20240531_143022_450.png) so captures "
        "are never overwritten. Optionally pass a custom filename or path. "
        "Returns the full path of the saved file — pass this to ExtractScreenshotTextTool."
    )

    def _run(self, input: Any = "") -> str:
        raw = coerce_input(input)
        path = safe_screenshot_path(raw) if raw else timestamped_screenshot_path()

        err = capture_screen(path)
        if err:
            debug_log(f"ERROR {err}")
            return err

        debug_log(f"SUCCESS {path}")
        return str(path)


class ExtractScreenshotTextTool(BaseTool):
    name: str = "ExtractScreenshotTextTool"
    description: str = (
        "Run vision-based OCR on a screenshot to extract all visible text. "
        "Pass the file path returned by ScreenshotTool. "
        "If no path is given, uses the most recent file in screenshots/. "
        "Returns a JSON array of objects with keys: id, text."
    )

    def _run(self, input: Any = "") -> str:
        path_str = coerce_input(input)
        img_path = Path(path_str) if path_str else latest_screenshot()
        if img_path is None:
            return (
                "Error: no screenshot found in screenshots/. "
                "Call ScreenshotTool first, or pass an explicit path."
            )

        if not img_path.exists():
            return f"Error: file not found — {img_path}"
        if img_path.suffix.lower() not in VALID_IMAGE_EXTENSIONS:
            return f"Error: unsupported file type '{img_path.suffix}'. Use PNG or JPG."

        try:
            text_lines = run_ocr(img_path)
        except Exception as exc:
            return f"Error running OCR: {exc}"

        elements: list[dict[str, Any]] = [
            {"id": f"text_element_{i}", "text": text}
            for i, text in enumerate(text_lines)
            if text.strip()
        ]
        return json.dumps(elements, indent=2)


class ClickCoordinatesTool(BaseTool):
    name: str = "ClickCoordinatesTool"
    description: str = (
        "Click a specific X, Y coordinate on the screen. "
        'Accepts JSON: {"x": <int>, "y": <int>, "width": <int (optional)>, "height": <int (optional)>}. '
        "If width and height are provided, clicks the centre of the bounding box. "
        "Returns a confirmation message or an error."
    )

    def _run(self, input: Any) -> str:
        raw = coerce_input(input)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            return f"Error: invalid JSON input — {exc}"

        try:
            x, y = validate_coordinates(payload.get("x"), payload.get("y"))
        except ValueError as exc:
            return f"Error: {exc}"

        width = payload.get("width")
        height = payload.get("height")
        if width is not None and height is not None:
            try:
                x += int(width) // 2
                y += int(height) // 2
            except (TypeError, ValueError):
                return "Error: 'width' and 'height' must be integers."

        try:
            import pyautogui
        except ImportError:
            return "Error: pyautogui is not installed."

        for attempt in range(1, MAX_CLICK_RETRIES + 1):
            try:
                pyautogui.click(x, y)
                return f"Clicked at ({x}, {y})."
            except Exception as exc:
                if attempt == MAX_CLICK_RETRIES:
                    return f"Error clicking at ({x}, {y}) after {MAX_CLICK_RETRIES} attempts: {exc}"
                time.sleep(CLICK_RETRY_DELAY)

        return "Error: click failed for unknown reason."


def _get_ax_elements() -> list[dict[str, Any]]:
    if platform.system() != "Darwin":
        return []

    try:
        from src.tools.utils.chromium_cdp import (
            get_cdp_ax_elements,
            get_chromium_app_info,
            get_osascript_elements,
        )

        app_info = get_chromium_app_info()
        if app_info and app_info.get("cdp_port"):
            cdp_elements = get_cdp_ax_elements(app_info["cdp_port"])
            if cdp_elements:
                return cdp_elements
        elif app_info:
            osa_elements = get_osascript_elements(app_info["name"])
            if len(osa_elements) >= 8:
                return osa_elements
    except Exception as exc:
        LOGGER.debug("Chromium CDP/osascript detection failed: %s", exc)

    try:
        tree = build_locator_tree()
        if tree is None:
            return []
        locators = get_all_locators(tree)
        seen: set[tuple[str, str]] = set()
        unique: list[dict[str, Any]] = []
        for el in locators:
            label = (el.get("label") or "").strip()
            role = el.get("role", "")
            cx = el.get("center_x")
            cy = el.get("center_y")
            if not label or cx is None or cy is None:
                continue
            key = (label, role)
            if key in seen:
                continue
            seen.add(key)
            unique.append(
                {
                    "text": label,
                    "role": role,
                    "role_desc": role.removeprefix("AX").lower(),
                    "center_x": cx,
                    "center_y": cy,
                    "box_x": el.get("x", 0),
                    "box_y": el.get("y", 0),
                    "width": el.get("width", 0),
                    "height": el.get("height", 0),
                    "source": "accessibility",
                    "confidence": 1.0,
                }
            )
        return unique
    except Exception as exc:
        LOGGER.warning("AX tree extraction failed: %s", exc)
        return []


class ScreenMapperTool(BaseTool):
    name: str = "ScreenMapperTool"
    description: str = (
        "Capture the full screen and build a coordinate map of every visible "
        "text and UI element using OCR.  Returns a "
        "compact summary of detected elements (text, coordinates, type).  "
        "The full map is persisted so ClickByLabelTool can use it.  "
        "Call with no arguments."
    )

    def _run(self, input: Any = "") -> str:
        path = timestamped_screenshot_path()
        capture_err = capture_screen(path)
        if capture_err:
            return capture_err
        if not path.exists():
            return f"Error: screenshot was not saved to {path}"

        source = "ocr"
        elements: list[dict[str, Any]] = []
        ax_elements = _get_ax_elements()

        if ax_elements and len(ax_elements) >= 8:
            elements = ax_elements
            source = "accessibility"
        else:
            ocr_json = ExtractScreenshotTextTool()._run(str(path))
            if ocr_json.startswith("Error"):
                return ocr_json
            try:
                elements = json.loads(ocr_json)
            except json.JSONDecodeError:
                return f"Error: OCR produced invalid JSON — {ocr_json[:300]}"

            if ax_elements:
                ax_texts = {e["text"].lower() for e in ax_elements}
                ocr_deduped = [
                    e for e in elements
                    if e.get("text", "").strip().lower() not in ax_texts
                ]
                elements = ax_elements + ocr_deduped
                source = "accessibility+ocr"

        screen_w, screen_h = 0, 0
        try:
            import pyautogui

            screen_w, screen_h = pyautogui.size()
        except Exception:
            pass

        screen_map: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "screenshot_path": str(path),
            "screen_size": {"width": screen_w, "height": screen_h},
            "element_count": len(elements),
            "source": source,
            "elements": elements,
        }

        try:
            save_screen_map(screen_map)
        except RuntimeError as exc:
            return f"Error: map built but not saved — {exc}"

        max_summary = 40
        lines = [
            f"Screen mapped: {len(elements)} elements (source: {source})",
            f"Screen: {screen_w}×{screen_h}",
            "",
            "Elements:",
        ]
        for el in elements[:max_summary]:
            text = el.get("text", "").strip()[:50] or f"[{el.get('type', 'icon')}]"
            cx, cy = el.get("center_x", "?"), el.get("center_y", "?")
            role = el.get("role", "") if el.get("source") == "accessibility" else ""
            role_tag = f" [{role}]" if role else ""
            lines.append(f'  ({cx:>4},{cy:>4}) "{text}"{role_tag}')
        if len(elements) > max_summary:
            lines.append(f"  … and {len(elements) - max_summary} more")

        return "\n".join(lines)


class ClickByLabelTool(BaseTool):
    name: str = "ClickByLabelTool"
    description: str = (
        "Click a UI element by its visible text label.  Uses the latest screen "
        "map produced by ScreenMapperTool to locate the element and click its "
        "centre coordinates.  Pass the text label as a plain string.  Matching "
        "is case-insensitive; substring and partial matches are supported. "
        "Call ScreenMapperTool first to build the map."
    )

    @staticmethod
    def _semantic_match(
        intent: str,
        elements: list[dict[str, Any]],
        min_score: float = 0.6,
    ) -> dict[str, Any] | None:
        intent_lower = intent.lower()
        scored: list[tuple[float, dict[str, Any]]] = []
        for el in elements:
            txt = el.get("text", "").strip()
            if not txt or len(txt) < 3:
                continue
            ratio = difflib.SequenceMatcher(None, intent_lower, txt.lower()).ratio()
            if ratio >= min_score:
                scored.append((ratio, el))

        if not scored:
            return None

        scored.sort(
            key=lambda t: (
                -t[0],
                0 if t[1].get("center_y", 0) > 100 else 1,
            ),
        )
        best_score, best_el = scored[0]
        LOGGER.info(
            "Semantic match: '%s' → '%s' (score=%.3f)",
            intent,
            best_el.get("text", ""),
            best_score,
        )
        return best_el

    def _run(self, input: Any) -> str:
        label = coerce_input(input)
        if not label:
            return "Error: label must be non-empty."

        try:
            screen_map = load_screen_map()
        except RuntimeError as exc:
            return f"Error: {exc}"

        elements = screen_map.get("elements", [])
        if not elements:
            return (
                "Error: no screen map available or map is empty. "
                "Call ScreenMapperTool first."
            )

        label_lower = label.lower()
        exact_matches, substring_matches = [], []

        for el in elements:
            txt = el.get("text", "").strip()
            if not txt:
                continue
            txt_lower = txt.lower()
            if txt_lower == label_lower:
                exact_matches.append(el)
            elif label_lower in txt_lower:
                substring_matches.append(el)

        if not exact_matches and substring_matches:
            substring_matches.sort(
                key=lambda e: (
                    len(e.get("text", "")) - len(label),
                    -e.get("confidence", 0),
                ),
            )

        input_roles = {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"}
        candidates = exact_matches or substring_matches

        if candidates:
            content = [
                e for e in candidates
                if e.get("role", "") not in input_roles and e.get("center_y", 0) > 100
            ]
            if content:
                candidates = content
            elif len(candidates) == 1 and candidates[0].get("center_y", 0) < 100:
                top_el = candidates[0]
                top_txt = top_el.get("text", "").strip().lower()
                for el in elements:
                    if el is top_el:
                        continue
                    el_txt = el.get("text", "").strip().lower()
                    if (
                        el.get("center_y", 0) > 100
                        and len(el_txt) >= 3
                        and (el_txt in top_txt or top_txt in el_txt)
                    ):
                        candidates = [el]
                        break

        target = candidates[0] if candidates else self._semantic_match(label, elements)
        if target is None:
            available = [e.get("text", "") for e in elements[:30]]
            return (
                f"No element matching '{label}' found in the screen map. "
                f"Available labels (first 30): {available}"
            )

        click_x = target["center_x"]
        click_y = target["center_y"]

        full_text = target.get("text", "")
        if not exact_matches and full_text and label_lower in full_text.lower():
            idx = full_text.lower().index(label_lower)
            full_len = len(full_text)
            if full_len > 0:
                box_x = target.get("box_x", click_x)
                box_w = target.get("width", 0)
                char_w = box_w / full_len
                substr_start_x = box_x + idx * char_w
                substr_end_x = substr_start_x + len(label) * char_w
                click_x = int((substr_start_x + substr_end_x) / 2)

        cal_path = Path(__file__).resolve().parents[2] / "calibration.json"
        try:
            if cal_path.exists():
                cal = json.loads(cal_path.read_text())
                click_x += int(cal.get("offset_x", 0))
                click_y += int(cal.get("offset_y", 0))
        except Exception:
            pass

        try:
            x, y = validate_coordinates(click_x, click_y)
        except (ValueError, KeyError) as exc:
            return f"Error: bad coordinates for '{target.get('text')}' — {exc}"

        try:
            import pyautogui
        except ImportError:
            return "Error: pyautogui is not installed."

        for attempt in range(1, MAX_CLICK_RETRIES + 1):
            try:
                pyautogui.click(x, y)
                result = (
                    f"Clicked '{target['text']}' at ({x}, {y}). "
                    f"Confidence: {target.get('confidence', 'N/A')}"
                )
                _memory.record_action(
                    "native", "ClickByLabelTool", str(target.get("text", "")), result
                )
                return result
            except Exception as exc:
                if attempt == MAX_CLICK_RETRIES:
                    return f"Error after {MAX_CLICK_RETRIES} attempts: {exc}"
                time.sleep(CLICK_RETRY_DELAY)

        return "Error: click failed."


ScreenMapTool = ScreenMapperTool
