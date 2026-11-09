from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

LOGGER = logging.getLogger(__name__)

_PLAYBOOK_DIR = Path(__file__).resolve().parent.parent.parent
_PLAYBOOK_FILE = _PLAYBOOK_DIR / "data" / "playbooks.json"

_LOGIN_ACTIONS = {"navigate", "click", "type_text", "key_combo"}
_MAX_ACTIONS = 20

_CRED_EXTRACT_SYSTEM = """\
Extract login credentials from the user's message. Output ONLY a JSON object:
{"username": "...", "password": "..."}

Rules:
- Look for any kind of user identifier: username, user ID, uid, student ID, email, login name, etc.
- Look for a password, passcode, or pin.
- Preserve the EXACT values — do not modify, trim, or censor them.
- Passwords may contain special characters. Include them.
- If you cannot find a username or password, use an empty string for that field.
- Output ONLY the JSON. No prose."""


def _load_playbooks() -> dict[str, Any]:
    if _PLAYBOOK_FILE.exists():
        try:
            with open(_PLAYBOOK_FILE, "r") as f:
                return json.load(f)
        except Exception as exc:
            LOGGER.warning("Failed to load playbooks: %s", exc)
    return {}


def _save_playbooks(data: dict[str, Any]) -> None:
    try:
        with open(_PLAYBOOK_FILE, "w") as f:
            json.dump(data, f, indent=2)
        LOGGER.info("Playbooks saved to %s", _PLAYBOOK_FILE)
    except Exception as exc:
        LOGGER.warning("Failed to save playbooks: %s", exc)


def _extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.netloc or ""
    except Exception:
        return ""


def _infer_target_domain(goal: str) -> str:
    known_sites_path = _PLAYBOOK_DIR / "data" / "known_sites.json"
    if not known_sites_path.exists():
        return ""

    try:
        with open(known_sites_path, "r") as f:
            data = json.load(f)
        sites = data.get("sites", {})
        goal_lower = goal.lower()
        for name, url in sites.items():
            if name.lower() in goal_lower:
                return _extract_domain(url)
    except Exception:
        pass
    return ""


def record_playbook(domain: str, history: list[dict[str, Any]], post_login_url: str = "") -> None:
    if not domain or not history:
        return

    real_actions = [h for h in history if h.get("action") in ("click", "type_text", "key_combo")]
    if not real_actions:
        LOGGER.info("Skipping playbook recording for %s — no login actions (navigate-only)", domain)
        return

    playbooks = _load_playbooks()
    site = playbooks.get(domain, {})

    login_actions = []
    nav_actions = {}
    login_phase = True

    for h in history[:_MAX_ACTIONS]:
        action = h.get("action", "")
        if action not in _LOGIN_ACTIONS:
            continue

        step = {"action": action}
        if h.get("url"):
            step["url"] = h["url"]
        if h.get("target"):
            step["target"] = h["target"]
        if h.get("selector"):
            step["selector"] = h["selector"]

        if action == "type_text":
            selector = h.get("selector", "").lower()
            if "password" in selector:
                step["text"] = "{{password}}"
            elif any(k in selector for k in ("user", "name", "id", "email", "login")):
                step["text"] = "{{username}}"
            else:
                step["text"] = h.get("text", "")

        result = h.get("result", "").lower()

        if login_phase:
            login_actions.append(step)
            if any(w in result for w in ("dashboard", "welcome", "activity", "home", "courses")):
                login_phase = False
        else:
            if action == "click":
                target_text = h.get("target", "").replace("text=", "")
                if target_text:
                    nav_actions[target_text] = [step]

    if login_actions:
        site["login"] = login_actions
        site["recorded_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if post_login_url:
        site["post_login_url"] = post_login_url
    if nav_actions:
        existing_paths = site.get("known_paths", {})
        existing_paths.update(nav_actions)
        site["known_paths"] = existing_paths

    playbooks[domain] = site
    _save_playbooks(playbooks)
    LOGGER.info(
        "Recorded playbook for %s: %d login steps, %d nav paths",
        domain,
        len(login_actions),
        len(nav_actions),
    )


def get_playbook(domain: str) -> dict[str, Any] | None:
    if not domain:
        return None
    playbooks = _load_playbooks()
    return playbooks.get(domain)


def get_playbook_for_goal(goal: str) -> tuple[str, dict[str, Any] | None]:
    domain = _infer_target_domain(goal)
    if domain:
        pb = get_playbook(domain)
        if pb:
            return domain, pb
    return "", None


def _normalize_goal_key(goal: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", " ", (goal or "").strip().lower()).strip()


def record_native_macro(goal: str, app_name: str, history: list[dict[str, Any]]) -> bool:
    key = _normalize_goal_key(goal)
    if not key or not app_name or not history:
        return False

    steps: list[dict[str, Any]] = []
    for h in history[:_MAX_ACTIONS]:
        act = h.get("action", "")
        res = str(h.get("result", ""))
        if "Error" in res or act not in ("open_app", "click", "type_text", "key_combo"):
            continue
        entry: dict[str, Any] = {"action": act, "target": h.get("target", "")}
        if h.get("selector"):
            entry["selector"] = h["selector"]
        if h.get("text"):
            entry["text"] = h["text"]
        if "Locator: " in res:
            entry["locator"] = res.split("Locator: ", 1)[1].strip()
        steps.append(entry)

    if len(steps) < 2:
        return False

    playbooks = _load_playbooks()
    macros = playbooks.setdefault("native_macros", {})
    macros[key] = {
        "app_name": app_name,
        "steps": steps,
        "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_playbooks(playbooks)
    return True


def get_native_macro(goal: str) -> dict[str, Any] | None:
    key = _normalize_goal_key(goal)
    if not key:
        return None
    playbooks = _load_playbooks()
    return (playbooks.get("native_macros") or {}).get(key)


def replay_native_macro(
    macro: dict[str, Any],
    on_step=None,
) -> tuple[bool, list[dict[str, Any]], str]:
    steps = macro.get("steps") or []
    app_name = str(macro.get("app_name") or "").strip()
    if not steps or not app_name:
        return False, [], ""

    from src.tools.macui_tools import (
        AXLocatorClickTool,
        AXLocatorSetValueTool,
        OpenMacApplicationTool,
    )
    from src.agents.StepAgent import KeyComboTool, TypeTextTool

    history: list[dict[str, Any]] = []
    for step in steps:
        act = step.get("action", "")
        target = step.get("locator") or step.get("selector") or step.get("target", "")
        text = step.get("text", "")
        res = ""
        try:
            if act == "open_app":
                res = OpenMacApplicationTool()._run(target or app_name)
            elif act == "click":
                res = AXLocatorClickTool()._run(target)
            elif act == "type_text":
                if target:
                    res = AXLocatorSetValueTool()._run(f"{target} ||| {text}")
                if not res or "Error" in res:
                    res = TypeTextTool()._run(text)
            elif act == "key_combo":
                res = KeyComboTool()._run(target)
            else:
                return False, history, ""
        except Exception as exc:
            LOGGER.debug("replay_native_macro failed on %s: %s", act, exc)
            return False, history, ""

        if "Error" in res or "no element found" in res:
            return False, history, ""

        rec = {"action": act, "target": target, "result": res[:200]}
        history.append(rec)
        if on_step:
            try:
                on_step(act, target, res[:120])
            except Exception:
                pass

    return True, history, f"Completed '{app_name}' task via learned AXPath macro ({len(history)} steps)."


def replay_login(
    playbook: dict[str, Any],
    credentials: dict[str, Any],
    on_step=None,
    *,
    use_safari: bool = True,
) -> list[dict[str, Any]]:
    login_steps = playbook.get("login", [])
    if not login_steps:
        return []

    bt = None
    if not use_safari:
        from src.graph.tools import get_browser_tools

        bt = get_browser_tools()
    else:
        from src.skills.web_search_skill import open_in_safari
        from src.tools.macui_tools import AXLocatorClickTool, AXLocatorSetValueTool
        from src.tools.utils.ax_locator import wait_for_safari_ready
        from src.agents.StepAgent import KeyComboTool

    history = []

    LOGGER.info("Replaying %d login steps from playbook (safari=%s)", len(login_steps), use_safari)

    for step in login_steps:
        action = step.get("action", "")
        result = ""

        try:
            if action == "navigate":
                url = step.get("url", "")
                if url:
                    if use_safari:
                        open_in_safari(url)
                        wait_for_safari_ready(timeout=1.5)
                        result = f"Opened {url} in Safari"
                    else:
                        result = bt["navigate"]._run(url)
                        time.sleep(1)

            elif action == "click":
                target = step.get("target", "")
                if target:
                    if use_safari:
                        result = AXLocatorClickTool()._run(target)
                        wait_for_safari_ready(timeout=1.2)
                    else:
                        result = bt["click"]._run(target)
                        time.sleep(0.5)

            elif action == "type_text":
                selector = step.get("selector", "")
                text = step.get("text", "")
                is_pw = text == "{{password}}" or "password" in selector.lower()
                if text == "{{username}}":
                    text = credentials.get("username", "")
                elif text == "{{password}}":
                    text = credentials.get("password", "")
                if selector and text:
                    if use_safari:
                        result = AXLocatorSetValueTool()._run(f"{selector} ||| {text}")
                    else:
                        result = bt["type"]._run(
                            json.dumps({"selector": selector, "text": text})
                        )
                    if is_pw and text:
                        result = result.replace(text, "••••••••")

            elif action == "key_combo":
                keys = step.get("keys") or step.get("combo", "")
                if keys:
                    if use_safari:
                        result = KeyComboTool()._run(keys)
                    else:
                        tool = bt.get("key_combo") or bt.get("type")
                        if tool is not None:
                            result = tool._run(keys)

        except Exception as exc:
            LOGGER.warning("Playbook replay step failed: %s %s — %s", action, step, exc)
            result = f"Error: {exc}"
            history.append(
                {
                    "action": action,
                    "target": step.get("target", step.get("selector", "")),
                    "result": result,
                }
            )
            break

        record = {
            "action": action,
            "target": step.get("target", step.get("selector", step.get("url", ""))),
            "result": result[:200] if result else "OK",
        }
        history.append(record)
        LOGGER.info("Playbook replay: %s → %s", action, result[:80] if result else "OK")

        if on_step:
            on_step(action, record["target"], record["result"])

    return history


def extract_credentials_from_goal(goal: str) -> dict[str, str]:
    try:
        from src.graph.grammars import GENERIC_JSON_OBJECT_GRAMMAR, load_grammar
        from src.graph.llm import call_llm, extract_json

        raw = call_llm(
            _CRED_EXTRACT_SYSTEM,
            goal,
            num_predict=1024,
            grammar=load_grammar(GENERIC_JSON_OBJECT_GRAMMAR),
        )
        LOGGER.info("Credential extraction raw: %s", raw[:200])
        parsed = extract_json(raw)
        creds = {}
        if parsed.get("username"):
            creds["username"] = parsed["username"]
        if parsed.get("password"):
            creds["password"] = parsed["password"]
        return creds
    except Exception as exc:
        LOGGER.warning("LLM credential extraction failed: %s", exc)
        return {}
