from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

import src.graph.nodes.reason as reason_mod


@settings(max_examples=30, deadline=None)
@given(step=st.text(max_size=40), enabled=st.booleans())
def test_grammar_passed_through_only_when_enabled(step, enabled):
    sentinel = object()
    seen: dict = {}

    def fake_call_llm(*a, **k):
        seen.update(k)
        return '{"action":"read_page"}'

    saved = (reason_mod.call_llm, reason_mod.grammar_for_step, reason_mod.CONSTRAINED_DECODING)
    reason_mod.call_llm = fake_call_llm
    reason_mod.grammar_for_step = lambda *a, **k: sentinel
    reason_mod.CONSTRAINED_DECODING = enabled
    try:
        reason_mod.reason_node({"goal": "g", "plan": [step or "x"], "plan_step": 0, "current_url": ""})
    finally:
        reason_mod.call_llm, reason_mod.grammar_for_step, reason_mod.CONSTRAINED_DECODING = saved

    assert seen["grammar"] is (sentinel if enabled else None)
