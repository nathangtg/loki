from __future__ import annotations

from datetime import date, timedelta

from src.graph.nodes import skill as skill_mod
from src.skills import calendar_skill


def test_parse_datetime_natural_expressions():
    tomorrow = date.today() + timedelta(days=1)
    dt_start = calendar_skill._parse_datetime("tomorrow 8am")
    dt_end = calendar_skill._parse_datetime("10am", default_date=dt_start.date())

    assert dt_start.date() == tomorrow
    assert (dt_start.hour, dt_start.minute) == (8, 0)
    assert dt_end.date() == tomorrow
    assert (dt_end.hour, dt_end.minute) == (10, 0)


def test_create_event_handles_combined_time_range_and_locale_safe_applescript(monkeypatch):
    scripts: list[str] = []
    monkeypatch.setattr(calendar_skill, "_try_eventkit_create_event", lambda *a, **k: False)
    monkeypatch.setattr(calendar_skill, "run_applescript", lambda s, *a, **k: scripts.append(s) or "")

    msg = calendar_skill._create_event(
        {
            "title": "Team Sync",
            "start_datetime": "tomorrow 8am to 10am",
        }
    )

    assert "Team Sync" in msg
    assert "8:00 AM to 10:00 AM" in msg
    assert len(scripts) == 1
    assert "set hours of evStart to 8" in scripts[0]
    assert "set hours of evEnd to 10" in scripts[0]
    assert 'date "' not in scripts[0]


def test_skill_node_fast_paths_calendar_event_creation(monkeypatch):
    scripts: list[str] = []
    monkeypatch.setattr(calendar_skill, "_try_eventkit_create_event", lambda *a, **k: False)
    monkeypatch.setattr(calendar_skill, "run_applescript", lambda s, *a, **k: scripts.append(s) or "")

    out = skill_mod.skill_node(
        {
            "goal": "Can you create an event on my calendar tomorrow from 8am to 10am?",
            "plan": ["Open Calendar", "Create event for tomorrow 8am to 10am"],
            "history": [],
        }
    )

    assert out.get("done") is True
    assert "8:00 AM to 10:00 AM" in out.get("result", "")
    assert len(scripts) == 1


def test_extract_calendar_event_with_month_day_and_title(monkeypatch):
    scripts: list[str] = []
    monkeypatch.setattr(calendar_skill, "_try_eventkit_create_event", lambda *a, **k: False)
    monkeypatch.setattr(calendar_skill, "run_applescript", lambda s, *a, **k: scripts.append(s) or "")

    goal = "Create an event in Calendar for October 28th from 8am to 9am called Sleep"
    params = calendar_skill.extract_calendar_event_from_goal(goal)
    assert params is not None
    assert params["title"] == "Sleep"
    assert params["start_datetime"].endswith("-10-28 08:00")
    assert params["end_datetime"].endswith("-10-28 09:00")

    out = skill_mod.skill_node({"goal": goal, "plan": [goal], "history": []})
    assert out.get("done") is True
    assert "Sleep" in out.get("result", "")
    assert "Oct 28 from 8:00 AM to 9:00 AM" in out.get("result", "")

