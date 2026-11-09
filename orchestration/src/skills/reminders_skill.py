from __future__ import annotations

from datetime import datetime

from src.skills.applescript_utils import run_applescript, escape_as_string
from src.skills.base import Skill, SkillIntent
from src.skills.calendar_skill import _as_date_block, _parse_datetime
from src.skills.registry import register_skill


def _parse_due_datetime(raw: str) -> datetime:
    dt = _parse_datetime(raw)
    if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and "midnight" not in (raw or "").lower():
        dt = dt.replace(hour=9, minute=0)
    return dt


def _add_reminder(params: dict) -> str:
    title = params["title"]
    safe_title = escape_as_string(title)
    script = f'''
tell application "Reminders"
    make new reminder with properties {{name:"{safe_title}"}}
end tell
'''
    run_applescript(script)
    return f"Reminder '{title}' added."


def _add_reminder_with_due(params: dict) -> str:
    title = params["title"]
    due_datetime = params["due_datetime"]

    dt = _parse_due_datetime(due_datetime)
    due_block = _as_date_block("dueDate", dt)
    safe_title = escape_as_string(title)

    script = f'''
tell application "Reminders"
{due_block}
    make new reminder with properties {{name:"{safe_title}", due date:dueDate}}
end tell
'''
    run_applescript(script)
    return f"Reminder '{title}' due {due_datetime} added."


def _list_due_today(params: dict) -> str:
    script = '''
tell application "Reminders"
    set todayStart to current date
    set hours of todayStart to 0
    set minutes of todayStart to 0
    set seconds of todayStart to 0
    set todayEnd to todayStart + 86400
    set dueToday to (every reminder whose completed is false and due date >= todayStart and due date < todayEnd)
    set resultList to {}
    repeat with r in dueToday
        set end of resultList to name of r
    end repeat
    return resultList
end tell
'''
    raw = run_applescript(script)
    if not raw:
        return "No reminders due today."
    items = [item.strip() for item in raw.split(",") if item.strip()]
    if not items:
        return "No reminders due today."
    lines = "\n".join(f"- {item}" for item in items)
    return f"Due today:\n{lines}"


def _list_incomplete(params: dict) -> str:
    script = '''
tell application "Reminders"
    set allIncomplete to (every reminder whose completed is false)
    set resultList to {}
    set itemCount to 0
    repeat with r in allIncomplete
        if itemCount >= 20 then exit repeat
        set end of resultList to name of r
        set itemCount to itemCount + 1
    end repeat
    return resultList
end tell
'''
    raw = run_applescript(script)
    if not raw:
        return "Incomplete reminders (0):"
    items = [item.strip() for item in raw.split(",") if item.strip()]
    n = len(items)
    lines = "\n".join(f"- {item}" for item in items)
    return f"Incomplete reminders ({n}):\n{lines}"


def _complete_reminder(params: dict) -> str:
    title = params["title"]
    safe_title = escape_as_string(title)
    script = f'''
tell application "Reminders"
    set matchedReminders to (every reminder whose name is "{safe_title}" and completed is false)
    if (count of matchedReminders) is 0 then
        return "NOT_FOUND"
    end if
    set completed of item 1 of matchedReminders to true
    return "OK"
end tell
'''
    result = run_applescript(script)
    if result == "NOT_FOUND":
        raise RuntimeError(f"Reminder '{title}' not found or already complete.")
    return f"Reminder '{title}' marked complete."


_reminders_skill = Skill(
    name="reminders",
    description="Create, list, and complete reminders via Apple Reminders' AppleScript dictionary",
    app_names={"reminders", "apple reminders", "reminders.app"},
)

_reminders_skill.add_intent(
    SkillIntent(
        name="add_reminder",
        description="Add a new reminder with just a title",
        params=["title"],
        handler=_add_reminder,
    )
)
_reminders_skill.add_intent(
    SkillIntent(
        name="add_reminder_with_due",
        description="Add a new reminder with a title and a due date/time",
        params=["title", "due_datetime"],
        handler=_add_reminder_with_due,
    )
)
_reminders_skill.add_intent(
    SkillIntent(
        name="list_due_today",
        description="List all incomplete reminders due today",
        params=[],
        handler=_list_due_today,
    )
)
_reminders_skill.add_intent(
    SkillIntent(
        name="list_incomplete",
        description="List up to 20 incomplete reminders across all lists",
        params=[],
        handler=_list_incomplete,
    )
)
_reminders_skill.add_intent(
    SkillIntent(
        name="complete_reminder",
        description="Mark a reminder as complete by its exact title",
        params=["title"],
        handler=_complete_reminder,
    )
)

register_skill(_reminders_skill)
