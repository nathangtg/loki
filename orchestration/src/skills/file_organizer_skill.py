from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.graph.llm import call_llm, extract_json
from src.graph.prompts import FILE_CLASSIFY_SYSTEM
from src.skills.base import Skill, SkillIntent
from src.skills.registry import register_skill
from src.skills.shell_utils import list_directory, read_file_snippet, move_file, human_size

LOGGER = logging.getLogger(__name__)

_DEFAULT_FOLDER = str(Path.home() / "Downloads")

_CATEGORY_DIRS: dict[str, str] = {
    "Documents":  "Documents",
    "Images":     "Images",
    "Videos":     "Videos",
    "Code":       "Code",
    "Archives":   "Archives",
    "Installers": "Installers",
    "Other":      "Other",
}

_VALID_CATEGORIES = set(_CATEGORY_DIRS.keys())


def _classify_files(file_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    listing_lines: list[str] = []
    for e in file_entries:
        snippet = read_file_snippet(e["path"], max_bytes=256)
        snippet_part = f' | snippet: "{snippet[:120]}"' if snippet else ""
        listing_lines.append(
            f'- {e["name"]} ({e["mime_type"]}, {human_size(e["size_bytes"])}){snippet_part}'
        )
    listing_text = "\n".join(listing_lines)

    prompt = f"FILES TO CLASSIFY:\n{listing_text}"

    try:
        raw = call_llm(FILE_CLASSIFY_SYSTEM, prompt, num_predict=1024, temperature=0.0)
    except Exception as exc:
        LOGGER.warning("file_organizer: LLM classification call failed: %s", exc)
        return [
            {"file": e["name"], "folder": "Other", "reason": "Classification unavailable"}
            for e in file_entries
        ]

    raw_stripped = raw.strip()
    plan: list[dict[str, Any]] = []
    try:
        if raw_stripped.startswith("["):
            plan = json.loads(raw_stripped)
        else:
            if raw_stripped.startswith("```"):
                lines = raw_stripped.splitlines()
                raw_stripped = "\n".join(
                    lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
                ).strip()
            if raw_stripped.startswith("["):
                plan = json.loads(raw_stripped)
            else:
                obj = extract_json(raw_stripped)
                plan = obj.get("files", obj.get("plan", []))
    except Exception as exc:
        LOGGER.warning("file_organizer: failed to parse LLM classification response: %s", exc)
        plan = []

    result: list[dict[str, Any]] = []
    for e in file_entries:
        matched = next(
            (item for item in plan
             if isinstance(item, dict) and item.get("file") == e["name"]),
            None,
        )
        if matched and matched.get("folder") in _VALID_CATEGORIES:
            result.append({
                "file": e["name"],
                "folder": matched["folder"],
                "reason": (matched.get("reason") or "").strip()[:200],
            })
        else:
            result.append({
                "file": e["name"],
                "folder": "Other",
                "reason": "Unclassified or unrecognised category",
            })

    return result


def _scan_downloads(params: dict[str, Any]) -> str:
    folder = params.get("folder", _DEFAULT_FOLDER)
    try:
        entries = list_directory(folder)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise RuntimeError(str(exc)) from exc

    if not entries:
        return f"No files found in '{folder}'."

    total_size = sum(e["size_bytes"] for e in entries)
    lines = [f"{e['name']} ({e['mime_type']}, {human_size(e['size_bytes'])})" for e in entries]
    summary = "\n".join(f"- {item}" for item in lines)
    return (
        f"Found {len(entries)} file(s) in '{folder}' "
        f"({human_size(total_size)} total):\n{summary}"
    )


def _propose_organization(params: dict[str, Any]) -> str:
    folder = params.get("folder", _DEFAULT_FOLDER)
    try:
        entries = list_directory(folder)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise RuntimeError(str(exc)) from exc

    if not entries:
        return f"No files found in '{folder}' — nothing to organise."

    plan = _classify_files(entries)

    lines: list[str] = []
    for item in plan:
        reason = item["reason"]
        lines.append(f"  {item['file']} → {item['folder']}/ — {reason}")

    plan_json = json.dumps(plan)
    header = (
        f"Proposed organisation for '{folder}' ({len(plan)} file(s)):\n"
        + "\n".join(lines)
        + f"\n\nTo execute, reply with:\n"
        f'  skill: file_organizer | intent: execute_organization\n'
        f'  params: {{"folder": "{folder}", "plan_json": <plan below>, "confirmed": "yes"}}\n\n'
        f"Plan JSON:\n{plan_json}"
    )
    return header


def _execute_organization(params: dict[str, Any]) -> str:
    folder = params.get("folder", _DEFAULT_FOLDER)
    plan_json = params.get("plan_json", "")
    confirmed = (params.get("confirmed") or "").strip().lower()

    try:
        plan: list[dict[str, Any]] = json.loads(plan_json) if plan_json else []
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid plan_json: {exc}") from exc

    if not plan:
        raise ValueError("plan_json is empty or missing — run propose_organization first.")

    if confirmed != "yes":
        lines = [
            f"  {item['file']} → {item.get('folder', 'Other')}/"
            for item in plan
        ]
        preview = "\n".join(lines)
        return (
            f"Dry run — {len(plan)} file(s) would be moved:\n{preview}\n\n"
            'Set confirmed="yes" to execute.'
        )

    folder_path = Path(folder).expanduser().resolve()
    moved: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for item in plan:
        filename = item.get("file", "")
        category = item.get("folder", "Other")
        if category not in _VALID_CATEGORIES:
            category = "Other"

        src = str(folder_path / filename)
        dst_dir = str(folder_path / _CATEGORY_DIRS[category])

        if not Path(src).is_file():
            skipped.append(filename)
            continue

        try:
            new_path = move_file(src, dst_dir)
            moved.append(f"{filename} → {category}/")
            LOGGER.info("file_organizer: moved '%s' to '%s'", filename, new_path)
        except Exception as exc:
            errors.append(f"{filename}: {exc}")
            LOGGER.warning("file_organizer: failed to move '%s': %s", filename, exc)

    parts: list[str] = []
    if moved:
        parts.append(f"Moved {len(moved)} file(s):\n" + "\n".join(f"  ✓ {m}" for m in moved))
    if skipped:
        parts.append(f"Skipped {len(skipped)} (already gone): " + ", ".join(skipped))
    if errors:
        parts.append(f"Errors ({len(errors)}):\n" + "\n".join(f"  ✗ {e}" for e in errors))

    return "\n\n".join(parts) if parts else "Nothing to move."


_file_organizer_skill = Skill(
    name="file_organizer",
    description=(
        "Scan a folder, classify files with LLM assistance, and move them "
        "into category subfolders — requires explicit confirmation before moving"
    ),
    app_names={"finder", "downloads", "files", "organizer", "file organizer"},
)

_file_organizer_skill.add_intent(
    SkillIntent(
        name="scan_downloads",
        description="List all files in ~/Downloads (or a given folder) with sizes and types",
        params=[],
        handler=_scan_downloads,
    )
)
_file_organizer_skill.add_intent(
    SkillIntent(
        name="propose_organization",
        description=(
            "Classify files in ~/Downloads (or a given folder) into categories "
            "and propose a move plan — does NOT move anything"
        ),
        params=[],
        handler=_propose_organization,
    )
)
_file_organizer_skill.add_intent(
    SkillIntent(
        name="execute_organization",
        description=(
            "Execute a previously proposed file organisation plan. "
            "Requires plan_json (from propose_organization). "
            "Set confirmed='yes' to actually move files; omit or set to 'no' for a dry-run preview."
        ),
        params=["plan_json"],
        handler=_execute_organization,
    )
)

register_skill(_file_organizer_skill)
