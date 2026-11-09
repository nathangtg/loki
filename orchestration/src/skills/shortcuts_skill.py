from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from src.skills.base import Skill, SkillIntent
from src.skills.registry import register_skill

LOGGER = logging.getLogger(__name__)

_SHORTCUTS_BIN = "/usr/bin/shortcuts"


def list_installed_shortcuts(timeout: float = 5.0) -> list[str]:
    if not Path(_SHORTCUTS_BIN).exists():
        return []
    try:
        res = subprocess.run(
            [_SHORTCUTS_BIN, "list"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if res.returncode != 0:
            return []
        return [line.strip() for line in res.stdout.splitlines() if line.strip()]
    except Exception as exc:
        LOGGER.debug("shortcuts_skill: list failed: %s", exc)
        return []


def _handle_list_shortcuts(params: dict) -> str:
    shortcuts = list_installed_shortcuts()
    if not shortcuts:
        return "No macOS Shortcuts found on this system."
    preview = shortcuts[:25]
    lines = "\n".join(f"- {name}" for name in preview)
    more = f"\n... and {len(shortcuts) - len(preview)} more" if len(shortcuts) > len(preview) else ""
    return f"Available macOS Shortcuts ({len(shortcuts)} total):\n{lines}{more}"


def _handle_run_shortcut(params: dict) -> str:
    raw_name = (params.get("name") or "").strip()
    if not raw_name:
        raise ValueError("Shortcut 'name' parameter is required")

    available = list_installed_shortcuts()
    exact_match = next(
        (s for s in available if s == raw_name or s.lower() == raw_name.lower()),
        None,
    )
    if exact_match is None:
        raise RuntimeError(
            f"Shortcut '{raw_name}' is not in the installed shortcuts allowlist"
        )

    input_text = params.get("input", "")
    cmd = [_SHORTCUTS_BIN, "run", exact_match]

    try:
        res = subprocess.run(
            cmd,
            input=str(input_text) if input_text else None,
            capture_output=True,
            text=True,
            timeout=30.0,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Shortcut '{exact_match}' timed out after 30s") from exc

    if res.returncode != 0:
        err = (res.stderr or "").strip() or f"exit code {res.returncode}"
        raise RuntimeError(f"Shortcut '{exact_match}' failed: {err}")

    out = (res.stdout or "").strip()
    return out if out else f"Shortcut '{exact_match}' executed successfully."


_shortcuts_skill = Skill(
    name="shortcuts",
    description="List and run macOS Shortcuts (App Intents workflows) via /usr/bin/shortcuts",
    app_names={"shortcuts", "apple shortcuts", "shortcuts.app", "shortcut"},
)
_shortcuts_skill.add_intent(
    SkillIntent(
        name="list_shortcuts",
        description="List all available macOS Shortcuts on this Mac",
        params=[],
        handler=_handle_list_shortcuts,
    )
)
_shortcuts_skill.add_intent(
    SkillIntent(
        name="run_shortcut",
        description="Run a named macOS Shortcut by its exact name",
        params=["name"],
        handler=_handle_run_shortcut,
    )
)

register_skill(_shortcuts_skill)
