from __future__ import annotations

from pathlib import Path

import pytest

import src.graph.nodes.act as act_mod
import src.tools.research_tools as research_mod
from src.agents.ContentAgent import ContentAgent


class _RecordingTool:
    def __init__(self, result: str = "ok"):
        self.result = result
        self.calls: list = []

    def _run(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


@pytest.fixture
def fake_tools(monkeypatch: pytest.MonkeyPatch):
    nt = {
        "type": _RecordingTool("typed ok"),
        "key_combo": _RecordingTool(),
        "wait": _RecordingTool(),
        "scroll": _RecordingTool(),
        "screen_map": _RecordingTool("screen text"),
        "find_apps": _RecordingTool("Notes"),
        "open_app": _RecordingTool(),
        "shortcuts": _RecordingTool(""),
    }
    monkeypatch.setattr(act_mod, "get_native_tools", lambda: nt)
    return nt


def _state(**extra) -> dict:
    base = {
        "goal": "test goal",
        "plan": ["Search the web for cats"],
        "plan_step": 0,
        "history": [],
        "installed_apps": [],
        "current_url": "",
        "page_text": "",
        "screen_elements": "",
        "findings": [],
        "on_step": None,
        "on_message": None,
    }
    base.update(extra)
    return base


def test_act_web_search_appends_finding_and_history(
    monkeypatch: pytest.MonkeyPatch, fake_tools
):
    class _FakeSearch:
        def _run(self, query):
            return f"results for {query}"

    monkeypatch.setattr(research_mod, "WebSearchTool", _FakeSearch)

    updates = act_mod.act_node(
        _state(next_action={"action": "web_search", "query": "cats"})
    )

    assert updates["findings"] == [
        {"type": "search", "query_or_url": "cats", "content": "results for cats"}
    ]
    assert updates["history"][-1]["action"] == "web_search"
    assert updates["history"][-1]["target"] == "cats"
    assert "results for cats" in updates["history"][-1]["result"]


def test_act_read_url_appends_finding_and_updates_page_state(
    monkeypatch: pytest.MonkeyPatch, fake_tools
):
    class _FakeFetch:
        def _run(self, url):
            return "Title: Cats\nURL: https://cats.test\n\nAll about cats."

    monkeypatch.setattr(research_mod, "FetchPageContentTool", _FakeFetch)
    monkeypatch.setattr(act_mod, "live_url", lambda: "https://cats.test")
    monkeypatch.setattr(act_mod, "live_title", lambda: "Cats")

    updates = act_mod.act_node(
        _state(next_action={"action": "read_url", "url": "https://cats.test"})
    )

    assert updates["findings"][0]["type"] == "read"
    assert updates["current_url"] == "https://cats.test"
    assert updates["page_title"] == "Cats"
    assert "All about cats." in updates["page_text"]


def test_act_read_url_error_keeps_previous_page_text(
    monkeypatch: pytest.MonkeyPatch, fake_tools
):
    class _FakeFetch:
        def _run(self, url):
            return "Error: could not read https://cats.test: boom"

    monkeypatch.setattr(research_mod, "FetchPageContentTool", _FakeFetch)
    monkeypatch.setattr(act_mod, "live_url", lambda: "")
    monkeypatch.setattr(act_mod, "live_title", lambda: "")

    updates = act_mod.act_node(
        _state(
            page_text="old text",
            next_action={"action": "read_url", "url": "https://cats.test"},
        )
    )

    assert "page_text" not in updates
    assert updates["findings"][0]["content"].startswith("Error:")


def test_act_human_labels_for_research_actions():
    assert (
        act_mod._human_label("web_search", {"query": "cats"})
        == "Searching the web for cats"
    )
    assert (
        act_mod._human_label("read_url", {"url": "https://en.wikipedia.org/wiki/Apple"})
        == "Reading en.wikipedia.org"
    )
    assert (
        act_mod._human_label(
            "write_content", {"destination": "notes", "description": "a summary"}
        )
        == "Writing a summary to notes"
    )


class _FakeSkill:
    def __init__(self, result: str = "created"):
        self.result = result
        self.calls: list = []

    def execute(self, intent: str, params: dict) -> str:
        self.calls.append((intent, params))
        return self.result


@pytest.fixture
def fake_generation(monkeypatch: pytest.MonkeyPatch):
    import importlib

    content_mod = importlib.import_module("src.agents.ContentAgent")

    class _FakeAgent:
        def generate(self, description):
            return f"generated for {description}"

        def generate_with_context(self, description, page_text):
            return f"generated for {description}"

        def generate_from_findings(self, description, findings, page_text=""):
            return f"generated for {description}"

    monkeypatch.setattr(content_mod, "ContentAgent", _FakeAgent)
    return _FakeAgent


def test_write_content_notes_routes_to_notes_skill(
    monkeypatch: pytest.MonkeyPatch, fake_tools, fake_generation
):
    import src.skills as skills_pkg

    skill = _FakeSkill("Note 'X' created.")
    monkeypatch.setattr(skills_pkg, "find_skill_by_name", lambda name: skill)

    updates = act_mod.act_node(
        _state(
            findings=[{"type": "read", "query_or_url": "https://a", "content": "A"}],
            next_action={
                "action": "write_content",
                "destination": "notes",
                "description": "a summary of cats",
            },
        )
    )

    intent, params = skill.calls[0]
    assert intent == "create_note"
    assert params["body"] == "generated for a summary of cats"
    assert params["title"]
    assert "Note 'X' created." in updates["history"][-1]["result"]


def test_write_content_email_routes_to_mail_draft(
    monkeypatch: pytest.MonkeyPatch, fake_tools, fake_generation
):
    import src.skills as skills_pkg

    skill = _FakeSkill("Draft opened.")
    monkeypatch.setattr(skills_pkg, "find_skill_by_name", lambda name: skill)

    updates = act_mod.act_node(
        _state(
            next_action={
                "action": "write_content",
                "destination": "email",
                "description": "a summary of cats",
            }
        )
    )

    intent, params = skill.calls[0]
    assert intent == "draft_email"
    assert params["subject"]
    assert params["body"] == "generated for a summary of cats"
    assert updates["history"][-1]["action"] == "write_content"


def test_write_content_missing_skill_returns_error(
    monkeypatch: pytest.MonkeyPatch, fake_tools, fake_generation
):
    import src.skills as skills_pkg

    monkeypatch.setattr(skills_pkg, "find_skill_by_name", lambda name: None)

    updates = act_mod.act_node(
        _state(
            next_action={
                "action": "write_content",
                "destination": "notes",
                "description": "a summary",
            }
        )
    )

    assert "Error" in updates["history"][-1]["result"]


def test_write_content_file_writes_to_documents(
    monkeypatch: pytest.MonkeyPatch, fake_tools, fake_generation, tmp_path: Path
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    updates = act_mod.act_node(
        _state(
            next_action={
                "action": "write_content",
                "destination": "file",
                "description": "a summary of cats",
            }
        )
    )

    written = list((tmp_path / "Documents").glob("*.md"))
    assert len(written) == 1
    assert written[0].read_text(encoding="utf-8") == "generated for a summary of cats"
    assert str(written[0]) in updates["history"][-1]["result"]


def test_write_content_file_never_overwrites_existing_file(
    monkeypatch: pytest.MonkeyPatch, fake_tools, fake_generation, tmp_path: Path
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    documents = tmp_path / "Documents"
    documents.mkdir(parents=True)
    (documents / "a_summary_of_cats.md").write_text("user's own file", encoding="utf-8")

    act_mod.act_node(
        _state(
            next_action={
                "action": "write_content",
                "destination": "file",
                "description": "a summary of cats",
            }
        )
    )

    assert (documents / "a_summary_of_cats.md").read_text(encoding="utf-8") == "user's own file"
    assert (documents / "a_summary_of_cats_1.md").exists()


def test_write_content_browser_destination_types_generated_text(
    fake_tools, fake_generation
):
    nt = fake_tools

    act_mod.act_node(
        _state(
            next_action={
                "action": "write_content",
                "destination": "browser",
                "description": "a summary",
            }
        )
    )

    args, _kwargs = nt["type"].calls[0]
    assert args[0] == "generated for a summary"


def test_reason_prompt_includes_research_findings(monkeypatch: pytest.MonkeyPatch):
    import src.graph.nodes.reason as reason_mod

    captured: dict = {}

    def fake_call_llm(system, user, **kwargs):
        captured["user"] = user
        return '{"action":"done","summary":"ok"}'

    monkeypatch.setattr(reason_mod, "call_llm", fake_call_llm)
    monkeypatch.setattr(reason_mod, "grammar_for_step", lambda *a, **k: None)

    reason_mod.reason_node(
        _state(
            findings=[
                {"type": "search", "query_or_url": "cats", "content": "[1] Cats everywhere"}
            ]
        )
    )

    assert "RESEARCH FINDINGS:" in captured["user"]
    assert "[1] Cats everywhere" in captured["user"]


def test_content_agent_format_findings_renders_sources():
    out = ContentAgent.format_findings(
        [{"type": "read", "query_or_url": "https://a.test", "content": "AAA"}],
        page_text="page",
    )
    assert "Source (read): https://a.test" in out
    assert "AAA" in out
    assert ContentAgent.format_findings([], page_text="page") == "page"
