from __future__ import annotations

import logging
import re
from typing import Any

from src.graph.config import LokiState
from src.graph.grammars import grammar_for_skill_candidates
from src.graph.llm import call_llm, extract_json
from src.graph.prompts import SKILL_ROUTE_SYSTEM
from src.skills import all_skills, discover_skill_for_app, ensure_builtin_skills_loaded
from src.skills.registry import find_skill_by_name

LOGGER = logging.getLogger(__name__)

_DIRECT_WEB_SEARCH_RE = re.compile(
    r"^(?:hey\s+loki[,\s]*|loki[,\s]*|please\s+|can\s+you\s+(?:please\s+)?|could\s+you\s+(?:please\s+)?)*"
    r"(?:search(?:\s+(?:google|safari|the\s+web|online|up))?(?:\s+for)?|look\s+up|google)\s+(.+?)"
    r"(?:\s+(?:in|on)\s+(?:safari|google|the\s+browser|edge|chrome))?"
    r"(?:\s+for\s+me)?(?:\s+please)?$",
    re.IGNORECASE,
)
_DIRECT_WEATHER_RE = re.compile(
    r"^(?:hey\s+loki[,\s]*|loki[,\s]*|please\s+|can\s+you\s+(?:please\s+)?)?"
    r"(?:what(?:'s|\s+is)\s+the\s+weather|how(?:'s|\s+is)\s+the\s+weather|check\s+(?:the\s+)?weather)\s+(?:in|at|for)\s+(.+?)"
    r"(?:\s+right\s+now|\s+today|\s+for\s+me)?$",
    re.IGNORECASE,
)
_NON_BROWSER_APP_RE = re.compile(
    r"\b("
    r"spotify|notes|mail|calendar|reminders|finder|photos|messages|calculator|terminal|"
    r"contacts|music|preview|excel|pages|numbers|keynote|teams|zoom|system\s+settings|clipboard|spotlight"
    r")\b",
    re.IGNORECASE,
)
_COMPOUND_SEARCH_FOLLOWUP_RE = re.compile(
    r"(?:"
    r"\band\s+(?:then\s+)?(?:tell|read|find|click|select|open|show|summarize|explain|give|pick|choose|check|get|see|write|type|save|extract)\b"
    r"|\bthen\s+(?:tell|read|find|click|select|open|show|summarize|explain|give|pick|choose|check|get|see)\b"
    r"|\b(?:tell|give|read|find|show)\s+me\s+(?:a|an|the|some)?\s*(?:fun\s+fact|fact|summary|answer|info|information|details?)\b"
    r"|\b(?:login|log\s+in)\b"
    r")",
    re.IGNORECASE,
)


def _is_compound_web_goal(goal: str) -> bool:
    clean = " ".join((goal or "").split()).strip()
    return bool(_COMPOUND_SEARCH_FOLLOWUP_RE.search(clean))


_SAFARI_STATUS_RE = re.compile(
    r"\bsafari\b.*\b(?:what|which)\b|\b(?:what|which)\b.*\bsafari\b|"
    r"\b(?:what|which)\b.*\b(?:tabs?|windows?)\b.*\bopen\b|\b(?:list|read\s+out)\b.*\bsafari\b",
    re.IGNORECASE,
)
_SAFARI_STATUS_TOPIC_RE = re.compile(
    r"\b(?:open|tabs?|windows?|showing|displayed|on\s+(?:my\s+)?(?:screen|safari)|looking\s+at|viewing)\b",
    re.IGNORECASE,
)
_INFO_QUESTION_RE = re.compile(
    r"^(?:(?:hey\s+)?loki[,\s]*)?(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
    r"(?:tell\s+me|what|which|who|where|how\s+many|is\s+there|are\s+there|describe|read\s+out)\b",
    re.IGNORECASE,
)


def _is_safari_status_query(goal: str) -> bool:
    clean = " ".join((goal or "").split()).strip()
    return (
        bool(_SAFARI_STATUS_RE.search(clean))
        and bool(_SAFARI_STATUS_TOPIC_RE.search(clean))
        and not _is_compound_web_goal(clean)
    )


def _is_info_question(goal: str) -> bool:
    return bool(_INFO_QUESTION_RE.search(" ".join((goal or "").split()).strip()))


def _extract_direct_web_search_query(goal: str) -> str | None:
    clean = " ".join((goal or "").split()).strip().rstrip(".!?")
    if not clean or _NON_BROWSER_APP_RE.search(clean) or _is_compound_web_goal(clean):
        return None
    m_weather = _DIRECT_WEATHER_RE.match(clean)
    if m_weather:
        loc = m_weather.group(1).strip()
        return f"weather in {loc}" if loc else None
    m_search = _DIRECT_WEB_SEARCH_RE.match(clean)
    if m_search:
        q = m_search.group(1).strip()
        return q if q else None
    return None


def _matched_hardcoded_skills(goal: str, plan: list[str] | None = None) -> list:
    goal_lower = (goal or "").lower()
    is_multi_step = bool(plan and len(plan) > 1) or _is_compound_web_goal(goal)
    matched = [
        s
        for s in all_skills()
        if any(app in goal_lower for app in s.app_names)
        and not (is_multi_step and s.name == "web_search")
    ]
    if (
        not matched
        and not is_multi_step
        and any(k in goal_lower for k in ("search", "look up", "weather", "forecast"))
    ):
        ws = find_skill_by_name("web_search")
        if ws:
            matched.append(ws)
    if not any(s.name == "calendar" for s in matched) and any(
        k in goal_lower for k in ("schedule", "appointment", "calendar", "free slot", "create an event", "add an event", "book a ")
    ):
        cal = find_skill_by_name("calendar")
        if cal:
            matched.append(cal)
    return matched


def _mentioned_installed_app(goal: str, installed_apps: list[str]) -> str | None:
    goal_lower = (goal or "").lower()
    for app in installed_apps:
        name = (app or "").strip()
        if not name:
            continue
        bare = name[:-4] if name.lower().endswith(".app") else name
        if bare and bare.lower() in goal_lower:
            return bare
    return None


def _execute_and_record(
    state: LokiState,
    skill_name: str,
    intent_name: str,
    params: dict[str, Any],
    target_label: str | None = None,
) -> dict[str, Any] | None:
    skill = find_skill_by_name(skill_name)
    if skill is None:
        return None
    try:
        result_msg = skill.execute(intent_name, params)
        LOGGER.info("skill_node: executed %s.%s → %s", skill_name, intent_name, result_msg[:120])
    except Exception as exc:
        LOGGER.warning("skill_node: %s.%s failed: %s", skill_name, intent_name, exc)
        return None

    on_step = state.get("on_step")
    if on_step:
        try:
            on_step(intent_name, skill_name, result_msg[:120])
        except Exception:
            pass

    history = list(state.get("history", []))
    history.append({"action": intent_name, "target": target_label or skill_name, "result": result_msg})
    return {
        "done": True,
        "result": result_msg,
        "history": history,
    }


def skill_node(state: LokiState) -> dict[str, Any]:
    ensure_builtin_skills_loaded()
    goal = state.get("goal", "")

    direct_query = _extract_direct_web_search_query(goal)
    if direct_query:
        res = _execute_and_record(state, "web_search", "search_web", {"query": direct_query}, target_label=direct_query)
        if res:
            return res

    if _is_safari_status_query(goal):
        res = _execute_and_record(state, "web_search", "describe_open_tabs", {}, target_label="Safari")
        if res:
            return res

    try:
        from src.skills.calendar_skill import extract_calendar_event_from_goal

        cal_params = extract_calendar_event_from_goal(goal)
        if cal_params:
            res = _execute_and_record(state, "calendar", "create_event", cal_params)
            if res:
                return res
    except Exception as exc:
        LOGGER.warning("skill_node: fast-path calendar failed: %s", exc)

    try:
        from src.skills.mail_skill import extract_mail_intent_from_goal

        mail_match = extract_mail_intent_from_goal(goal)
        if mail_match:
            res = _execute_and_record(state, "mail", mail_match[0], mail_match[1])
            if res:
                return res
    except Exception as exc:
        LOGGER.warning("skill_node: fast-path mail failed: %s", exc)

    try:
        from src.skills.spotify_skill import extract_spotify_intent_from_goal

        sp_match = extract_spotify_intent_from_goal(goal)
        if sp_match:
            res = _execute_and_record(state, "spotify", sp_match[0], sp_match[1])
            if res:
                return res
    except Exception as exc:
        LOGGER.warning("skill_node: fast-path spotify failed: %s", exc)

    candidates = _matched_hardcoded_skills(goal, state.get("plan"))
    if not candidates:
        mentioned_app = _mentioned_installed_app(goal, state.get("installed_apps", []))
        if mentioned_app and _is_info_question(goal):
            # Generated .sdef skills only expose commands that act on an app; they cannot
            # answer a question, so let the UI loop read the screen and reply instead.
            LOGGER.info("skill_node: info question — skipping discovered %s skill", mentioned_app)
        elif mentioned_app:
            discovered = discover_skill_for_app(mentioned_app)
            if discovered:
                candidates = [discovered]

    if not candidates:
        return {}

    skills_context = "\n\n".join(s.describe() for s in candidates)
    prompt = f"GOAL: {goal}\n\nAVAILABLE SKILLS:\n{skills_context}"

    try:
        raw = call_llm(
            SKILL_ROUTE_SYSTEM,
            prompt,
            num_predict=256,
            temperature=0.0,
            grammar=grammar_for_skill_candidates(candidates),
        )
        decision = extract_json(raw)
    except Exception as exc:
        LOGGER.warning("skill_node: routing call failed: %s", exc)
        return {}

    skill_name = decision.get("skill", "none")
    intent_name = decision.get("intent", "none")
    params = decision.get("params", {}) or {}

    if skill_name in ("none", "", None) or intent_name in ("none", "", None):
        return {}

    skill = next((s for s in candidates if s.name == skill_name), None)
    if skill is None:
        LOGGER.warning("skill_node: unknown skill '%s'", skill_name)
        return {}

    try:
        result_msg = skill.execute(intent_name, params)
        LOGGER.info("skill_node: executed %s.%s → %s", skill_name, intent_name, result_msg)
    except Exception as exc:
        LOGGER.warning("skill_node: execution failed (%s.%s): %s", skill_name, intent_name, exc)
        return {}

    on_step = state.get("on_step")
    if on_step:
        try:
            on_step(intent_name, skill_name, result_msg[:120])
        except Exception:
            pass

    history = list(state.get("history", []))
    history.append({"action": intent_name, "target": skill_name, "result": result_msg})

    return {
        "done": True,
        "result": result_msg,
        "history": history,
    }
