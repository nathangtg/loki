from __future__ import annotations

from src.skills.applescript_utils import run_applescript, escape_as_string
from src.skills.base import Skill, SkillIntent
from src.skills.registry import register_skill


def _create_note(params: dict) -> str:
    title = escape_as_string(params["title"])
    body = escape_as_string(params["body"])

    script = f'''
    tell application "Notes"
        try
            tell account "iCloud"
                make new note with properties {{name:"{title}", body:"{body}"}}
            end tell
        on error
            make new note with properties {{name:"{title}", body:"{body}"}}
        end try
    end tell
    '''
    run_applescript(script)
    return f"Note '{params['title']}' created."


def _append_to_note(params: dict) -> str:
    title = escape_as_string(params["title"])
    content = escape_as_string(params["content"])

    script = f'''
    tell application "Notes"
        set theNote to first note whose name is "{title}"
        if theNote is missing value then
            error "Note not found"
        end if
        set body of theNote to (body of theNote) & "\n{content}"
    end tell
    '''
    try:
        run_applescript(script)
    except RuntimeError as exc:
        if "not found" in str(exc).lower() or "can't get" in str(exc).lower():
            raise RuntimeError(f"Note '{params['title']}' not found") from exc
        raise
    return f"Note '{params['title']}' updated."


def _search_notes(params: dict) -> str:
    query = escape_as_string(params["query"])

    script = f'''
    tell application "Notes"
        set results to {{}}
        repeat with theAccount in accounts
            repeat with theNote in notes of theAccount
                set noteName to name of theNote
                set noteBody to body of theNote
                if noteName contains "{query}" or noteBody contains "{query}" then
                    set end of results to (noteName & "|||" & noteBody)
                end if
            end repeat
        end repeat
        set output to ""
        repeat with item in results
            set output to output & item & "~~~"
        end repeat
        return output
    end tell
    '''
    raw = run_applescript(script).strip()

    if not raw:
        return f"No notes matching '{params['query']}'."

    entries = [e for e in raw.split("~~~") if e.strip()]
    lines: list[str] = []
    for entry in entries[:5]:
        parts = entry.split("|||", 1)
        note_title = parts[0].strip()
        note_body = parts[1].strip() if len(parts) > 1 else ""
        snippet = note_body[:80]
        lines.append(f"- {note_title}: {snippet}")

    return "\n".join(lines) if lines else f"No notes matching '{params['query']}'."


def _list_notes(params: dict) -> str:
    script = '''
    tell application "Notes"
        try
            set noteNames to name of every note in account "iCloud"
        on error
            set noteNames to name of every note
        end try
        set output to ""
        set noteCount to count of noteNames
        if noteCount > 10 then
            set noteCount to 10
        end if
        repeat with i from 1 to noteCount
            set output to output & (item i of noteNames) & "~~~"
        end repeat
        return output
    end tell
    '''
    raw = run_applescript(script).strip()

    if not raw:
        return "Recent notes:\n(none)"

    names = [n.strip() for n in raw.split("~~~") if n.strip()]
    recent = names[:10]
    bullet_lines = "\n".join(f"- {n}" for n in recent)
    return f"Recent notes:\n{bullet_lines}"


_notes_skill = Skill(
    name="notes",
    description="Create, update, search, and list notes via Apple Notes' AppleScript dictionary",
    app_names={"notes", "apple notes", "notes.app"},
)
_notes_skill.add_intent(
    SkillIntent(
        name="create_note",
        description="Create a new note with a title and body",
        params=["title", "body"],
        handler=_create_note,
    )
)
_notes_skill.add_intent(
    SkillIntent(
        name="append_to_note",
        description="Append content to the body of an existing note",
        params=["title", "content"],
        handler=_append_to_note,
    )
)
_notes_skill.add_intent(
    SkillIntent(
        name="search_notes",
        description="Search notes by a query string, matched against title and body",
        params=["query"],
        handler=_search_notes,
    )
)
_notes_skill.add_intent(
    SkillIntent(
        name="list_notes",
        description="List the 10 most recent notes from the iCloud account",
        params=[],
        handler=_list_notes,
    )
)

register_skill(_notes_skill)
