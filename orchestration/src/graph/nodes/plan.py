from __future__ import annotations

import logging
import re
from typing import Any

from src.graph.config import LokiState
from src.graph.grammars import AUTOMATION_STEPS_GRAMMAR, PLAN_GRAMMAR, load_grammar
from src.graph.llm import call_llm, extract_json
from src.graph.prompts import AUTOMATION_PLAN_SYSTEM, PLAN_SYSTEM

LOGGER = logging.getLogger(__name__)

_MACOS_APPS = {
    "photos", "notes", "terminal", "finder", "safari", "mail", "calendar",
    "reminders", "contacts", "preview", "messages", "facetime", "music",
    "maps", "weather", "system settings", "system preferences",
    "spotlight", "activity monitor", "disk utility", "textedit",
    "calculator", "clock", "stocks", "voice memos", "app store",
}

_WINDOWS_PATTERNS = re.compile(
    r"(windows?\s+start|win\s*\+|start\s+menu|taskbar|cortana|"
    r"microsoft\s+store|control\s+panel|cmd\.exe|powershell)",
    re.IGNORECASE,
)

_OPEN_VERBS = {"open", "launch", "start", "run"}
_MAX_PLAN_ATTEMPTS = 3

_AUTOMATION_VERBS = re.compile(
    r"\b("
    r"open|launch|start|quit|close|relaunch|"
    r"navigate|go\s+to|browse|search(?:\s+(?:for|up|google|online|the\s+web))?|look\s+up|google|"
    r"click|tap|type|write|compose|"
    r"send|reply|forward|"
    r"play|pause|resume|skip|shuffle|"
    r"set\s+(a|an)\s+(alarm|reminder|timer)|add\s+(a|an)\s+(event|reminder|note)|schedule|"
    r"check\s+(my\s+)?(email|calendar|inbox|messages)|read\s+(my\s+)?(email|messages)|"
    r"find|download|upload|install|screenshot|take\s+a\s+screenshot|"
    r"turn\s+(on|off)|change\s+the\s+(volume|brightness|wallpaper)"
    r")\b",
    re.IGNORECASE,
)

_APP_WORD_PATTERN = re.compile(r"\bapps?\b|\bapplications?\b", re.IGNORECASE)
_APP_LISTING_VERB_PATTERN = re.compile(r"\b(list|show\s+me)\b", re.IGNORECASE)
_APP_QUESTION_WORD_PATTERN = re.compile(r"\b(what|which)\b", re.IGNORECASE)
_APP_POSSESSION_PATTERN = re.compile(r"\b(install\w*|have|got|own)\b", re.IGNORECASE)


def _looks_like_automation(goal: str, installed_apps: list[str]) -> bool:
    goal_lower = goal.lower()
    if _AUTOMATION_VERBS.search(goal_lower):
        return True

    app_names_lower = {a.strip().lower() for a in installed_apps if a.strip()}
    all_known_apps = _MACOS_APPS | app_names_lower
    return any(app and app in goal_lower for app in all_known_apps)


def _looks_like_app_list_query(goal: str) -> bool:
    if not _APP_WORD_PATTERN.search(goal):
        return False
    if _APP_LISTING_VERB_PATTERN.search(goal):
        return True
    return bool(_APP_QUESTION_WORD_PATTERN.search(goal)) and bool(
        _APP_POSSESSION_PATTERN.search(goal)
    )


def _answer_installed_apps(state: LokiState) -> str:
    from src.graph.app_knowledge import AppKnowledge, canonicalize_app_name

    try:
        apps = AppKnowledge().get_canonical_names()
    except Exception:
        apps = []
    if not apps:
        apps = [
            canonicalize_app_name(a)
            for a in state.get("installed_apps", [])
            if a and a.strip()
        ]

    if not apps:
        return "I couldn't detect any installed applications on this Mac."

    preview = apps[:12]
    listed = preview[0] if len(preview) == 1 else ", ".join(preview[:-1]) + f", and {preview[-1]}"
    reply = f"You have {listed} installed"
    remaining = len(apps) - len(preview)
    if remaining > 0:
        reply += f", plus {remaining} more"
    return reply + "."


def _fix_plan(steps: list[str], goal: str, installed_apps: list[str]) -> list[str]:
    goal_lower = goal.lower()
    if not installed_apps:
        try:
            from src.graph.app_knowledge import AppKnowledge

            installed_apps = AppKnowledge().get_canonical_names()
        except Exception:
            installed_apps = []

    app_names_lower = {a.strip().lower() for a in installed_apps if a.strip()}
    all_known_apps = _MACOS_APPS | app_names_lower

    target_app = ""
    for app in sorted(all_known_apps, key=len, reverse=True):
        if app in goal_lower:
            for installed in installed_apps:
                if installed.strip().lower() == app:
                    target_app = installed.strip()
                    break
            if not target_app:
                target_app = app.title()
            break

    if not target_app:
        return [s for s in steps if not _WINDOWS_PATTERNS.search(s)]

    has_open_step = any(
        target_app.lower() in s.lower()
        and any(v in s.lower() for v in _OPEN_VERBS)
        for s in steps
    )

    cleaned = []
    for s in steps:
        s_lower = s.lower()
        if _WINDOWS_PATTERNS.search(s):
            LOGGER.info("plan fix: removed Windows step: %s", s)
            continue
        if target_app.lower() != "finder" and (
            "finder" in s_lower and ("launch" in s_lower or "open" in s_lower)
            and target_app.lower() not in s_lower
        ):
            LOGGER.info("plan fix: removed wrong launcher step: %s", s)
            continue
        if "search" in s_lower and "type" in s_lower and target_app.lower() in s_lower:
            LOGGER.info("plan fix: removed search-for-app step: %s", s)
            continue
        cleaned.append(s)

    if cleaned and target_app.lower() in cleaned[0].lower():
        first_lower = cleaned[0].lower()
        if any(v in first_lower for v in _OPEN_VERBS) and len(cleaned[0].split()) <= 5:
            cleaned[0] = f"Open {target_app}"

    if not has_open_step:
        has_nav_step = any(
            any(w in s.lower() for w in ("navigate", "go to", "search", "browse"))
            for s in cleaned
        )
        if not (has_nav_step and target_app.lower() in {"safari", "microsoft edge", "google chrome", "firefox", "arc"}):
            cleaned.insert(0, f"Open {target_app}")
            LOGGER.info("plan fix: prepended 'Open %s'", target_app)

    return cleaned


def _build_plan_prompt(state: LokiState, goal: str) -> str:
    user_parts: list[str] = []

    try:
        from src.graph.app_knowledge import AppKnowledge

        ak = AppKnowledge()
        app_ctx = ak.format_for_prompt(limit=40)
        user_parts.append(app_ctx)
    except Exception:
        pass

    user_parts.append(f"\nGoal: {goal}")

    try:
        from src.graph.site_knowledge import SiteKnowledge

        sk = SiteKnowledge()
        known_url = sk.resolve_site_url(goal)
        if known_url:
            user_parts.append(
                f"\nKNOWN SITE: The target is {known_url} — navigate directly, no need to Google."
            )
            domain = sk._extract_domain(known_url)
            creds = sk._find_credentials(domain)
            if creds and creds.get("username"):
                user_parts.append(
                    "CREDENTIALS: Stored locally — the agent has credentials for this site."
                )
    except Exception:
        pass

    session_ctx = state.get("session_context", "")
    if session_ctx:
        user_parts.append(f"\n{session_ctx}")

    cur_url = state.get("current_url", "")
    cur_title = state.get("page_title", "")
    if cur_url:
        user_parts.append(
            f"\nCURRENT BROWSER STATE:\n"
            f"  URL: {cur_url}\n"
            f"  Title: {cur_title}\n"
            f"  (The browser is already open — start from here, do NOT re-login if already authenticated.)"
        )

    replay_history = state.get("history", [])
    if replay_history:
        steps_summary = ", ".join(
            h.get("action", "?") + " " + h.get("target", "")[:30]
            for h in replay_history[-5:]
        )
        user_parts.append(
            f"\nPLAYBOOK ACTIONS ALREADY COMPLETED:\n"
            f"  {steps_summary}\n"
            f"  (These steps have been executed — plan only the REMAINING steps.)"
        )

    return "\n".join(user_parts)


def _normalize_steps(raw_steps: list[Any]) -> list[str]:
    steps: list[str] = []
    for s in raw_steps:
        if isinstance(s, str):
            steps.append(s)
        elif isinstance(s, dict):
            steps.append(str(s.get("description") or s.get("step") or s))
        else:
            steps.append(str(s))
    return steps


def _generate_plan_or_chat(user_prompt: str) -> tuple[str, list[str], str]:
    steps: list[str] = []
    grammar = load_grammar(PLAN_GRAMMAR)

    for attempt in range(_MAX_PLAN_ATTEMPTS):
        prompt = user_prompt
        if attempt == 1:
            prompt += (
                "\n\nIMPORTANT: Your previous automation response had too few steps. "
                "Break the goal into 3-8 separate verifiable milestone steps."
            )
        elif attempt == 2:
            prompt += (
                '\n\nCRITICAL: Output a JSON object like {"mode":"automation","steps":'
                '["step 1", "step 2", "step 3"]} or {"mode":"chat","reply":"..."}. '
                "Automation plans need at least 1 step."
            )

        raw = call_llm(PLAN_SYSTEM, prompt, num_predict=448, grammar=grammar)
        LOGGER.info("plan raw (attempt %d): %s", attempt + 1, raw[:300])

        parsed = extract_json(raw)
        mode = parsed.get("mode", "automation")

        if mode == "chat":
            reply = str(parsed.get("reply", "")).strip()
            if reply:
                return "chat", [], reply
            LOGGER.warning("plan: empty chat reply (attempt %d) — retrying", attempt + 1)
            continue

        steps = _normalize_steps(parsed.get("steps", []))
        if steps:
            return "automation", steps, ""

        if attempt < _MAX_PLAN_ATTEMPTS - 1:
            LOGGER.warning("plan: got %d step(s) (attempt %d) — retrying", len(steps), attempt + 1)

    return "automation", steps, ""


def _generate_automation_steps(user_prompt: str) -> list[str]:
    steps: list[str] = []
    grammar = load_grammar(AUTOMATION_STEPS_GRAMMAR)

    for attempt in range(_MAX_PLAN_ATTEMPTS):
        prompt = user_prompt
        if attempt == 1:
            prompt += (
                "\n\nIMPORTANT: Your previous response had too few steps. "
                "Break the goal into 2-8 separate verifiable milestone steps."
            )
        elif attempt == 2:
            prompt += (
                '\n\nCRITICAL: Output a JSON object like {"steps": ["step 1", "step 2"]}.'
            )

        raw = call_llm(AUTOMATION_PLAN_SYSTEM, prompt, num_predict=448, grammar=grammar)
        LOGGER.info("plan raw (automation, attempt %d): %s", attempt + 1, raw[:300])

        parsed = extract_json(raw)
        steps = _normalize_steps(parsed.get("steps", []))
        if steps:
            return steps

        if attempt < _MAX_PLAN_ATTEMPTS - 1:
            LOGGER.warning("plan: got 0 automation step(s) (attempt %d) — retrying", attempt + 1)

    return steps


def _fast_track_plan(goal: str, reason: str) -> dict[str, Any]:
    return {
        "plan": [goal],
        "plan_step": 0,
        "plan_reasoning": reason,
    }


def plan_node(state: LokiState) -> dict[str, Any]:
    if state.get("plan"):
        return {}

    goal = state.get("goal", "")
    LOGGER.info("plan: decomposing goal: %s", goal[:100])

    try:
        from src.graph.nodes.skill import _extract_direct_web_search_query, _is_safari_status_query
        from src.skills.calendar_skill import extract_calendar_event_from_goal
        from src.skills.mail_skill import extract_mail_intent_from_goal
        from src.skills.spotify_skill import extract_spotify_intent_from_goal

        installed_apps = state.get("installed_apps", [])

        if _looks_like_app_list_query(goal):
            LOGGER.info("plan: deterministic installed-apps answer — skipping LLM call entirely")
            mode, steps, reply = "chat", [], _answer_installed_apps(state)
        elif _extract_direct_web_search_query(goal):
            LOGGER.info("plan: direct web search detected — fast-tracking to skill_node")
            return _fast_track_plan(goal, "Direct web search via Safari skill")
        elif _is_safari_status_query(goal):
            LOGGER.info("plan: Safari status question detected — fast-tracking to skill_node")
            return _fast_track_plan(goal, "Describe open Safari tabs via Safari skill")
        elif extract_calendar_event_from_goal(goal):
            LOGGER.info("plan: direct calendar event creation detected — fast-tracking to skill_node")
            return _fast_track_plan(goal, "Direct calendar event creation via Calendar skill")
        elif extract_mail_intent_from_goal(goal):
            LOGGER.info("plan: direct Mail request detected — fast-tracking to skill_node")
            return _fast_track_plan(goal, "Direct email action via Mail skill")
        elif extract_spotify_intent_from_goal(goal):
            LOGGER.info("plan: direct Spotify request detected — fast-tracking to skill_node")
            return _fast_track_plan(goal, "Direct playback action via Spotify skill")
        elif _looks_like_automation(goal, installed_apps):
            LOGGER.info("plan: heuristic detected automation intent — bypassing chat routing")
            user_prompt = _build_plan_prompt(state, goal)
            mode, steps, reply = "automation", _generate_automation_steps(user_prompt), ""
            if not steps:
                mode, steps, reply = _generate_plan_or_chat(user_prompt)
        else:
            user_prompt = _build_plan_prompt(state, goal)
            mode, steps, reply = _generate_plan_or_chat(user_prompt)

        if mode == "chat" and reply:
            LOGGER.info("plan: routed to direct chat reply (%d chars), skipping automation loop", len(reply))
            on_message = state.get("on_message")
            if on_message:
                on_message("answer", reply)
            return {
                "plan": [],
                "plan_step": 0,
                "plan_reasoning": "chat",
                "done": True,
                "result": reply,
            }

        if not steps:
            steps = [goal]

        steps = _fix_plan(steps, goal, installed_apps)
        LOGGER.info("plan: %d steps: %s", len(steps), steps)

        on_message = state.get("on_message")
        if on_message:
            on_message("plan", "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps)))

        return {
            "plan": steps,
            "plan_step": 0,
            "plan_reasoning": f"Initial plan with {len(steps)} steps",
        }
    except Exception as exc:
        LOGGER.error("plan_node failed: %s", exc)
        return _fast_track_plan(goal, f"Planning failed ({exc}), using goal as single step")
