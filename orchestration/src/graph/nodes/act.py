from __future__ import annotations

import logging
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

from src.graph.config import LokiState
from src.graph.tools import get_native_tools, live_title, live_url
from src.tools.utils.ax_locator import activate_app, wait_for_safari_ready

LOGGER = logging.getLogger(__name__)

_SHORTCUTS_CACHE: dict[str, str] = {}


def _resolve_locator(index_str: str, locator_map: dict[str, Any] | None) -> str | None:
    if not locator_map:
        return None
    idx = index_str.strip().strip("[]")
    if not idx.isdigit():
        return None
    num = int(idx)
    return locator_map.get(str(num)) or locator_map.get(f"{num:02d}") or locator_map.get(idx)


def _resolve_coords(index_str: str, state: LokiState) -> tuple[int, int, str] | None:
    idx = index_str.strip().strip("[]")
    if not idx.isdigit():
        return None
    key = str(int(idx))
    coords_map = state.get("_element_coords_map") or {}
    entry = coords_map.get(key)
    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
        label = str(entry[2]) if len(entry) >= 3 else key
        return int(entry[0]), int(entry[1]), label
    return None


def _resolve_element(index_str: str, elements: str) -> str:
    idx = index_str.strip().strip("[]")
    if not idx.isdigit():
        return index_str
    target_idx = int(idx)
    for line in elements.splitlines():
        line = line.strip()
        if line.startswith(f"[{target_idx}]") or line.startswith(f"[{target_idx:02d}]"):
            match = re.search(r'\[\d+\]\s+(?:<[^>]+>\s+)?(?:\w+:\s*)?"([^"]+)"', line)
            if match:
                return match.group(1).strip()
            parts = re.split(r"^\[\d+\]", line, maxsplit=1)
            if len(parts) > 1:
                cleaned = re.sub(r"<[^>]+>", "", parts[1]).strip()
                cleaned = re.sub(r"\[static\]", "", cleaned, flags=re.I).strip()
                cleaned = re.sub(r"^(button|link|input|text|item):\s*", "", cleaned, flags=re.I).strip()
                cleaned = cleaned.strip('"').strip("'").strip()
                if cleaned:
                    return cleaned[:60]
    return index_str


def _ensure_app_focused(state: LokiState) -> None:
    app_name = state.get("_opened_app_name", "")
    if app_name:
        activate_app(app_name)


def _human_label(action: str, decision: dict[str, Any], screen_elements: str = "") -> str:
    if action == "navigate":
        url = decision.get("url", "")
        domain = urllib.parse.urlparse(url).netloc or url
        return f"Navigating to {domain}"
    if action == "click":
        raw = decision.get("selector", decision.get("target", "element"))
        return f"Clicking {_resolve_element(raw, screen_elements)}"
    if action == "type_text":
        txt = decision.get("text", "")
        sel = decision.get("selector", "")
        resolved_sel = _resolve_element(sel, screen_elements) if sel else ""
        is_secret = txt.strip() == "{{password}}" or "password" in (sel + " " + resolved_sel).lower()
        label = '"••••••••"' if is_secret else (f'"{txt[:30]}"' if txt else "text")
        if sel:
            return f"Typing {label} into {resolved_sel}"
        return f"Typing {label}"
    if action == "type_content":
        return f"Writing {decision.get('description', 'content')[:40]}"
    if action == "key_combo":
        return f"Pressing {decision.get('combo', 'keys')}"
    if action == "open_app":
        return f"Opening {decision.get('name', 'app')}"
    if action == "wait":
        return f"Waiting {decision.get('seconds', 2)}s"
    if action == "scroll":
        return f"Scrolling {decision.get('direction', 'down')}"
    if action == "read_page":
        return "Reading page content"
    if action == "web_search":
        return f"Searching the web for {decision.get('query', '')[:40]}"
    if action == "read_url":
        domain = urllib.parse.urlparse(decision.get("url", "")).netloc or decision.get("url", "")
        return f"Reading {domain}"
    if action == "write_content":
        return f"Writing {decision.get('description', 'content')[:40]} to {decision.get('destination', 'target')}"
    if action == "ask_user":
        return "Asking user for input"
    if action == "done":
        return "Task complete"
    return f"Action: {action}"


def _handle_navigate(
    decision: dict[str, Any],
    state: LokiState,
    updates: dict[str, Any],
    bt: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    url = decision.get("url", "")
    from src.skills.web_search_skill import open_in_safari

    open_in_safari(url)
    wait_for_safari_ready(timeout=1.5)
    updates["current_url"] = ""
    updates["_opened_app_name"] = "Safari"
    return f"Opened {url} in Safari", False


def _handle_click(
    decision: dict[str, Any],
    state: LokiState,
    updates: dict[str, Any],
    bt: dict[str, Any] | None = None,
    nt: dict[str, Any] | None = None,
) -> str:
    nt = nt or {}
    raw_sel = decision.get("selector") or decision.get("target", "")
    elements_str = state.get("screen_elements", "")
    selector = _resolve_element(raw_sel, elements_str)

    _ensure_app_focused(state)
    from src.tools.macui_tools import AXLocatorClickTool

    exact_locator = _resolve_locator(raw_sel, state.get("_locator_map"))
    if exact_locator:
        res = AXLocatorClickTool()._run(exact_locator)
        if "Error" not in res and "no element found" not in res:
            if (state.get("_opened_app_name") or "").lower() == "safari":
                wait_for_safari_ready(timeout=1.2)
            return res

    coords = _resolve_coords(raw_sel, state)
    if coords is not None:
        cx, cy, label = coords
        try:
            import pyautogui

            pyautogui.click(cx, cy)
            if (state.get("_opened_app_name") or "").lower() == "safari":
                wait_for_safari_ready(timeout=1.2)
            return f"Clicked '{label}' at ({cx},{cy})"
        except Exception as exc:
            LOGGER.debug("act: coordinate click fallback failed: %s", exc)

    res = AXLocatorClickTool()._run(selector)
    if "Error" not in res and "no element found" not in res:
        if (state.get("_opened_app_name") or "").lower() == "safari":
            wait_for_safari_ready(timeout=1.2)
        return res

    click_tool = nt.get("click") or nt.get("click_label")
    if click_tool is not None:
        return click_tool._run(selector)
    return f"Error: could not click '{selector}'"


def _handle_read_page(updates: dict[str, Any], nt: dict[str, Any]) -> str:
    try:
        from src.graph.nodes.observe import _read_safari_front_document

        title, safari_text = _read_safari_front_document()
        if safari_text:
            if title:
                updates["page_title"] = title
            updates["page_text"] = safari_text[:6000]
            return f"Read Safari page ({len(safari_text)} chars)"
    except Exception:
        pass
    screen_text = nt["screen_map"]._run("")
    if screen_text:
        updates["page_text"] = screen_text[:6000]
    return screen_text


def _append_finding(state: LokiState, updates: dict[str, Any], kind: str, source: str, content: str) -> None:
    findings = list(state.get("findings", []))
    findings.append({"type": kind, "query_or_url": source, "content": content})
    updates["findings"] = findings


def _handle_web_search(decision: dict[str, Any], state: LokiState, updates: dict[str, Any]) -> str:
    query = decision.get("query", "")
    from src.tools.research_tools import WebSearchTool

    result = WebSearchTool()._run(query)
    _append_finding(state, updates, "search", query, result)
    return result


def _handle_read_url(decision: dict[str, Any], state: LokiState, updates: dict[str, Any]) -> str:
    url = decision.get("url", "")
    from src.tools.research_tools import FetchPageContentTool

    result = FetchPageContentTool()._run(url)
    _append_finding(state, updates, "read", url, result)
    updates["current_url"] = live_url()
    updates["page_title"] = live_title()
    if not result.startswith("Error"):
        updates["page_text"] = result
    return result


def _handle_type_text(
    decision: dict[str, Any],
    state: LokiState,
    bt: dict[str, Any] | None = None,
    nt: dict[str, Any] | None = None,
) -> str:
    nt = nt or {}
    text = decision.get("text", "")
    raw_sel = decision.get("selector", "")
    elements_str = state.get("screen_elements", "")
    selector = _resolve_element(raw_sel, elements_str)

    is_secret = text.strip() == "{{password}}" or "password" in (raw_sel + " " + selector).lower()
    if text.strip() == "{{password}}":
        try:
            from src.graph.site_knowledge import SiteKnowledge

            resolved_pw = SiteKnowledge().resolve_password_for_domain(
                state.get("current_url") or state.get("goal", "")
            )
            if resolved_pw:
                text = resolved_pw
                is_secret = True
        except Exception as exc:
            LOGGER.debug("act: password resolution skipped: %s", exc)

    _ensure_app_focused(state)
    time.sleep(0.05)
    res = ""
    if selector or raw_sel:
        from src.tools.macui_tools import AXLocatorClickTool, AXLocatorSetValueTool

        exact_locator = _resolve_locator(raw_sel, state.get("_locator_map"))
        target_loc = exact_locator or selector
        set_res = AXLocatorSetValueTool()._run(f"{target_loc} ||| {text}")
        if "Error" not in set_res:
            res = set_res
        else:
            coords = _resolve_coords(raw_sel, state)
            if coords is not None:
                try:
                    import pyautogui

                    pyautogui.click(coords[0], coords[1])
                    time.sleep(0.2)
                except Exception:
                    pass
            else:
                AXLocatorClickTool()._run(target_loc)
                time.sleep(0.15)
    if not res:
        res = nt["type"]._run(text)
    if is_secret and text and text != "{{password}}":
        res = res.replace(text, "••••••••")
    step_and_goal = f"{state.get('goal', '')} {' '.join(state.get('plan', []))}".lower()
    if "Error" not in res and any(w in step_and_goal for w in ("search", "look up", "find", "google")):
        time.sleep(0.15)
        nt["key_combo"]._run("return")
        res += " and pressed return"
    return res


def _generate_content(state: LokiState, description: str) -> str:
    from src.agents.ContentAgent import ContentAgent

    agent = ContentAgent()
    findings = state.get("findings", [])
    if findings:
        return agent.generate_from_findings(
            description, findings, state.get("page_text", "")
        )
    if state.get("page_text"):
        return agent.generate_with_context(description, state.get("page_text"))
    return agent.generate(description)


def _handle_type_content(decision: dict[str, Any], state: LokiState, nt: dict[str, Any]) -> str:
    desc = decision.get("description", "")
    text = _generate_content(state, desc)
    LOGGER.info("Generated content (%d chars): %s…", len(text), text[:80])
    return nt["type"]._run(text)


def _derive_title(state: LokiState, description: str) -> str:
    source = description or state.get("goal", "") or "Loki note"
    title = re.sub(r"\s+", " ", source).strip().strip(".")
    return title[:60] or "Loki note"


def _write_note(title: str, text: str) -> str:
    from src.skills import find_skill_by_name

    skill = find_skill_by_name("notes")
    if skill is None:
        return "Error: notes skill unavailable"
    try:
        return skill.execute("create_note", {"title": title, "body": text})
    except Exception as exc:
        LOGGER.warning("write_content: notes skill failed: %s", exc)
        return f"Error: could not create note: {exc}"


def _draft_email(subject: str, text: str) -> str:
    from src.skills import find_skill_by_name

    skill = find_skill_by_name("mail")
    if skill is None:
        return "Error: mail skill unavailable"
    try:
        return skill.execute("draft_email", {"subject": subject, "body": text})
    except Exception as exc:
        LOGGER.warning("write_content: mail skill failed: %s", exc)
        return f"Error: could not draft email: {exc}"


def _write_file(title: str, text: str) -> str:
    base = re.sub(r"[^\w\s-]", "", title).strip()
    base = re.sub(r"\s+", "_", base)[:60] or "loki_note"
    documents = Path.home() / "Documents"
    documents.mkdir(parents=True, exist_ok=True)
    path = documents / f"{base}.md"
    counter = 1
    while path.exists():
        path = documents / f"{base}_{counter}.md"
        counter += 1
    try:
        path.write_text(text, encoding="utf-8")
    except Exception as exc:
        LOGGER.warning("write_content: file write failed: %s", exc)
        return f"Error: could not write file: {exc}"
    return f"Saved {len(text)} chars to {path}"


def _handle_write_content(
    decision: dict[str, Any], state: LokiState, nt: dict[str, Any]
) -> str:
    destination = (decision.get("destination") or "").strip().lower()
    description = decision.get("description", "")

    text = _generate_content(state, description)
    title = _derive_title(state, description)
    LOGGER.info(
        "write_content: destination=%s, generated %d chars for: %s",
        destination, len(text), description[:60],
    )

    if destination in ("note", "notes", "apple notes", "notes app"):
        return _write_note(title, text)
    if destination in ("email", "mail", "email draft", "draft", "draft an email"):
        return _draft_email(title, text)
    if destination in ("pages", "apple pages", "pages creator studio", "pages doc", "pages document"):
        from src.skills import find_skill_by_name

        doc_skill = find_skill_by_name("documents")
        if doc_skill is not None:
            try:
                return doc_skill.execute("create_pages_document", {"title": title, "body": text})
            except Exception as exc:
                LOGGER.warning("write_content: pages skill failed: %s", exc)
    if destination in ("clipboard", "pasteboard", "copy"):
        from src.skills import find_skill_by_name

        sys_skill = find_skill_by_name("system_control")
        if sys_skill is not None:
            try:
                return sys_skill.execute("set_clipboard", {"text": text})
            except Exception as exc:
                LOGGER.warning("write_content: clipboard skill failed: %s", exc)
    if destination in ("file", "document", "documents", "disk"):
        return _write_file(title, text)
    return nt["type"]._run(text)


def _handle_key_combo(decision: dict[str, Any], state: LokiState, nt: dict[str, Any]) -> str:
    combo = decision.get("combo", "")
    if not state.get("current_url"):
        _ensure_app_focused(state)
    result = nt["key_combo"]._run(combo)
    time.sleep(0.3)
    return result


def _handle_open_app(decision: dict[str, Any], state: LokiState, updates: dict[str, Any], nt: dict[str, Any]) -> str:
    name = decision.get("name", "")
    from src.graph.app_knowledge import AppKnowledge

    apps = state.get("installed_apps") or nt["find_apps"]._run("").strip().splitlines()
    exact, err = AppKnowledge().resolve_app(name, custom_apps=apps)
    if not exact:
        err_msg = err or f"Unknown application: {name!r}"
        LOGGER.warning("act open_app '%s' rejected: %s", name, err_msg)
        return f"Error: {err_msg}"

    if hasattr(nt.get("open_app"), "installed_apps"):
        nt["open_app"].installed_apps = apps

    result = nt["open_app"]._run(exact)
    plan = state.get("plan") or []
    is_single_open_step = len(plan) <= 1

    shortcuts = ""
    if not is_single_open_step:
        activate_app(exact, wait_timeout=0.8)
        if exact in _SHORTCUTS_CACHE:
            shortcuts = _SHORTCUTS_CACHE[exact]
        else:
            try:
                shortcuts = nt["shortcuts"]._run(exact)
                _SHORTCUTS_CACHE[exact] = shortcuts
            except Exception:
                pass

    updates["installed_apps"] = apps
    updates["shortcuts_raw"] = shortcuts
    updates["current_url"] = ""
    updates["_opened_app_name"] = exact
    return result


def _handle_scroll(decision: dict[str, Any], state: LokiState, nt: dict[str, Any]) -> str:
    return nt["scroll"]._run(decision.get("direction", "down"))


def _handle_ask_user(decision: dict[str, Any], state: LokiState, updates: dict[str, Any]) -> str:
    question = decision.get("question", "I need more information to proceed.")
    ask_fn = state.get("ask_user")
    if ask_fn:
        answer = ask_fn(question)
        updates["user_answer"] = answer
        return f"User answered: {answer}" if answer else "No answer from user"

    updates["user_answer"] = ""
    return "Cannot ask user (not running interactively)"


def act_node(state: LokiState) -> dict[str, Any]:
    decision = state.get("next_action", {})
    action = decision.get("action", "done")
    history = list(state.get("history", []))
    nt = get_native_tools()
    bt: dict[str, Any] = {}

    LOGGER.info("act: %s", decision)
    result = ""
    updates: dict[str, Any] = {}

    try:
        if action == "navigate":
            res, is_blocked = _handle_navigate(decision, state, updates, bt)
            if is_blocked:
                history.append({"action": "navigate", "target": decision.get("url", ""), "result": res})
                updates["history"] = history
                return updates
            result = res
        elif action == "click":
            result = _handle_click(decision, state, updates, bt, nt)
        elif action == "read_page":
            result = _handle_read_page(updates, nt)
        elif action == "web_search":
            result = _handle_web_search(decision, state, updates)
        elif action == "read_url":
            result = _handle_read_url(decision, state, updates)
        elif action == "write_content":
            result = _handle_write_content(decision, state, nt)
        elif action == "type_text":
            result = _handle_type_text(decision, state, bt, nt)
        elif action == "type_content":
            result = _handle_type_content(decision, state, nt)
        elif action == "key_combo":
            result = _handle_key_combo(decision, state, nt)
        elif action == "open_app":
            result = _handle_open_app(decision, state, updates, nt)
        elif action == "wait":
            result = nt["wait"]._run(str(decision.get("seconds", 2)))
        elif action == "scroll":
            result = _handle_scroll(decision, state, nt)
        elif action == "ask_user":
            result = _handle_ask_user(decision, state, updates)
        elif action in ("done", "fail"):
            result = decision.get("summary", decision.get("reason", "Finished"))
            updates["result"] = result
        else:
            result = f"Unknown action: {action}"
    except Exception as exc:
        result = f"Error: {exc}"
        LOGGER.error("act failed: %s", exc)

    on_message = state.get("on_message")
    if on_message and action not in ("ask_user",):
        on_message("step", _human_label(action, decision, state.get("screen_elements", "")))

    raw_text_val = decision.get("text", "")
    is_secret_entry = (
        action == "type_text"
        and (
            raw_text_val.strip() == "{{password}}"
            or "password" in str(decision.get("selector", "")).lower()
            or "••••••••" in str(result)
        )
    )
    safe_text_val = "{{password}}" if is_secret_entry else raw_text_val
    target_str = (
        decision.get("url")
        or decision.get("target")
        or decision.get("name")
        or decision.get("query")
        or safe_text_val[:60]
        or decision.get("description", "")[:60]
        or decision.get("combo")
        or decision.get("direction")
        or ""
    )
    history_entry = {
        "action": action,
        "target": target_str,
        "result": str(result)[:200],
    }
    if decision.get("url"):
        history_entry["url"] = decision["url"]
    if decision.get("selector"):
        history_entry["selector"] = decision["selector"]
    if decision.get("text"):
        history_entry["text"] = safe_text_val
    history.append(history_entry)

    try:
        from src.memory import SessionMemory

        SessionMemory().record_action("graph", action, target_str, str(result)[:200])
    except Exception:
        pass

    on_step = state.get("on_step")
    if on_step:
        try:
            on_step(action, target_str, str(result)[:120])
        except Exception:
            pass

    LOGGER.info("act result: %s", str(result)[:120])
    updates["history"] = history
    return updates
