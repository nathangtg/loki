from __future__ import annotations

import pytest

import src.graph.nodes.finish as finish_mod
import src.graph.nodes.reason as reason_mod
import src.graph.site_knowledge as sk_mod


def _state(**extra) -> dict:
    base = {"goal": "open the calculator", "plan": ["Open Calculator"], "plan_step": 0, "history": [],
            "installed_apps": ["Calculator"], "current_url": "", "screen_elements": "", "page_text": ""}
    base.update(extra)
    return base


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"action":"done","summary":"Calculator is open."}', "Calculator is open."),
        ('{"action":"fail","reason":"no such app"}', "no such app"),
    ],
)
def test_reason_done_and_fail_carry_message_to_result(monkeypatch: pytest.MonkeyPatch, raw: str, expected: str):
    monkeypatch.setattr(reason_mod, "call_llm", lambda *a, **k: raw)
    monkeypatch.setattr(reason_mod, "grammar_for_step", lambda *a, **k: None)

    out = reason_mod.reason_node(_state())

    assert out["done"] is True
    assert out["result"] == expected


def test_reason_assemble_prompt_handles_non_empty_history_without_name_error(monkeypatch: pytest.MonkeyPatch):
    captured_user = []

    def fake_llm(system, user, **kwargs):
        captured_user.append(user)
        return '{"action":"key_combo","combo":"return"}'

    monkeypatch.setattr(reason_mod, "call_llm", fake_llm)
    monkeypatch.setattr(reason_mod, "grammar_for_step", lambda *a, **k: None)

    out = reason_mod.reason_node(
        _state(
            history=[{"action": "type_text", "target": "[0]", "result": "Typed Spongebob"}],
            _opened_app_name="Safari",
            page_title="Spongebob - Google Search",
        )
    )

    assert out["done"] is False
    assert out["next_action"] == {"action": "key_combo", "combo": "return"}
    assert "IMPORTANT: You JUST typed text in the previous turn." in captured_user[0]


def test_finish_skips_llm_site_learning_for_native_sessions(monkeypatch: pytest.MonkeyPatch):
    class _Boom:
        def __init__(self, *a, **k):
            raise AssertionError("SiteKnowledge must not run for native-only sessions")

    monkeypatch.setattr(sk_mod, "SiteKnowledge", _Boom)

    out = finish_mod.finish_node(
        {"history": [{"action": "open_app", "target": "Calculator", "result": "ok"}], "result": "Done."}
    )

    assert out == {"result": "Done."}


def test_finish_learns_from_web_sessions(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    class _SK:
        def learn_from_session(self, goal, history, current_url):
            calls.append(current_url)

    monkeypatch.setattr(sk_mod, "SiteKnowledge", _SK)

    finish_mod.finish_node(
        {"goal": "g", "current_url": "https://a.test", "history": [{"action": "navigate", "url": "https://a.test"}]}
    )

    assert calls == ["https://a.test"]


def test_finish_synthesizes_selected_fact_from_screen_when_summary_is_generic(monkeypatch: pytest.MonkeyPatch):
    import src.graph.nodes.reflect as reflect_mod

    monkeypatch.setattr(
        reflect_mod,
        "call_llm",
        lambda *a, **k: "Here's a fun fact about SpongeBob: his original name was SpongeBoy before it was changed due to a trademark!",
    )

    out = finish_mod.finish_node(
        {
            "goal": "Can you search up Spongebob and tell me a fun fact about it?",
            "result": "Task completed",
            "page_text": "SpongeBob SquarePants was originally named SpongeBoy by creator Stephen Hillenburg.",
            "screen_elements": '[0] <a href="/wiki/SpongeBob">SpongeBob Trivia</a>',
            "history": [],
        }
    )

    assert "SpongeBoy" in out["result"]


def test_act_navigate_uses_safari_without_launching_edge_when_cdp_down(monkeypatch: pytest.MonkeyPatch):
    import src.graph.nodes.act as act_mod
    from src.skills import web_search_skill

    opened_urls: list[str] = []
    monkeypatch.setattr(web_search_skill, "open_in_safari", lambda u: opened_urls.append(u))
    monkeypatch.setattr(act_mod.time, "sleep", lambda _: None)

    updates: dict = {}
    res, blocked = act_mod._handle_navigate(
        {"action": "navigate", "url": "https://www.google.com/search?q=bass+guitar"},
        {"goal": "search up bass guitar and tell me a fun fact"},
        updates,
        {},
    )

    assert blocked is False
    assert opened_urls == ["https://www.google.com/search?q=bass+guitar"]
    assert updates["_opened_app_name"] == "Safari"


def test_act_click_falls_back_to_cached_coords_without_keyerror_click(monkeypatch: pytest.MonkeyPatch):
    import sys
    from types import SimpleNamespace
    import src.graph.nodes.act as act_mod
    from src.tools.macui_tools import AXLocatorClickTool

    clicked_coords: list[tuple[int, int]] = []
    monkeypatch.setattr(act_mod, "_ensure_app_focused", lambda _: None)
    monkeypatch.setattr(act_mod.time, "sleep", lambda _: None)
    monkeypatch.setattr(AXLocatorClickTool, "_run", lambda self, inp: "Error: no element found")
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace(click=lambda x, y: clicked_coords.append((x, y))))

    res = act_mod._handle_click(
        {"action": "click", "target": "[35]"},
        {
            "current_url": "",
            "_opened_app_name": "Safari",
            "screen_elements": '[35] link "Bass guitar - Wikipedia"',
            "_element_coords_map": {"35": (412, 318, "Bass guitar - Wikipedia")},
        },
        {},
        {},
        {},
    )

    assert clicked_coords == [(412, 318)]
    assert "Clicked 'Bass guitar - Wikipedia' at (412,318)" in res
