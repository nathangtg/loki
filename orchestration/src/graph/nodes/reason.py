from __future__ import annotations

import logging
from collections import Counter
from typing import Any
from urllib.parse import urlparse

from src.graph.config import MAX_TURNS, PROMPT_COMPRESSION_ENABLED, TERSE_SYSTEM_ENABLED, LokiState
from src.graph.grammars import extract_element_indices, grammar_for_step
from src.graph.llm import call_llm, extract_json
from src.graph.prompt_utils import (
    compress_elements,
    compress_page_text,
    narrow_elements_iterative,
    rank_elements,
)
from src.graph.prompts import REASON_SYSTEM, TERSE_REASON_SYSTEM

LOGGER = logging.getLogger(__name__)

_MAX_ELEMENTS = 25
_RANK_TOP_K = 10
_ITERATIVE_THRESHOLD = 40


def _build_history_summary(history: list[dict]) -> tuple[str, set[str]]:
    history_lines = []
    for i, h in enumerate(history[-4:]):
        res_short = str(h.get("result", ""))[:80]
        history_lines.append(f"  {i+1}. {h.get('action')} {h.get('target', '')[:40]} → {res_short}")
    history_block = "\n".join(history_lines) if history_lines else "  (none)"

    failed_targets: set[str] = set()
    if len(history) >= 3:
        target_counts = Counter(
            (h.get("action"), h.get("target", "")) for h in history[-8:]
        )
        for (act, tgt), count in target_counts.items():
            if count >= 3 and tgt:
                failed_targets.add(tgt)

        last3 = [(h.get("action"), h.get("target", "")) for h in history[-3:]]
        if last3[0] == last3[1] == last3[2] and last3[0][1]:
            LOGGER.warning("Loop detected: %s repeated 3x", last3[0])
            failed_targets.add(last3[0][1])

    return history_block, failed_targets


def _prepare_element_list(
    raw_elements: str, is_native: bool, current_step: str, goal: str
) -> str:
    if not raw_elements.strip():
        return ""

    lines = raw_elements.strip().split("\n")
    if len(lines) > _ITERATIVE_THRESHOLD:
        capped = narrow_elements_iterative(
            lines, current_step=current_step, goal=goal, top_k=_RANK_TOP_K
        )
    elif len(lines) > _MAX_ELEMENTS:
        if is_native:
            interactable = [line for line in lines if "[static]" not in line]
            static_labeled = [line for line in lines if "[static]" in line and line.strip()]
            budget = max(0, _MAX_ELEMENTS - len(interactable))
            capped = interactable[:_MAX_ELEMENTS] + static_labeled[:budget]
        else:
            form = [line for line in lines if any(k in line for k in ("<input", "<button", "<select", "<textarea"))]
            other = [line for line in lines if line not in form]
            budget = max(0, _MAX_ELEMENTS - len(form))
            capped = form + other[:budget]

        if len(capped) > _RANK_TOP_K:
            capped = rank_elements(capped, current_step=current_step, goal=goal, top_k=_RANK_TOP_K)
    else:
        capped = lines

    return "\n".join(compress_elements(capped))


def _format_findings(findings: list[dict]) -> str:
    if not findings:
        return ""
    parts: list[str] = []
    for f in findings[-3:]:
        kind = f.get("type", "read")
        head = str(f.get("query_or_url", ""))[:80]
        content = str(f.get("content", ""))[:900]
        parts.append(f"[{kind}] {head}\n{content}")
    return "\n\n".join(parts)


def _assemble_prompt(
    state: LokiState,
    current_step: str,
    is_native: bool,
    history_block: str,
    elements: str,
    failed_targets: set[str],
) -> str:
    cur_url = state.get("current_url", "")
    page_text = state.get("page_text", "")
    goal = state.get("goal", "")

    user = ""
    if is_native:
        user += "MODE: native desktop app (use open_app, click, type_text, key_combo — NOT navigate or read_page)\n"
        try:
            from src.graph.app_knowledge import AppKnowledge

            ak = AppKnowledge()
            user += f"\n{ak.format_for_prompt(limit=40)}\n"
        except Exception:
            pass
        user += f"\nGOAL: {goal}\n"
        shortcuts = state.get("shortcuts_raw", "")
        if shortcuts:
            user += f"\nKEYBOARD SHORTCUTS:\n{shortcuts}\n"
    else:
        user += f"GOAL: {goal}\n"
        try:
            from src.graph.site_knowledge import SiteKnowledge

            sk = SiteKnowledge()
            known_url = sk.resolve_site_url(goal)
            if known_url and urlparse(known_url).netloc != urlparse(cur_url or "").netloc:
                user += f"\nTARGET URL: {known_url}\n"

            cred_ctx = sk.get_credential_context(cur_url, elements)
            if cred_ctx:
                user += f"\n{cred_ctx}\n"
                LOGGER.info("reason: injected credentials for %s", cur_url[:40])
        except Exception as exc:
            LOGGER.debug("Site knowledge lookup failed: %s", exc)

    findings_block = _format_findings(state.get("findings", []))
    if findings_block:
        user += f"\nRESEARCH FINDINGS:\n{findings_block}\n"

    user_answer = state.get("user_answer", "")
    if user_answer:
        user += f"\nUSER ANSWER: {user_answer}\n"

    if current_step:
        user += f"\nCURRENT STEP: {current_step}\n"
    user += f"\nACTIONS SO FAR:\n{history_block}\n"
    if not is_native:
        user += f"\nURL: {cur_url}\n"
        user += f"Title: {state.get('page_title', '(none)')}\n"

    step_lower = current_step.lower() if current_step else ""
    skip_page_text = "click" in step_lower and "navigate" not in step_lower and bool(elements.strip())
    if page_text.strip() and not skip_page_text:
        text_for_prompt = page_text[:1500]
        if PROMPT_COMPRESSION_ENABLED:
            text_for_prompt = compress_page_text(text_for_prompt)
        user += f"\nPAGE TEXT:\n{text_for_prompt[:500]}\n"

    if elements.strip():
        user += f"\nINTERACTIVE ELEMENTS:\n{elements}\n"

    ui_delta = state.get("ui_delta_summary", "")
    if ui_delta:
        user += f"\nUI DELTA: {ui_delta}\n"

    if failed_targets:
        avoid_list = ", ".join(sorted(failed_targets))
        user += f"\nWARNING: You already tried {avoid_list} multiple times without progress. DO NOT click those again. Pick a DIFFERENT element.\n"

    history = state.get("history", [])
    if history:
        last_entry = history[-1]
        if last_entry.get("action") == "type_text" and "pressed return" not in str(last_entry.get("result", "")).lower():
            user += (
                '\nIMPORTANT: You JUST typed text in the previous turn. '
                'If it was a search or form input, press return now with {"action":"key_combo","combo":"return"}.\n'
            )
        elif (
            last_entry.get("action") == "open_app"
            and "error" not in str(last_entry.get("result", "")).lower()
            and len(state.get("plan") or []) <= 1
        ):
            user += (
                '\nIMPORTANT: You ALREADY opened the app in ACTIONS SO FAR and the goal is complete. '
                'Do NOT call open_app again — output {"action":"done","summary":"Opened application."}.\n'
            )

    user += "\nNext JSON action:"
    return user


def reason_node(state: LokiState) -> dict[str, Any]:
    turn = state.get("turn", 0)
    if turn >= MAX_TURNS:
        return {
            "next_action": {"action": "fail", "reason": "Max turns reached"},
            "done": True,
        }

    plan = state.get("plan", [])
    plan_step = state.get("plan_step", 0)
    current_step = plan[plan_step] if plan and plan_step < len(plan) else ""

    history = state.get("history", [])
    history_block, failed_targets = _build_history_summary(history)

    cur_url = state.get("current_url", "")
    is_native = not bool(cur_url)
    raw_elements = state.get("screen_elements", "")
    elements = _prepare_element_list(raw_elements, is_native, current_step, state.get("goal", ""))

    user = _assemble_prompt(state, current_step, is_native, history_block, elements, failed_targets)

    on_message = state.get("on_message")
    step_text = current_step or state.get("goal", "")
    if on_message:
        on_message("thinking", step_text)

    screenshot_b64 = state.get("screenshot_b64", "")
    images = [screenshot_b64] if screenshot_b64 else None
    system_prompt = TERSE_REASON_SYSTEM if TERSE_SYSTEM_ENABLED else REASON_SYSTEM

    LOGGER.info(
        "reason prompt: %d + %d = %d chars, vision=%s",
        len(system_prompt),
        len(user),
        len(system_prompt) + len(user),
        bool(images),
    )

    valid_indices = extract_element_indices(elements)
    grammar = grammar_for_step(
        current_step,
        state.get("installed_apps"),
        valid_indices=valid_indices or None,
    )
    try:
        raw = call_llm(
            system_prompt,
            user,
            num_predict=512,
            images=images,
            temperature=0.0,
            grammar=grammar,
        )
        LOGGER.info("reason raw: %s", raw[:200])
        decision = extract_json(raw)

        if not decision:
            LOGGER.warning("reason: unparseable response, forcing read_page")
            return {
                "next_action": {"action": "read_page"},
                "done": False,
                "turn": turn + 1,
            }

        LOGGER.info("reason decision: %s", decision)
        is_done = decision.get("action") in ("done", "fail")
        update: dict[str, Any] = {"next_action": decision, "done": is_done, "turn": turn + 1}
        if is_done:
            update["result"] = str(decision.get("summary") or decision.get("reason") or "")
        return update

    except Exception as exc:
        LOGGER.error("reason node failed: %s", exc)
        return {"next_action": {"action": "fail", "reason": str(exc)}, "done": True}
