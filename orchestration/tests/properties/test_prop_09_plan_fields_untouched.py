from __future__ import annotations

import copy

from hypothesis import given, settings
from hypothesis import strategies as st

import src.graph.nodes.reason as reason_mod

_entry = st.fixed_dictionaries(
    {"action": st.just("click"), "target": st.text(max_size=30), "result": st.text(max_size=60)}
)


@settings(max_examples=50, deadline=None)
@given(
    history=st.lists(_entry, min_size=0, max_size=30),
    plan=st.lists(st.text(min_size=1, max_size=40), min_size=1, max_size=6),
    plan_reasoning=st.text(max_size=60),
    data=st.data(),
)
def test_reason_node_leaves_plan_fields_untouched(history, plan, plan_reasoning, data):
    plan_step = data.draw(st.integers(min_value=0, max_value=len(plan) - 1))
    state = {
        "goal": "g",
        "plan": plan,
        "plan_step": plan_step,
        "plan_reasoning": plan_reasoning,
        "history": history,
        "current_url": "",
        "screen_elements": "",
        "page_text": "",
    }
    before = copy.deepcopy(state)

    orig_llm, orig_grammar = reason_mod.call_llm, reason_mod.grammar_for_step
    reason_mod.call_llm = lambda *a, **k: '{"action":"read_page"}'
    reason_mod.grammar_for_step = lambda *a, **k: None
    try:
        out = reason_mod.reason_node(state)
    finally:
        reason_mod.call_llm, reason_mod.grammar_for_step = orig_llm, orig_grammar

    assert state == before
    assert not {"plan", "plan_step", "plan_reasoning"} & set(out)
