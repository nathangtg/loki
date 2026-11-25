from __future__ import annotations

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from src.graph.llm import extract_json

_text = st.text(alphabet=st.characters(blacklist_categories=("Cs", "Cc")), max_size=40)

_FIELDS = {
    "navigate": ["url"],
    "click": ["target"],
    "type_text": ["selector", "text"],
    "key_combo": ["combo"],
    "read_page": [],
    "scroll": ["direction"],
    "open_app": ["name"],
    "web_search": ["query"],
    "read_url": ["url"],
    "write_content": ["destination", "description"],
    "done": ["summary"],
    "fail": ["reason"],
}


@st.composite
def _actions(draw):
    name = draw(st.sampled_from(sorted(_FIELDS)))
    obj = {"action": name}
    for field in _FIELDS[name]:
        obj[field] = draw(_text)
    return obj


@settings(max_examples=100, deadline=None)
@given(obj=_actions(), pad=st.text(alphabet=" \t\n", max_size=8))
def test_grammar_conformant_output_parses_unchanged(obj, pad):
    raw = pad + json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + pad
    assert extract_json(raw) == obj
