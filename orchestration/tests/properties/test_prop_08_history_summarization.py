"""Property 8: History summarization preserves recency and bounds.

Validates: Requirements 3.3, 3.4, 3.5
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from src.graph.history import is_summary_entry, summarize_if_needed

_entry = st.fixed_dictionaries(
    {
        "action": st.sampled_from(["click", "type_text", "navigate", "open_app", "key_combo"]),
        "target": st.text(max_size=60),
        "result": st.text(max_size=120),
    }
)


@settings(max_examples=100, deadline=None)
@given(
    history=st.lists(_entry, max_size=40),
    keep_recent=st.integers(min_value=0, max_value=8),
    max_chars=st.integers(min_value=20, max_value=800),
)
def test_summarization_preserves_recency_and_bounds(history, keep_recent, max_chars):
    out = summarize_if_needed(history, keep_recent=keep_recent, max_chars=max_chars)

    if len(history) <= keep_recent:
        assert out == history
        return

    summaries = [e for e in out if is_summary_entry(e)]
    assert len(summaries) == 1 and is_summary_entry(out[0])
    assert len(summaries[0]["result"]) <= max_chars
    expected_recent = history[len(history) - keep_recent :] if keep_recent else []
    assert out[1:] == expected_recent

    again = summarize_if_needed(out, keep_recent=keep_recent, max_chars=max_chars)
    assert sum(1 for e in again if is_summary_entry(e)) == 1
    assert again[1:] == expected_recent
