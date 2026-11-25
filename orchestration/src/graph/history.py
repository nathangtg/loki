from __future__ import annotations

from typing import Any

from src.graph.config import HISTORY_KEEP_RECENT, HISTORY_SUMMARY_MAX_CHARS

SUMMARY_ACTION = "summary"

_LINE_TARGET_MAX = 40
_LINE_RESULT_MAX = 80


def is_summary_entry(entry: dict) -> bool:
    return entry.get("action") == SUMMARY_ACTION


def _entry_line(entry: dict) -> str:
    target = str(entry.get("target", ""))[:_LINE_TARGET_MAX]
    result = str(entry.get("result", ""))[:_LINE_RESULT_MAX].replace("\n", " ")
    return f"{entry.get('action', '')} {target} -> {result}".strip()


def _cap_lines(lines: list[str], max_chars: int) -> str:
    kept = list(lines)
    while kept and len("\n".join(kept)) > max_chars:
        kept.pop(0)
    if kept:
        return "\n".join(kept)
    # Even the newest line is too long: keep its tail.
    return lines[-1][-max_chars:] if lines and max_chars > 0 else ""


def summarize_if_needed(
    history: list[dict],
    keep_recent: int = HISTORY_KEEP_RECENT,
    max_chars: int = HISTORY_SUMMARY_MAX_CHARS,
) -> list[dict]:
    keep_recent = max(keep_recent, 0)
    entries = [e for e in history if not is_summary_entry(e)]

    older_count = len(entries) - keep_recent
    if older_count <= 0:
        return history

    older = entries[:older_count]
    recent = entries[older_count:]

    lines: list[str] = []
    for e in history:
        if is_summary_entry(e):
            lines.extend(ln for ln in str(e.get("result", "")).split("\n") if ln)
    lines.extend(_entry_line(e) for e in older)

    summary = {"action": SUMMARY_ACTION, "target": "", "result": _cap_lines(lines, max_chars)}
    return [summary, *recent]
