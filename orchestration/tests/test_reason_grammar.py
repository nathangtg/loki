from __future__ import annotations

import pytest

import src.graph.grammars as g

APPS = ["Notes", "Safari", "Calculator.app"]


@pytest.fixture(autouse=True)
def _identity_grammar(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(g, "load_grammar", lambda text: text)


@pytest.mark.parametrize(
    "step,expected",
    [
        ("Open Notes", g.OPEN_APP_ONLY_GRAMMAR),
        ("open the calculator app", g.OPEN_APP_ONLY_GRAMMAR),
        ("Open Fees Summary", g.ACTION_GRAMMAR),
        ("Open the login page", g.ACTION_GRAMMAR),
        ("Enter login credentials and submit", g.TYPE_OR_CLICK_GRAMMAR),
        ("Click the first picture", g.CLICK_OR_SCROLL_GRAMMAR),
        ("Search for cats", g.ACTION_GRAMMAR),
        ("", g.ACTION_GRAMMAR),
    ],
)
def test_grammar_for_step(step: str, expected: str):
    assert g.grammar_for_step(step, APPS) == expected


def test_open_step_without_known_apps_keeps_full_vocabulary():
    assert g.grammar_for_step("Open Notes", None) == g.ACTION_GRAMMAR


@pytest.mark.parametrize(
    "grammar",
    [
        g.OPEN_APP_ONLY_GRAMMAR,
        g.TYPE_OR_CLICK_GRAMMAR,
        g.CLICK_OR_SCROLL_GRAMMAR,
        g.RESEARCH_GRAMMAR,
    ],
)
def test_narrowed_grammars_can_always_finish(grammar: str):
    assert "done" in grammar and "fail" in grammar


@pytest.mark.parametrize(
    "step",
    [
        "Search the web for the weather in Kuala Lumpur",
        "Research the biggest company in the world",
        "Search Google for cheap flights",
        "Look up the definition of entropy",
        "Find out what the company does",
        "Find information about Sunway University",
    ],
)
def test_web_research_steps_narrow_to_research_grammar(step: str):
    assert g.grammar_for_step(step, APPS) == g.RESEARCH_GRAMMAR


@pytest.mark.parametrize(
    "step,expected",
    [
        ("Search for cats", g.ACTION_GRAMMAR),
        ("Open Google Chrome", g.ACTION_GRAMMAR),
        ("Open Fees Summary", g.ACTION_GRAMMAR),
        ("Click the search button", g.CLICK_OR_SCROLL_GRAMMAR),
    ],
)
def test_non_research_steps_keep_expected_grammar(step: str, expected: str):
    assert g.grammar_for_step(step, APPS) == expected


def test_full_vocabulary_includes_research_and_write_actions():
    for action in ("web_search", "read_url", "write_content"):
        assert action in g.ACTION_GRAMMAR
    assert "web_search" in g.TYPE_OR_CLICK_GRAMMAR
    assert "read_url" in g.CLICK_OR_SCROLL_GRAMMAR


def test_grammars_compile_with_llama_cpp():
    for grammar_text in (
        g.ACTION_GRAMMAR,
        g.RESEARCH_GRAMMAR,
        g.TYPE_OR_CLICK_GRAMMAR,
        g.CLICK_OR_SCROLL_GRAMMAR,
    ):
        assert g._compile(grammar_text) is not None


def test_extract_element_indices_and_constrained_grammar():
    elements = '[00] button "Save"\n[03] textfield "Username"\n[12] link "Help"'
    indices = g.extract_element_indices(elements)
    assert "[00]" in indices and "[0]" in indices
    assert "[03]" in indices and "[3]" in indices
    assert "[12]" in indices

    constrained = g.grammar_for_step("Click the Save button", APPS, valid_indices=indices)
    assert "idx-token" in constrained
    assert '"\\"[00]\\""' in constrained
    assert '"\\"[12]\\""' in constrained
    assert g._compile(constrained) is not None


def test_ax_locator_cache_avoids_rebuild():
    from src.tools.utils.ax_locator import (
        AXNode,
        _assign_locators,
        cache_locator_tree,
        clear_locator_cache,
        get_cached_ax_node,
    )

    clear_locator_cache()
    root = AXNode(
        role="AXApplication",
        title="Safari",
        description="",
        value="",
        identifier="",
        role_description="",
        position=(0, 0),
        size=(1200, 800),
        subrole="",
        enabled=True,
        focused=True,
    )
    btn = AXNode(
        role="AXButton",
        title="Search",
        description="",
        value="",
        identifier="search_btn",
        role_description="",
        position=(100, 100),
        size=(80, 30),
        subrole="",
        enabled=True,
        focused=False,
        parent=root,
    )
    root.children.append(btn)
    _assign_locators(root, parent_path="")
    cache_locator_tree(root, pid=1234)

    cached = get_cached_ax_node(btn.locator_path)
    assert cached is btn
    clear_locator_cache()
    assert get_cached_ax_node(btn.locator_path) is None
