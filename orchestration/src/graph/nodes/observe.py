from __future__ import annotations

import logging
import subprocess
import time
from typing import Any

from src.graph.config import LokiState
from src.tools.utils.ax_locator import activate_app, build_locator_tree, get_all_locators

LOGGER = logging.getLogger(__name__)

_MIN_NATIVE_ELEMENTS = 8


def observe_node(state: LokiState) -> dict[str, Any]:
    history = state.get("history", [])
    if history:
        last_action = history[-1].get("action", "")
        last_result = str(history[-1].get("result", "")).lower()
        if last_action in {"wait", "scroll"} or (
            last_action == "type_text" and "pressed return" not in last_result
        ):
            LOGGER.info("observe: skipping re-scan after trivial action '%s'", last_action)
            return {}
        if last_action in {"open_app", "key_combo"} or "pressed return" in last_result:
            LOGGER.info("observe: forcing fresh scan after '%s'", last_action)

    cur_url = ""
    page_title = ""
    target_app = state.get("_opened_app_name", "")
    elements_str, ax_tree, page_text, locator_map, coords_map = _observe_native(target_app=target_app)

    if target_app.lower() == "safari":
        safari_title, safari_text = _read_safari_front_document()
        safari_url = _read_safari_front_url()
        if safari_url:
            cur_url = safari_url
        if safari_title and not page_title:
            page_title = safari_title
        if safari_text:
            page_text = f"{safari_text}\n{page_text}".strip() if page_text else safari_text

    LOGGER.info(
        "observe: url=%s title=%s text=%d chars, ax_tree=%d chars, %d element lines",
        cur_url[:60] if cur_url else "(none)",
        page_title[:40] if page_title else "(none)",
        len(page_text),
        len(ax_tree),
        elements_str.count("\n") + 1 if elements_str else 0,
    )

    updates: dict[str, Any] = {
        "current_url": cur_url,
        "page_title": page_title,
        "page_text": page_text,
        "screen_elements": elements_str,
    }
    if locator_map:
        updates["_locator_map"] = locator_map
        cur_loc_set = {str(v) for k, v in locator_map.items() if k.isdigit() and len(k) <= 2}
        prev_locs = state.get("_prev_locators") or []
        if prev_locs:
            prev_set = set(prev_locs)
            added = len(cur_loc_set - prev_set)
            removed = len(prev_set - cur_loc_set)
            if added == 0 and removed == 0:
                updates["ui_delta_summary"] = "0 structural UI changes since last action"
            else:
                updates["ui_delta_summary"] = f"+{added} new UI element(s), -{removed} removed"
        updates["_prev_locators"] = sorted(cur_loc_set)
    if coords_map:
        updates["_element_coords_map"] = coords_map
    return updates


def _activate_and_get_pid(target_app: str) -> int | None:
    if not target_app:
        return None
    pid = activate_app(target_app, wait_timeout=1.0)
    if pid is not None:
        LOGGER.info("observe: targeting app '%s' (PID %d)", target_app, pid)
    return pid


def _try_cdp_tier(app_info: dict[str, Any] | None) -> tuple[str, str] | None:
    if not app_info or not app_info.get("cdp_port"):
        return None
    try:
        from src.tools.utils.chromium_cdp import get_cdp_ax_elements

        cdp_elements = get_cdp_ax_elements(app_info["cdp_port"])
        if len(cdp_elements) >= _MIN_NATIVE_ELEMENTS:
            elements_str = _format_native_elements(cdp_elements)
            page_text = "\n".join(e["text"] for e in cdp_elements[:30])
            LOGGER.info("observe: CDP tier accepted (%d elements)", len(cdp_elements))
            return elements_str, page_text
    except Exception as exc:
        LOGGER.debug("observe: Chromium detection failed: %s", exc)
    return None


def _try_osascript_tier(app_info: dict[str, Any] | None) -> tuple[str, str] | None:
    if not app_info:
        return None
    try:
        from src.tools.utils.chromium_cdp import get_osascript_elements

        osa_elements = get_osascript_elements(app_info["name"])
        if len(osa_elements) >= _MIN_NATIVE_ELEMENTS:
            elements_str = _format_native_elements(osa_elements)
            page_text = "\n".join(e["text"] for e in osa_elements[:30])
            LOGGER.info("observe: osascript tier accepted (%d elements)", len(osa_elements))
            return elements_str, page_text
    except Exception as exc:
        LOGGER.debug("observe: osascript tier failed: %s", exc)
    return None


def _read_safari_front_url() -> str:
    script = """
    tell application "Safari"
        if (count of documents) is 0 then return ""
        return URL of front document
    end tell
    """
    try:
        res = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.returncode == 0 and (res.stdout or "").strip():
            return res.stdout.strip()
    except Exception as exc:
        LOGGER.debug("observe: Safari URL read failed: %s", exc)
    return ""


def _read_safari_front_document() -> tuple[str, str]:
    script = """
    tell application "Safari"
        if (count of documents) is 0 then return ""
        set docTitle to name of front document
        set docText to text of front document
        return docTitle & "|||" & docText
    end tell
    """
    try:
        res = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=4,
        )
        if res.returncode != 0 or not (res.stdout or "").strip():
            return "", ""
        raw = res.stdout.strip()
        if "|||" not in raw:
            return "", _clean_safari_text(raw)[:3500]
        title, text = raw.split("|||", 1)
        return title.strip(), _clean_safari_text(text)[:3500]
    except Exception as exc:
        LOGGER.debug("observe: Safari document read failed: %s", exc)
        return "", ""


def _clean_safari_text(text: str) -> str:
    cleaned = (text or "").strip()
    idx = cleaned.find("Search Results")
    if 0 < idx < 800:
        cleaned = cleaned[idx:]
    return cleaned


def _try_ax_locator_tier(target_pid: int | None) -> tuple[str, str, str, dict[str, Any], dict[str, Any], bool]:
    try:
        tree = build_locator_tree(pid=target_pid)
        if not tree:
            return "", "", "", {}, {}, False

        all_locators = get_all_locators(tree)
        if not all_locators:
            return "", "", "", {}, {}, False

        elements_str, locator_map, coords_map = _format_locator_elements(all_locators)
        ax_tree = _format_locator_tree(all_locators)
        web_labels = [
            e["label"]
            for e in all_locators
            if e.get("label")
            and len(str(e["label"]).strip()) > 3
            and "AXWebArea" in str(e.get("locator", ""))
        ]
        general_labels = [
            e["label"]
            for e in all_locators
            if e.get("label")
            and len(str(e["label"]).strip()) > 3
            and e.get("role", "") not in ("AXMenuBarItem", "AXMenuBar", "AXMenuItem")
        ]
        chosen_labels = web_labels[:50] if web_labels else general_labels[:40]
        page_text = "\n".join(chosen_labels)

        interactable_content = [
            e for e in all_locators
            if e.get("interactable") and e.get("role", "") not in ("AXMenuBarItem", "AXMenuBar", "AXMenuItem")
        ]
        is_sufficient = len(interactable_content) >= _MIN_NATIVE_ELEMENTS
        return elements_str, ax_tree, page_text, locator_map, coords_map, is_sufficient
    except Exception as exc:
        LOGGER.debug("observe: AX locator tier failed: %s", exc)
        return "", "", "", {}, {}, False


def _append_ocr_fallback(
    elements_str: str,
    locator_map: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    loc_map = dict(locator_map) if locator_map else {}
    interactable_lines = [
        line for line in (elements_str or "").split("\n") if line.strip() and "[static]" not in line
    ]
    if elements_str and len(interactable_lines) >= _MIN_NATIVE_ELEMENTS:
        return elements_str, loc_map

    try:
        from src.tools.utils.constants import SCREENSHOTS_DIR
        from src.tools.utils.ocr import latest_screenshot, run_ocr
        from src.tools.utils.window_capture import capture_focused_window

        out_path = SCREENSHOTS_DIR / f"observe_{int(time.time())}.png"
        capture_focused_window(out_path)
        img_path = out_path if out_path.exists() else latest_screenshot()
        if not img_path:
            return elements_str, loc_map

        existing_lines = [ln for ln in (elements_str or "").split("\n") if ln.strip()]
        start_idx = len(existing_lines)

        ocr_lines_raw = run_ocr(img_path)
        if ocr_lines_raw:
            ocr_lines = [
                f"[{start_idx + i:02d}] [static] <ocr> {text[:60]}"
                for i, text in enumerate(ocr_lines_raw[:40])
                if text.strip()
            ]
            if ocr_lines:
                LOGGER.info("observe: OCR supplemented %d text elements", len(ocr_lines))
                combined = (elements_str + "\n" if elements_str else "") + "\n".join(ocr_lines)
                return combined, loc_map
    except Exception as exc:
        LOGGER.debug("observe: OCR fallback failed: %s", exc)

    return elements_str, loc_map


def _observe_native(target_app: str = "") -> tuple[str, str, str, dict[str, Any], dict[str, Any]]:
    target_pid = _activate_and_get_pid(target_app)

    elements_str, ax_tree, page_text, locator_map, coords_map, is_sufficient = _try_ax_locator_tier(target_pid)
    if is_sufficient:
        return elements_str, ax_tree, page_text, locator_map, coords_map

    app_info: dict[str, Any] | None = None
    try:
        from src.tools.utils.chromium_cdp import get_chromium_app_info

        app_info = get_chromium_app_info()
    except Exception:
        pass

    cdp_res = _try_cdp_tier(app_info)
    if cdp_res:
        return cdp_res[0], "", cdp_res[1], {}, {}

    osa_res = _try_osascript_tier(app_info)
    if osa_res:
        return osa_res[0], "", osa_res[1], {}, {}

    elements_str, locator_map = _append_ocr_fallback(elements_str, locator_map)
    return elements_str, ax_tree, page_text, locator_map, coords_map


def _format_native_elements(elements: list[dict[str, Any]]) -> str:
    lines = []
    for i, e in enumerate(elements[:60]):
        role = e.get("role_desc") or e.get("role", "").removeprefix("AX").lower() or "element"
        text = e.get("text", "")[:60]
        marker = "" if e.get("interactable", True) else " [static]"
        lines.append(f"[{i:02d}]{marker} <{role}> {text}")
    return "\n".join(lines)


def _format_locator_elements(locators: list[dict[str, Any]]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    max_elements = 80
    sidebar_cap = 6
    sidebar_x_threshold = 200

    def is_sidebar(e: dict[str, Any]) -> bool:
        loc = e.get("locator", "").lower()
        return "sidebar" in loc or "outline" in loc or e.get("center_x", 500) < sidebar_x_threshold

    def is_web_content(e: dict[str, Any]) -> bool:
        return "AXWebArea" in str(e.get("locator", ""))

    sidebar_inter = [e for e in locators if e.get("interactable") and is_sidebar(e) and not is_web_content(e)]
    web_inter = [e for e in locators if e.get("interactable") and is_web_content(e) and e.get("label")]
    content_inter = [
        e for e in locators
        if e.get("interactable") and not is_sidebar(e) and not is_web_content(e)
    ]
    static = [e for e in locators if not e.get("interactable") and e.get("label")]

    ordered = (web_inter + content_inter)[:max_elements]
    sidebar_budget = min(sidebar_cap, max_elements - len(ordered))
    ordered += sidebar_inter[:sidebar_budget]
    static_budget = max(0, max_elements - len(ordered))
    ordered += static[:static_budget]

    label_counts: dict[str, int] = {}
    for e in ordered:
        lbl = (e.get("label") or "")[:50].strip().lower()
        if lbl:
            label_counts[lbl] = label_counts.get(lbl, 0) + 1

    lines = []
    loc_map: dict[str, Any] = {}
    coords_map: dict[str, Any] = {}
    for i, e in enumerate(ordered):
        role = e.get("role", "").removeprefix("AX").lower() or "element"
        label = e.get("label", "")[:50]
        container = (e.get("container") or "").strip()
        is_dup = label_counts.get(label.strip().lower(), 0) > 1
        role_tag = f"{role} @{container}" if (container and is_dup) else role
        marker = "" if e.get("interactable") else " [static]"
        lines.append(f"[{i:02d}]{marker} <{role_tag}> {label}")
        locator = e.get("locator", "")
        if locator:
            loc_map[str(i)] = locator
            loc_map[f"{i:02d}"] = locator
        if e.get("center_x") is not None and e.get("center_y") is not None:
            coords_map[str(i)] = (int(e["center_x"]), int(e["center_y"]), label)

    return "\n".join(lines), loc_map, coords_map


def _format_locator_tree(locators: list[dict[str, Any]]) -> str:
    lines = []
    for e in locators:
        if not e.get("interactable") or not e.get("label"):
            continue
        role = e.get("role", "")
        label = e.get("label", "")
        enabled = "enabled" if e.get("enabled", True) else "disabled"
        lines.append(f'[{role}] "{label[:40]}" {enabled}')
        if len(lines) >= 25:
            break
    return "\n".join(lines)
