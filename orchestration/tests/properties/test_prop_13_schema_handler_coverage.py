from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

import src.graph.grammars as g
from src.graph.nodes.act import ACT_NODE_HANDLERS


def test_action_types_nonempty_and_covered():
    assert g.ACTION_TYPES
    assert g.ACTION_TYPES <= ACT_NODE_HANDLERS


@settings(max_examples=50, deadline=None)
@given(
    grammar=st.sampled_from(
        [
            g.ACTION_GRAMMAR,
            g.RESEARCH_GRAMMAR,
            g.TYPE_OR_CLICK_GRAMMAR,
            g.CLICK_OR_SCROLL_GRAMMAR,
            g.OPEN_APP_ONLY_GRAMMAR,
            g.NAVIGATE_ONLY_GRAMMAR,
        ]
    )
)
def test_every_step_grammar_action_is_handled(grammar):
    parsed = g.parse_action_types(grammar)
    assert parsed <= g.ACTION_TYPES
    assert parsed <= ACT_NODE_HANDLERS
