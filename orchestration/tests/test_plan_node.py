from __future__ import annotations

import json

import pytest

import src.graph.nodes.plan as plan_mod
from src.graph import _after_plan


def _state(goal: str = "hello there", **extra) -> dict:
    base = {
        "goal": goal,
        "plan": [],
        "installed_apps": [],
        "session_context": "",
        "current_url": "",
        "page_title": "",
        "history": [],
        "on_message": None,
    }
    base.update(extra)
    return base


def test_chat_reply_short_circuits_without_automation_plan(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        plan_mod,
        "call_llm",
        lambda *a, **k: json.dumps({"mode": "chat", "reply": "Hey! I'm doing great, thanks."}),
    )

    result = plan_mod.plan_node(_state("hey, how's it going?"))

    assert result["done"] is True
    assert result["result"] == "Hey! I'm doing great, thanks."
    assert result["plan"] == []
    assert _after_plan(result) == "finish"


def test_chat_reply_notifies_on_message(monkeypatch: pytest.MonkeyPatch):
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        plan_mod,
        "call_llm",
        lambda *a, **k: json.dumps({"mode": "chat", "reply": "Paris."}),
    )

    plan_mod.plan_node(_state("what's the capital of France?", on_message=lambda t, c: messages.append((t, c))))

    assert messages == [("answer", "Paris.")]


def test_automation_goal_produces_plan_and_continues_to_skill(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        plan_mod,
        "call_llm",
        lambda *a, **k: json.dumps(
            {"mode": "automation", "steps": ["Open Notes", "Create a new note"]}
        ),
    )

    result = plan_mod.plan_node(_state("open notes and write something"))

    assert "done" not in result
    assert result["plan"] == ["Open Notes", "Create a new note"]
    assert _after_plan(result) == "skill"


def test_empty_chat_reply_retries_then_falls_back_to_automation(monkeypatch: pytest.MonkeyPatch):
    responses = iter(
        [
            json.dumps({"mode": "chat", "reply": ""}),
            json.dumps({"mode": "automation", "steps": ["Open Notes", "Create a new note"]}),
        ]
    )
    monkeypatch.setattr(plan_mod, "call_llm", lambda *a, **k: next(responses))

    result = plan_mod.plan_node(_state("open notes and write something"))

    assert "done" not in result
    assert result["plan"] == ["Open Notes", "Create a new note"]


@pytest.mark.parametrize(
    "goal,expected",
    [
        ("can you open the calculator app for me?", True),
        ("open notes and write a poem", True),
        ("Search Google for the weather in KL", True),
        ("Can you search up Spongebob and tell me a fun fact about it?", True),
        ("check my calendar", True),
        ("hey, how's it going?", False),
        ("what's the capital of France?", False),
        ("tell me a joke", False),
    ],
)
def test_looks_like_automation_heuristic(goal: str, expected: bool):
    assert plan_mod._looks_like_automation(goal, installed_apps=[]) is expected


def test_compound_search_and_tell_goal_decomposes_into_multi_step_plan(monkeypatch: pytest.MonkeyPatch):
    import src.graph.nodes.skill as skill_mod

    goal = "Can you search up Spongebob and tell me a fun fact about it?"
    assert skill_mod._extract_direct_web_search_query(goal) is None

    monkeypatch.setattr(
        plan_mod,
        "call_llm",
        lambda *a, **k: json.dumps(
            {
                "steps": [
                    "Search Google for Spongebob fun facts",
                    "Select a search result about Spongebob fun facts",
                    "Read the page and tell the user a fun fact about Spongebob",
                ]
            }
        ),
    )

    planned = plan_mod.plan_node(_state(goal))
    assert "done" not in planned
    assert len(planned["plan"]) == 3
    assert skill_mod.skill_node({**_state(goal), **planned}) == {}


@pytest.mark.parametrize(
    "goal,expected",
    [
        ("What applications do I have installed on my laptop right now?", True),
        ("what apps do I have?", True),
        ("which apps are installed on this mac?", True),
        ("list my apps", True),
        ("show me the apps I have", True),
        ("can you open the calculator app for me?", False),
        ("what's the capital of France?", False),
        ("what apps should I use for photo editing?", False),
    ],
)
def test_looks_like_app_list_query_heuristic(goal: str, expected: bool):
    assert plan_mod._looks_like_app_list_query(goal) is expected


def test_app_list_query_answers_deterministically_without_llm_call(monkeypatch: pytest.MonkeyPatch):

    def _boom(*a, **k):
        raise AssertionError("call_llm should not be invoked for a deterministic app-list query")

    from src.graph.app_knowledge import AppKnowledge

    monkeypatch.setattr(plan_mod, "call_llm", _boom)
    monkeypatch.setattr(AppKnowledge, "get_canonical_names", lambda self: ["Safari", "Notes", "Calculator"])

    result = plan_mod.plan_node(_state("What applications do I have installed on my laptop right now?"))

    assert result["done"] is True
    assert "Safari" in result["result"]
    assert "Notes" in result["result"]
    assert "Calculator" in result["result"]
    assert "don't have access" not in result["result"].lower()
    assert "do not have access" not in result["result"].lower()


def test_automation_heuristic_bypasses_chat_refusal(monkeypatch: pytest.MonkeyPatch):

    def fake_call_llm(system, prompt, **kwargs):
        assert system == plan_mod.AUTOMATION_PLAN_SYSTEM
        return json.dumps({"steps": ["Open Calculator"]})

    monkeypatch.setattr(plan_mod, "call_llm", fake_call_llm)

    result = plan_mod.plan_node(_state("can you open the calculator app for me?"))

    assert "done" not in result
    assert result["plan"][0] == "Open Calculator"


def test_automation_heuristic_falls_back_to_router_if_steps_empty(monkeypatch: pytest.MonkeyPatch):
    responses = iter(
        [
            json.dumps({"steps": []}),
            json.dumps({"steps": []}),
            json.dumps({"steps": []}),
            json.dumps({"mode": "automation", "steps": ["Open Calculator", "Wait for it to load"]}),
        ]
    )
    monkeypatch.setattr(plan_mod, "call_llm", lambda *a, **k: next(responses))

    result = plan_mod.plan_node(_state("can you open the calculator app for me?"))

    assert "done" not in result
    assert result["plan"] == ["Open Calculator", "Wait for it to load"]


def test_existing_plan_short_circuits_without_llm_call(monkeypatch: pytest.MonkeyPatch):
    def _boom(*a, **k):
        raise AssertionError("call_llm should not be invoked when a plan already exists")

    monkeypatch.setattr(plan_mod, "call_llm", _boom)

    result = plan_mod.plan_node(_state(plan=["already planned"]))

    assert result == {}
