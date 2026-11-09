from __future__ import annotations

from src.graph.nodes import plan as plan_mod
from src.graph.nodes import skill as skill_mod
from src.skills import mail_skill


def test_extract_mail_intent_from_goal():
    goal = "Can you fetch me the five latest emails and summarize them for me?"
    matched = mail_skill.extract_mail_intent_from_goal(goal)
    assert matched == ("read_recent_emails", {"count": "5", "summarize": "true"})


def test_read_recent_emails_avoids_set_inbox_to_inbox_and_summarizes(monkeypatch):
    scripts: list[str] = []

    def fake_applescript(script: str, *args: str, timeout: float = 15.0) -> str:
        scripts.append(script)
        return (
            "Project Update|||Alice <alice@example.com>|||The deployment finished ahead of schedule.~~~"
            "Invoice #42|||Billing <billing@example.com>|||Your April invoice is attached.~~~"
        )

    monkeypatch.setattr(mail_skill, "run_applescript", fake_applescript)
    monkeypatch.setattr(
        mail_skill,
        "call_llm",
        lambda *a, **k: "You have two recent emails: Alice shared that the deployment finished early, and Billing sent your April invoice.",
    )

    goal = "Can you fetch me the five latest emails and summarize them for me?"
    planned = plan_mod.plan_node({"goal": goal, "plan": [], "installed_apps": ["Mail"]})
    assert planned["plan_reasoning"] == "Direct email action via Mail skill"

    out = skill_mod.skill_node({"goal": goal, **planned, "history": []})
    assert out.get("done") is True
    assert "Alice shared that the deployment finished early" in out.get("result", "")
    assert len(scripts) == 1
    assert "set inbox to inbox" not in scripts[0]
