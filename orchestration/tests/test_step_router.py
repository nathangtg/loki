from __future__ import annotations

import json

import pytest

import src.graph.nodes.route as route_mod
from src.graph import _after_reflect, _after_route, _after_skill


class _FakeSkill:
    name = "notes"
    app_names = {"notes", "apple notes"}

    def __init__(self, result: str = "Note created.", fail: bool = False):
        self.result = result
        self.fail = fail
        self.calls: list = []

    def describe(self) -> str:
        return "skill 'notes' — create_note(title, body)"

    def execute(self, intent: str, params: dict) -> str:
        self.calls.append((intent, params))
        if self.fail:
            raise RuntimeError("boom")
        return self.result


def _state(**extra) -> dict:
    base = {
        "goal": "research cats and take notes",
        "plan": [
            "Search the web for cats",
            "Create a note in Notes titled Cats with body meow",
        ],
        "plan_step": 0,
        "history": [],
        "findings": [],
        "on_step": None,
        "on_message": None,
    }
    base.update(extra)
    return base


def _llm_returns(monkeypatch: pytest.MonkeyPatch, payload: dict):
    monkeypatch.setattr(
        route_mod, "call_llm", lambda *a, **k: json.dumps(payload)
    )


def test_route_passes_through_when_no_plan():
    assert route_mod.route_node({"plan": [], "plan_step": 0}) == {"step_routed": False}


def test_route_passes_through_when_step_mentions_no_skill_app(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(route_mod, "all_skills", lambda: [_FakeSkill()])

    out = route_mod.route_node(_state(plan_step=0))

    assert out == {"step_routed": False}


def test_route_falls_back_when_llm_says_none(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(route_mod, "all_skills", lambda: [_FakeSkill()])
    _llm_returns(monkeypatch, {"skill": "none", "intent": "none", "params": {}})

    out = route_mod.route_node(_state(plan_step=1))

    assert out == {"step_routed": False}


def test_route_falls_back_when_llm_call_fails(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(route_mod, "all_skills", lambda: [_FakeSkill()])

    def boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(route_mod, "call_llm", boom)

    out = route_mod.route_node(_state(plan_step=1))

    assert out == {"step_routed": False}


def test_route_falls_back_when_skill_execute_raises(monkeypatch: pytest.MonkeyPatch):
    skill = _FakeSkill(fail=True)
    monkeypatch.setattr(route_mod, "all_skills", lambda: [skill])
    _llm_returns(
        monkeypatch,
        {"skill": "notes", "intent": "create_note", "params": {"title": "Cats", "body": "meow"}},
    )

    out = route_mod.route_node(_state(plan_step=1))

    assert out == {"step_routed": False}


def test_route_falls_back_on_unknown_skill_name(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(route_mod, "all_skills", lambda: [_FakeSkill()])
    _llm_returns(monkeypatch, {"skill": "does-not-exist", "intent": "create_note", "params": {}})

    out = route_mod.route_node(_state(plan_step=1))

    assert out == {"step_routed": False}


def test_route_dispatches_middle_step_and_advances(monkeypatch: pytest.MonkeyPatch):
    skill = _FakeSkill("Note 'Cats' created.")
    monkeypatch.setattr(route_mod, "all_skills", lambda: [skill])
    _llm_returns(
        monkeypatch,
        {"skill": "notes", "intent": "create_note", "params": {"title": "Cats", "body": "meow"}},
    )

    steps: list = []
    messages: list = []
    out = route_mod.route_node(
        _state(
            plan=[
                "Search the web for cats",
                "Create a note in Notes titled Cats with body meow",
                "Verify the note was created",
            ],
            plan_step=1,
            on_step=lambda *a: steps.append(a),
            on_message=lambda *m: messages.append(m),
        )
    )

    assert out["step_routed"] is True
    assert out["plan_step"] == 2
    assert "done" not in out
    assert out["history"][-1] == {
        "action": "create_note",
        "target": "notes",
        "result": "Note 'Cats' created.",
    }
    intent, params = skill.calls[0]
    assert intent == "create_note"
    assert params == {"title": "Cats", "body": "meow"}
    assert steps and messages


def test_route_dispatches_final_step_and_finishes(monkeypatch: pytest.MonkeyPatch):
    skill = _FakeSkill("Note 'Cats' created.")
    monkeypatch.setattr(route_mod, "all_skills", lambda: [skill])
    _llm_returns(
        monkeypatch,
        {"skill": "notes", "intent": "create_note", "params": {"title": "Cats", "body": "meow"}},
    )

    out = route_mod.route_node(_state(plan=["Create a note in Notes titled Cats"], plan_step=0))

    assert out["done"] is True
    assert out["result"] == "Note 'Cats' created."
    assert out["plan_step"] == 1


def test_after_skill_routes_through_route_node():
    assert _after_skill({}) == "route"
    assert _after_skill({"done": True}) == "finish"


def test_after_route_loops_on_dispatch_and_falls_through_to_observe():
    assert _after_route({"step_routed": True}) == "route"
    assert _after_route({"step_routed": False}) == "observe"
    assert _after_route({}) == "observe"
    assert _after_route({"done": True, "step_routed": True}) == "finish"


def test_after_reflect_routes_through_route_node():
    assert _after_reflect({}) == "route"
    assert _after_reflect({"error": "x"}) == "finish"
    assert _after_reflect({"done": True}) == "finish"


def test_graph_wires_route_node_into_the_loop():
    from src.graph import build_loki_graph

    graph = build_loki_graph()
    nodes = set(graph.get_graph().nodes.keys())

    assert {"plan", "skill", "route", "observe", "reason", "act", "reflect", "finish"} <= nodes
    assert build_loki_graph() is graph


def test_route_node_uses_deterministic_calendar_fast_path_without_llm(
    monkeypatch: pytest.MonkeyPatch,
):
    class _FakeCalendarSkill:
        name = "calendar"
        app_names = {"calendar"}

        def __init__(self):
            self.calls: list = []

        def describe(self) -> str:
            return "calendar"

        def execute(self, intent: str, params: dict) -> str:
            self.calls.append((intent, params))
            return "Scheduled!"

    cal_skill = _FakeCalendarSkill()
    monkeypatch.setattr(route_mod, "all_skills", lambda: [cal_skill])

    def _forbid_llm(*a, **k):
        raise AssertionError("call_llm should not be called on deterministic calendar step")

    monkeypatch.setattr(route_mod, "call_llm", _forbid_llm)

    out = route_mod.route_node(
        _state(
            plan=["Create an event in Calendar tomorrow from 9am to 10am called Standup"],
            plan_step=0,
        )
    )
    assert out["step_routed"] is True
    assert out["done"] is True
    assert cal_skill.calls and cal_skill.calls[0][0] == "create_event"
    assert cal_skill.calls[0][1]["title"] == "Standup"


def test_site_knowledge_masks_password_and_act_resolves_placeholder(
    monkeypatch: pytest.MonkeyPatch,
):
    import src.graph.nodes.act as act_mod
    import src.graph.site_knowledge as sk_mod

    sk = sk_mod.SiteKnowledge()
    monkeypatch.setattr(
        sk,
        "_data",
        {
            "sites": {},
            "credentials": {"portal.example.edu": {"username": "student"}},
            "navigation": {},
        },
    )
    monkeypatch.setattr(
        sk_mod,
        "_get_keychain_password",
        lambda domain, username="": "FAKE-KEYCHAIN-SECRET-FOR-TESTING" if "example.edu" in domain else None,
    )

    ctx = sk.get_credential_context(
        "https://portal.example.edu/login",
        '[00] <input type="text"> username\n[01] <input type="password"> password',
    )
    assert "{{password}}" in ctx
    assert "FAKE-KEYCHAIN-SECRET-FOR-TESTING" not in ctx

    typed_values: list[str] = []

    class _FakeTypeTool:
        def _run(self, val: str) -> str:
            typed_values.append(val)
            return f"Typed: '{val}'"

    monkeypatch.setattr(act_mod, "_ensure_app_focused", lambda _: None)
    res = act_mod._handle_type_text(
        {"action": "type_text", "selector": "", "text": "{{password}}"},
        {"current_url": "https://portal.example.edu/login", "goal": "login to the portal", "plan": []},
        {},
        {"type": _FakeTypeTool()},
    )
    assert typed_values == ["FAKE-KEYCHAIN-SECRET-FOR-TESTING"]
    assert "FAKE-KEYCHAIN-SECRET-FOR-TESTING" not in res
    assert "••••••••" in res
