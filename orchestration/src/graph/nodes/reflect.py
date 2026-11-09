from __future__ import annotations

import logging
import re
from typing import Any

from src.graph.config import LokiState
from src.graph.grammars import REFLECT_GRAMMAR, load_grammar
from src.graph.llm import call_llm, extract_json
from src.graph.prompts import REFLECT_SYSTEM

LOGGER = logging.getLogger(__name__)

_FAILURE_WORDS = ("error", "no element", "could not", "failed", "unknown application", "timed out")


def _is_failed_result(res_text: str) -> bool:
    low = (res_text or "").lower()
    return any(w in low for w in _FAILURE_WORDS)


def _apply_plan_adjustment(
    plan: list[str],
    plan_step: int,
    decision: dict[str, Any],
) -> tuple[list[str], int, bool]:
    adjust = str(decision.get("adjust") or "none").strip().lower()
    raw_new = decision.get("new_steps") or []
    new_steps = [str(s).strip() for s in raw_new if str(s).strip()]
    updated_plan = list(plan)
    updated_step = plan_step
    goal_complete = bool(decision.get("goal_complete", False))

    if adjust == "insert" and new_steps:
        updated_plan[plan_step + 1 : plan_step + 1] = new_steps
        updated_step = min(plan_step + 1, len(updated_plan) - 1)
    elif adjust == "replace" and new_steps:
        updated_plan = updated_plan[:plan_step] + new_steps
        updated_step = plan_step
    elif adjust == "skip":
        if plan_step + 1 < len(updated_plan):
            updated_step = plan_step + 1
        else:
            goal_complete = True
    elif decision.get("step_complete") and plan_step + 1 < len(updated_plan):
        updated_step = plan_step + 1

    return updated_plan, updated_step, goal_complete

_BEYOND_OPEN_GOAL_RE = re.compile(
    r"\b(search|look\s+up|google|navigate|go\s+to|browse|type|write|compose|click|tap|"
    r"play|send|reply|find|download|upload|install|and\s+then|then)\b",
    re.IGNORECASE,
)


def _goal_is_only_open_app(goal: str) -> bool:
    return not _BEYOND_OPEN_GOAL_RE.search(goal or "")


_ANSWER_SEEKING_GOAL_RE = re.compile(
    r"\b(tell\s+me|fun\s+fact|what\s+is|what's|who\s+is|how\s+many|summarize|explain|read\s+.*and|find\s+out|give\s+me\s+a\s+fact)\b",
    re.IGNORECASE,
)


def _goal_needs_spoken_answer(goal: str) -> bool:
    return bool(_ANSWER_SEEKING_GOAL_RE.search(goal or ""))


def _synthesize_answer_or_complete(state: LokiState, history: list[dict[str, Any]]) -> str:
    from src.audio.tts.system import humanize_goal_completion

    goal = state.get("goal", "")
    page_text = (state.get("page_text") or "").strip()
    elements = (state.get("screen_elements") or "").strip()
    if _goal_needs_spoken_answer(goal) and (page_text or elements):
        try:
            context = page_text[:2500] if page_text else elements[:2000]
            answer = call_llm(
                system=(
                    "You are Loki, a warm, casual, and upbeat voice assistant on macOS. "
                    "Using the on-screen page content below, directly answer the user's request "
                    "(such as selecting and telling them a specific fun fact or detail from the page) "
                    "in 1-2 short, natural spoken sentences (no markdown, no bullet points, no URLs)."
                ),
                user=f"Goal: {goal}\n\nOn-screen content:\n{context}",
                num_predict=160,
                temperature=0.2,
            ).strip()
            if answer:
                return answer
        except Exception as exc:
            LOGGER.debug("reflect: answer synthesis failed: %s", exc)
    return humanize_goal_completion(goal, history=history)


def reflect_node(state: LokiState) -> dict[str, Any]:
    if state.get("done"):
        return {}

    history = state.get("history", [])
    if not history:
        return {}

    plan = list(state.get("plan", []))
    plan_step = state.get("plan_step", 0)

    last = history[-1]
    last_action = last.get("action", "")
    last_result = str(last.get("result", ""))[:200]
    last_result_lower = last_result.lower()

    if plan_len := len(plan):
        if (
            plan_len == 1
            and last_action == "open_app"
            and "error" not in last_result_lower
            and _goal_is_only_open_app(state.get("goal", ""))
        ):
            from src.audio.tts.system import humanize_goal_completion

            LOGGER.info("reflect: single-step open_app succeeded — marking goal complete")
            return {
                "done": True,
                "result": humanize_goal_completion(state.get("goal", ""), history=history),
            }

    if last_action in {"wait", "read_page", "scroll", "key_combo"}:
        LOGGER.info("reflect: fast-continue after '%s'", last_action)
        return {}

    failed = _is_failed_result(last_result_lower)
    if failed:
        consecutive_failures = 0
        for h in reversed(history[-4:]):
            if _is_failed_result(str(h.get("result", ""))):
                consecutive_failures += 1
            else:
                break

        if consecutive_failures >= 2 and plan:
            cur_step_text = plan[plan_step] if plan_step < len(plan) else ""
            recent_lines = "\n".join(
                f"- {h.get('action')}: {h.get('target', '')} -> {str(h.get('result', ''))[:100]}"
                for h in history[-4:]
            )
            prompt = (
                f"GOAL: {state.get('goal', '')}\n"
                f"CURRENT PLAN: {plan}\n"
                f"CURRENT STEP ({plan_step + 1}/{len(plan)}): {cur_step_text}\n"
                f"RECENT FAILED ACTIONS:\n{recent_lines}"
            )
            try:
                raw = call_llm(
                    REFLECT_SYSTEM,
                    prompt,
                    num_predict=256,
                    temperature=0.0,
                    grammar=load_grammar(REFLECT_GRAMMAR),
                )
                decision = extract_json(raw)
                if decision:
                    new_plan, new_step, is_done = _apply_plan_adjustment(plan, plan_step, decision)
                    LOGGER.info(
                        "reflect: LLM plan adjustment '%s' -> step %d/%d",
                        decision.get("adjust", "none"),
                        new_step + 1,
                        len(new_plan),
                    )
                    updates: dict[str, Any] = {"plan": new_plan, "plan_step": new_step}
                    if is_done:
                        updates["done"] = True
                        updates["result"] = _synthesize_answer_or_complete(state, history)
                    return updates
            except Exception as exc:
                LOGGER.debug("reflect: LLM recovery reflection skipped: %s", exc)

        LOGGER.info(
            "reflect: last action '%s' reported failure — retrying step %d without LLM call",
            last_action,
            plan_step + 1,
        )
        return {}

    if last_action in {"open_app", "navigate", "click", "type_content", "web_search", "read_url", "write_content"} or (
        last_action == "type_text" and "pressed return" in last_result_lower
    ):
        if plan_len := len(plan):
            if plan_step < plan_len - 1:
                new_step = plan_step + 1
                LOGGER.info(
                    "reflect: deterministic advance step %d→%d after '%s'",
                    plan_step + 1,
                    new_step + 1,
                    last_action,
                )
                return {"plan_step": new_step}
            if not _goal_needs_spoken_answer(state.get("goal", "")):
                LOGGER.info("reflect: final step '%s' succeeded — marking goal complete", last_action)
                return {
                    "done": True,
                    "result": _synthesize_answer_or_complete(state, history),
                }
        return {}

    return {}
