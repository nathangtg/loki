from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

import src.graph.grammars as g


@settings(max_examples=50, deadline=None)
@given(text=st.text(max_size=80))
def test_malformed_grammar_text_never_raises(text):
    g._compile.cache_clear()
    g.load_grammar(text)


@settings(max_examples=30, deadline=None)
@given(text=st.text(max_size=80))
def test_compile_failure_returns_none(text):
    saved = g._compile

    def boom(_t):
        raise RuntimeError("compile failed")

    g._compile = boom
    try:
        assert g.load_grammar(text) is None
    finally:
        g._compile = saved
