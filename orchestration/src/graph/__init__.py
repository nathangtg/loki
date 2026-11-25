from __future__ import annotations

import functools
import logging
import uuid
from typing import Any, Callable, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from src.graph.checkpoint import (
    delete_state,
    get_checkpointer,
    inject_callbacks,
    load_state,
    runtime_callbacks,
    thread_config,
)
from src.graph.config import LokiState
from src.graph.nodes import (
    act_node,
    finish_node,
    observe_node,
    plan_node,
    reason_node,
    reflect_node,
    route_node,
    skill_node,
)

LOGGER = logging.getLogger(__name__)
_COMPILED_GRAPH = None


def _after_plan(state: LokiState) -> Literal["skill", "finish"]:
    if state.get("done"):
        return "finish"
    return "skill"


def _after_skill(state: LokiState) -> Literal["route", "finish"]:
    if state.get("done"):
        return "finish"
    return "route"


def _after_route(state: LokiState) -> Literal["route", "observe", "finish"]:
    if state.get("done"):
        return "finish"
    if state.get("step_routed"):
        return "route"
    return "observe"


def _after_reason(state: LokiState) -> Literal["act", "finish"]:
    if state.get("done"):
        return "finish"
    return "act"


def _after_reflect(state: LokiState) -> Literal["route", "finish"]:
    if state.get("done"):
        return "finish"
    if state.get("error"):
        return "finish"
    return "route"


def build_loki_graph():
    global _COMPILED_GRAPH
    if _COMPILED_GRAPH is not None:
        return _COMPILED_GRAPH

    g = StateGraph(LokiState)

    def _rt(fn):
        @functools.wraps(fn)
        def wrapper(state):
            return fn(inject_callbacks(state))

        return wrapper

    g.add_node("plan", _rt(plan_node))
    g.add_node("skill", _rt(skill_node))
    g.add_node("route", _rt(route_node))
    g.add_node("observe", _rt(observe_node))
    g.add_node("reason", _rt(reason_node))
    g.add_node("act", _rt(act_node))
    g.add_node("reflect", _rt(reflect_node))
    g.add_node("finish", _rt(finish_node))

    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", _after_plan)
    g.add_conditional_edges("skill", _after_skill)
    g.add_conditional_edges("route", _after_route)
    g.add_edge("observe", "reason")
    g.add_conditional_edges("reason", _after_reason)
    g.add_edge("act", "reflect")
    g.add_conditional_edges("reflect", _after_reflect)
    g.add_edge("finish", END)

    _COMPILED_GRAPH = g.compile(checkpointer=get_checkpointer())
    return _COMPILED_GRAPH


def _interrupt_question(final: dict[str, Any]) -> str | None:
    pending = final.get("__interrupt__")
    if not pending:
        return None
    value = getattr(pending[0], "value", pending[0])
    if isinstance(value, dict):
        return str(value.get("question", ""))
    return str(value)


def _finish_run(
    final: dict[str, Any],
    goal: str,
    job_id: str,
    memory: Any,
) -> dict[str, Any]:
    from src.graph.playbook import _extract_domain, record_playbook

    question = _interrupt_question(final)
    if question is not None:
        return {"status": "waiting_for_user", "question": question, "turns": final.get("turn", 0)}

    delete_state(job_id)

    full_history = final.get("history", [])
    final_url = final.get("current_url", "")
    final_domain = _extract_domain(final_url)

    if final.get("result") and not final.get("error"):
        memory.current_url = final_url
        memory.current_page_title = final.get("page_title", "")

        if final_domain:
            record_playbook(
                domain=final_domain,
                history=full_history,
                post_login_url=final_url,
            )
        elif final.get("_opened_app_name"):
            from src.graph.playbook import record_native_macro

            record_native_macro(
                goal=goal,
                app_name=str(final.get("_opened_app_name")),
                history=full_history,
            )

    return {
        "status": "ok",
        "message": final.get("result", ""),
        "turns": final.get("turn", 0),
    }


def resume_graph(
    job_id: str,
    answer: str,
    on_step: Callable[..., Any] | None = None,
    on_message: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    from src.memory import SessionMemory

    state = load_state(job_id)
    if state is None:
        return {"status": "error", "message": f"No paused state for job {job_id}"}
    goal = state.get("goal", "")
    graph = build_loki_graph()
    with runtime_callbacks(on_step=on_step, on_message=on_message):
        final = graph.invoke(
            Command(resume=answer),
            config=thread_config(job_id, recursion_limit=150),
        )
    return _finish_run(final, goal, job_id, SessionMemory())


def run_graph(
    goal: str,
    on_step: Callable[..., Any] | None = None,
    on_message: Callable[..., Any] | None = None,
    ask_user: Callable[..., Any] | None = None,
    job_id: str | None = None,
    interruptible: bool = False,
) -> dict[str, Any]:
    from src.graph.playbook import (
        extract_credentials_from_goal,
        get_native_macro,
        get_playbook_for_goal,
        replay_login,
        replay_native_macro,
    )
    from src.memory import SessionMemory

    job_id = job_id or uuid.uuid4().hex

    memory = SessionMemory()
    memory.record_command(goal)

    native_macro = get_native_macro(goal)
    if native_macro:
        ok_macro, macro_hist, macro_msg = replay_native_macro(native_macro, on_step=on_step)
        if ok_macro:
            LOGGER.info("Replayed native AXPath macro for goal '%s' in 0 LLM turns", goal[:60])
            return {
                "status": "ok",
                "message": macro_msg,
                "turns": 0,
            }

    graph = build_loki_graph()

    apps: list[str] = []
    try:
        from src.graph.app_knowledge import AppKnowledge

        apps = AppKnowledge().get_installed_apps()
    except Exception:
        pass

    starting_url = ""
    starting_title = ""
    browser_up = False

    replay_history: list[dict[str, Any]] = []
    domain, playbook = get_playbook_for_goal(goal)

    if playbook and playbook.get("login"):
        logged_in = memory.logged_in.get(domain) or ""
        already_on_site = domain and domain in starting_url

        if logged_in or already_on_site:
            LOGGER.info("Already on %s (logged_in=%s) — skipping playbook login", domain, logged_in or "on-site")
        else:
            creds: dict[str, str] = {}
            try:
                from src.graph.site_knowledge import SiteKnowledge

                creds = SiteKnowledge().resolve_credentials_for_domain(domain)
            except Exception:
                creds = {}
            if not (creds.get("username") and creds.get("password")):
                extracted = extract_credentials_from_goal(goal)
                if extracted.get("username"):
                    creds["username"] = extracted["username"]
                if extracted.get("password"):
                    creds["password"] = extracted["password"]
            if creds.get("username") and creds.get("password"):
                LOGGER.info("Replaying playbook for %s (%d login steps)", domain, len(playbook["login"]))
                replay_history = replay_login(playbook, creds, on_step=on_step)

                if replay_history and "Error" not in replay_history[-1].get("result", ""):
                    memory.logged_in[domain] = "confirmed"
            else:
                LOGGER.info("Playbook exists for %s but no credentials in goal — delegating to planner", domain)

    session_ctx = memory.get_state_only()

    with runtime_callbacks(on_step=on_step, on_message=on_message, ask_user=ask_user):
        final = graph.invoke(
            {
                "goal": goal,
                "interruptible": interruptible,
                "turn": 0,
                "history": replay_history,
                "plan": [],
                "plan_step": 0,
                "plan_reasoning": "",
                "next_action": {},
                "done": False,
                "current_url": starting_url,
                "page_title": starting_title,
                "page_text": "",
                "screen_elements": "",
                "installed_apps": apps,
                "shortcuts_raw": "",
                "browser_launched": browser_up,
                "findings": [],
                "step_routed": False,
                "result": "",
                "error": "",
                "session_context": session_ctx,
            },
            config=thread_config(job_id, recursion_limit=150),
        )

    return _finish_run(final, goal, job_id, memory)
