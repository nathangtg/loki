from __future__ import annotations

import pytest

import src.graph.grammars as g
import src.graph.nodes.act as act_mod
import src.graph.nodes.reason as reason_mod
from src.graph import config


def _run_reason(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> dict:
    sentinel = object()
    seen: dict = {}

    def fake_call_llm(*args, **kwargs):
        seen.update(kwargs)
        return '{"action":"read_page"}'

    monkeypatch.setattr(reason_mod, "CONSTRAINED_DECODING", enabled)
    monkeypatch.setattr(reason_mod, "grammar_for_step", lambda *a, **k: sentinel)
    monkeypatch.setattr(reason_mod, "call_llm", fake_call_llm)
    reason_mod.reason_node({"goal": "g", "plan": ["open the page"], "plan_step": 0, "current_url": ""})
    seen["sentinel"] = sentinel
    return seen


def test_constrained_decoding_defaults_on():
    assert config.CONSTRAINED_DECODING is True


def test_grammar_passed_to_call_llm_when_enabled(monkeypatch: pytest.MonkeyPatch):
    seen = _run_reason(monkeypatch, True)
    assert seen["grammar"] is seen["sentinel"]


def test_no_grammar_when_disabled(monkeypatch: pytest.MonkeyPatch):
    seen = _run_reason(monkeypatch, False)
    assert seen["grammar"] is None


def test_compile_failure_falls_back_to_none(monkeypatch: pytest.MonkeyPatch):
    def boom(text):
        raise ValueError("bad grammar")

    monkeypatch.setattr(g, "_compile", boom)
    assert g.load_grammar("not a grammar") is None


def test_action_types_parsed_from_grammar():
    assert {"navigate", "click", "type_text", "write_content", "done", "fail"} <= g.ACTION_TYPES
    assert "type_content" not in g.ACTION_TYPES


def test_schema_actions_subset_of_act_handlers():
    assert g.ACTION_TYPES <= act_mod.ACT_NODE_HANDLERS
    assert "type_content" not in act_mod.ACT_NODE_HANDLERS
