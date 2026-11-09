from __future__ import annotations

import logging
from typing import Any

from src.graph.config import LokiState
from src.graph.grammars import grammar_for_skill_candidates
from src.graph.llm import call_llm, extract_json
from src.graph.prompts import STEP_ROUTE_SYSTEM
from src.skills import all_skills, discover_skill_for_app, ensure_builtin_skills_loaded

LOGGER = logging.getLogger(__name__)


def _candidate_skills(step_text: str, installed_apps: list[str] | None = None) -> list:
    step_lower = step_text.lower()
    matched = [s for s in all_skills() if any(app in step_lower for app in s.app_names)]
    if matched:
        return matched
    for app in installed_apps or []:
        name = (app or "").strip()
        if not name:
            continue
        bare = name[:-4] if name.lower().endswith(".app") else name
        if bare and bare.lower() in step_lower:
            discovered = discover_skill_for_app(bare)
            if discovered:
                return [discovered]
    return []


def _format_step_context(state: LokiState) -> str:
    parts: list[str] = []
    for f in (state.get("findings") or [])[-3:]:
        kind = f.get("type", "finding")
        src = str(f.get("query_or_url", ""))[:80]
        content = str(f.get("content", ""))[:400]
        if content:
            parts.append(f"[{kind}] {src}: {content}")
    history = state.get("history") or []
    if history:
        last = history[-1]
        last_res = str(last.get("result", "")).strip()
        if last_res and "Error" not in last_res:
            parts.append(f"[previous_step_result] {last.get('action', '')}: {last_res[:400]}")
    return "\n".join(parts)


def _extract_intent(skill_name: str, step_text: str) -> tuple[str, dict[str, Any]] | None:
    # Imported lazily: the skill modules import src.graph.llm, which would be circular at load time.
    if skill_name == "calendar":
        from src.skills.calendar_skill import extract_calendar_event_from_goal

        params = extract_calendar_event_from_goal(step_text)
        return ("create_event", params) if params else None
    if skill_name == "mail":
        from src.skills.mail_skill import extract_mail_intent_from_goal

        return extract_mail_intent_from_goal(step_text)
    if skill_name == "spotify":
        from src.skills.spotify_skill import extract_spotify_intent_from_goal

        return extract_spotify_intent_from_goal(step_text)
    return None


def _try_deterministic_step_route(step_text: str, candidates: list) -> tuple[Any, str, dict[str, Any]] | None:
    by_name = {s.name: s for s in candidates}
    for name in ("calendar", "mail", "spotify"):
        if name not in by_name:
            continue
        try:
            match = _extract_intent(name, step_text)
        except Exception as exc:
            LOGGER.warning("route_node: %s intent extraction failed: %s", name, exc)
            continue
        if match:
            return by_name[name], match[0], match[1]
    return None


def route_node(state: LokiState) -> dict[str, Any]:
    ensure_builtin_skills_loaded()

    plan = state.get("plan", [])
    plan_step = state.get("plan_step", 0)
    if not plan or plan_step >= len(plan):
        return {"step_routed": False}

    step_text = str(plan[plan_step])

    candidates = _candidate_skills(step_text, state.get("installed_apps"))
    if not candidates:
        return {"step_routed": False}

    det_match = _try_deterministic_step_route(step_text, candidates)
    if det_match is not None:
        skill, intent_name, params = det_match
        skill_name = skill.name
    else:
        skills_context = "\n\n".join(s.describe() for s in candidates)
        ctx_block = _format_step_context(state)
        prompt = f"STEP: {step_text}\n"
        if ctx_block:
            prompt += f"\nRECENT FINDINGS / CONTEXT:\n{ctx_block}\n"
        prompt += f"\nAVAILABLE SKILLS:\n{skills_context}"

        try:
            raw = call_llm(
                STEP_ROUTE_SYSTEM,
                prompt,
                num_predict=256,
                temperature=0.0,
                grammar=grammar_for_skill_candidates(candidates),
            )
            decision = extract_json(raw)
        except Exception as exc:
            LOGGER.warning("route_node: routing call failed: %s", exc)
            return {"step_routed": False}

        skill_name = decision.get("skill", "none")
        intent_name = decision.get("intent", "none")
        params = decision.get("params", {}) or {}

        if skill_name in ("none", "", None) or intent_name in ("none", "", None):
            return {"step_routed": False}

        skill = next((s for s in candidates if s.name == skill_name), None)
        if skill is None:
            LOGGER.warning("route_node: unknown skill '%s'", skill_name)
            return {"step_routed": False}

    try:
        result_msg = skill.execute(intent_name, params)
        LOGGER.info(
            "route_node: step '%s' dispatched to %s.%s → %s",
            step_text[:40], skill_name, intent_name, result_msg,
        )
    except Exception as exc:
        LOGGER.warning(
            "route_node: execution failed (%s.%s): %s", skill_name, intent_name, exc
        )
        return {"step_routed": False}

    on_step = state.get("on_step")
    if on_step:
        try:
            on_step(intent_name, skill_name, result_msg[:120])
        except Exception:
            pass

    on_message = state.get("on_message")
    if on_message:
        try:
            on_message("step", f"{step_text[:60]} (via {skill_name})")
        except Exception:
            pass

    history = list(state.get("history", []))
    history.append({"action": intent_name, "target": skill_name, "result": result_msg})

    new_step = plan_step + 1
    updates: dict[str, Any] = {
        "step_routed": True,
        "plan_step": new_step,
        "history": history,
    }
    if new_step >= len(plan):
        updates["done"] = True
        updates["result"] = result_msg
    return updates
