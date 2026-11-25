from __future__ import annotations

import copy
import importlib
import sys
import types

import pytest

import src.graph.config as config_mod
import src.graph.llm as llm_mod
import src.graph.nodes.reason as reason_mod
from src.graph.history import is_summary_entry, summarize_if_needed


def _entries(n: int) -> list[dict]:
    return [{"action": "click", "target": f"btn{i}", "result": f"ok{i}"} for i in range(n)]


def test_no_summarization_below_threshold():
    history = _entries(4)
    assert summarize_if_needed(history, keep_recent=4, max_chars=600) is history


def test_older_entries_merge_into_single_summary_first():
    out = summarize_if_needed(_entries(10), keep_recent=4, max_chars=600)
    assert len(out) == 5
    assert is_summary_entry(out[0])
    assert sum(1 for e in out if is_summary_entry(e)) == 1
    assert "btn0" in out[0]["result"] and "btn5" in out[0]["result"]


def test_recent_entries_kept_verbatim_in_order_and_input_not_mutated():
    history = _entries(10)
    snapshot = copy.deepcopy(history)
    out = summarize_if_needed(history, keep_recent=4, max_chars=600)
    assert out[1:] == history[-4:]
    assert history == snapshot


def test_summary_capped_dropping_oldest_lines_first():
    out = summarize_if_needed(_entries(40), keep_recent=4, max_chars=100)
    text = out[0]["result"]
    assert len(text) <= 100
    assert "btn35" in text
    assert "btn0 " not in text


def test_summarizer_makes_no_llm_call(monkeypatch: pytest.MonkeyPatch):
    def boom(*a, **k):
        raise AssertionError("LLM must not be called")

    monkeypatch.setattr(llm_mod, "call_llm", boom)
    monkeypatch.setattr(reason_mod, "call_llm", boom)
    assert summarize_if_needed(_entries(12), keep_recent=4, max_chars=200)


def test_summarization_is_idempotent_and_does_not_stack():
    once = summarize_if_needed(_entries(10), keep_recent=4, max_chars=600)
    assert summarize_if_needed(once, keep_recent=4, max_chars=600) is once
    grown = once + _entries(3)
    twice = summarize_if_needed(grown, keep_recent=4, max_chars=600)
    assert sum(1 for e in twice if is_summary_entry(e)) == 1
    assert len(twice) == 5
    assert "btn0" in twice[0]["result"]


def test_kv_type_map_and_invalid_fallback():
    assert llm_mod.resolve_kv_type("f16") == 1
    assert llm_mod.resolve_kv_type("Q8_0") == 8
    assert llm_mod.resolve_kv_type("q4_0") == 2
    assert llm_mod.resolve_kv_type(1) == 1
    assert llm_mod.resolve_kv_type("8") == 8
    for bad in ("bogus", "", 999, None, True):
        assert llm_mod.resolve_kv_type(bad) == 8


def test_n_ctx_default_and_llama_receives_types(monkeypatch: pytest.MonkeyPatch):
    for var in ("LOKI_N_CTX", "LOKI_KV_TYPE", "LOKI_KV_TYPE_K", "LOKI_KV_TYPE_V"):
        monkeypatch.delenv(var, raising=False)
    fresh = importlib.reload(config_mod)
    try:
        assert fresh.N_CTX == 8192
        assert fresh.KV_TYPE_K == "q8_0" and fresh.KV_TYPE_V == "q8_0"
    finally:
        importlib.reload(config_mod)

    captured: dict = {}

    class FakeLlama:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake = types.ModuleType("llama_cpp")
    fake.Llama = FakeLlama
    monkeypatch.setitem(sys.modules, "llama_cpp", fake)
    monkeypatch.setattr(llm_mod, "_llm_instance", None)
    monkeypatch.setattr(llm_mod, "MODEL_PATH", "/tmp/model.gguf")
    monkeypatch.setattr(llm_mod, "N_CTX", 8192)
    monkeypatch.setattr(llm_mod, "KV_TYPE_K", "f16")
    monkeypatch.setattr(llm_mod, "KV_TYPE_V", "q4_0")
    monkeypatch.setattr(llm_mod, "MMPROJ_PATH", "")
    monkeypatch.setattr(llm_mod, "SPECULATIVE_MODE", "none")
    try:
        llm_mod._get_llm()
    finally:
        llm_mod._llm_instance = None
    assert captured["n_ctx"] == 8192
    assert captured["type_k"] == 1
    assert captured["type_v"] == 2


def test_reason_node_writes_summarized_history_back(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(reason_mod, "call_llm", lambda *a, **k: '{"action":"read_page"}')
    monkeypatch.setattr(reason_mod, "grammar_for_step", lambda *a, **k: None)
    history = _entries(10)
    state = {
        "goal": "g",
        "plan": ["a", "b"],
        "plan_step": 1,
        "history": history,
        "current_url": "",
        "screen_elements": "",
        "page_text": "",
    }
    out = reason_mod.reason_node(state)
    assert is_summary_entry(out["history"][0])
    assert out["history"][1:] == history[-config_mod.HISTORY_KEEP_RECENT :]
    assert "plan" not in out and "plan_step" not in out

    short = dict(state, history=_entries(2))
    assert "history" not in reason_mod.reason_node(short)
