from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from src.skills.applescript_utils import run_applescript, escape_as_string
from src.skills.base import Skill, SkillIntent
from src.skills.registry import register_skill
from src.skills.shell_utils import human_size

_MDFIND_BIN = "/usr/bin/mdfind"
_KIND_FILTERS = {
    "pdf": 'kMDItemContentType == "com.adobe.pdf"',
    "presentation": 'kMDItemContentTypeTree == "public.presentation"',
    "spreadsheet": 'kMDItemContentTypeTree == "public.spreadsheet"',
    "document": 'kMDItemContentTypeTree == "public.composite-content"',
    "image": 'kMDItemContentTypeTree == "public.image"',
    "audio": 'kMDItemContentTypeTree == "public.audio"',
    "video": 'kMDItemContentTypeTree == "public.movie"',
}


def spotlight_locate_paths(
    query: str,
    folder: str = "",
    kind: str = "",
    limit: int = 10,
) -> list[Path]:
    clean_q = (query or "").strip().replace('"', "").replace("'", "")
    if not clean_q or not Path(_MDFIND_BIN).exists():
        return []

    kind_key = (kind or "").strip().lower()
    kind_clause = _KIND_FILTERS.get(kind_key, "")

    scope_args = ["-onlyin", str(Path.home())]
    if folder:
        scope_dir = Path(folder).expanduser().resolve()
        if scope_dir.is_dir():
            scope_args = ["-onlyin", str(scope_dir)]

    def _run_mdfind(clause: str) -> list[Path]:
        expr = f"{clause} && {kind_clause}" if kind_clause else clause
        cmd = [_MDFIND_BIN, *scope_args, expr]
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if res.returncode != 0:
            return []
        found: list[Path] = []
        for line in res.stdout.splitlines():
            p = Path(line.strip())
            if p.exists() and "/Library/" not in str(p) and "/." not in str(p):
                found.append(p)
        found.sort(
            key=lambda x: x.stat().st_mtime if x.exists() else 0.0,
            reverse=True,
        )
        return found[: max(1, min(25, limit))]

    try:
        fast_matches = _run_mdfind(
            f'(kMDItemFSName == "*{clean_q}*"cdw || kMDItemDisplayName == "*{clean_q}*"cdw)'
        )
        if fast_matches:
            return fast_matches
        return _run_mdfind(f'kMDItemTextContent == "*{clean_q}*"cdw')
    except Exception:
        return []


def _spotlight_search(params: dict[str, Any]) -> str:
    query = (params.get("query") or "").strip()
    if not query:
        raise ValueError("Missing 'query' parameter for spotlight_search")
    folder = (params.get("folder") or "").strip()
    kind = (params.get("kind") or "").strip()
    matches = spotlight_locate_paths(query, folder=folder, kind=kind, limit=10)
    if not matches:
        return f"No Spotlight matches found for '{query}'."
    lines = []
    for p in matches:
        try:
            sz = human_size(p.stat().st_size)
        except OSError:
            sz = "?"
        lines.append(f"- {p} ({sz})")
    return f"Spotlight matches for '{query}' ({len(matches)}):\n" + "\n".join(lines)


def _find_recent_files(params: dict[str, Any]) -> str:
    folder_raw = (params.get("folder") or "~/Downloads").strip()
    target = Path(folder_raw).expanduser().resolve()
    if not target.is_dir():
        raise FileNotFoundError(f"Directory not found: {folder_raw}")
    try:
        count = max(1, min(20, int(params.get("count") or 10)))
    except (ValueError, TypeError):
        count = 10
    files = [f for f in target.iterdir() if f.is_file() and not f.name.startswith(".")]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    top = files[:count]
    if not top:
        return f"No files found in '{target}'."
    lines = [f"- {f} ({human_size(f.stat().st_size)})" for f in top]
    return f"Most recent {len(top)} file(s) in '{target}':\n" + "\n".join(lines)


def _reveal_file(params: dict[str, Any]) -> str:
    path = params["path"]
    safe = escape_as_string(path)
    script = f'''
tell application "Finder"
    activate
    reveal POSIX file "{safe}"
end tell
'''
    run_applescript(script)
    return f"Revealed '{path}' in Finder."


def _open_path(params: dict[str, Any]) -> str:
    path = params["path"]
    safe = escape_as_string(path)
    script = f'''
tell application "Finder"
    activate
    open POSIX file "{safe}"
end tell
'''
    run_applescript(script)
    return f"Opened '{path}' in Finder."


def _new_folder(params: dict[str, Any]) -> str:
    path = params["path"]

    from pathlib import Path as _Path
    p = _Path(path).expanduser()
    parent = str(p.parent)
    folder_name = p.name
    safe_parent = escape_as_string(parent)
    safe_name = escape_as_string(folder_name)

    script = f'''
tell application "Finder"
    make new folder at POSIX file "{safe_parent}" with properties {{name:"{safe_name}"}}
end tell
'''
    run_applescript(script)
    return f"Created folder '{path}'."


def _get_info(params: dict[str, Any]) -> str:
    path = params["path"]
    safe = escape_as_string(path)
    script = f'''
tell application "Finder"
    set theItem to POSIX file "{safe}" as alias
    set itemName to name of theItem
    set itemKind to kind of theItem
    set itemSize to size of theItem
    return itemName & " | " & itemKind & " | " & (itemSize as string) & " bytes"
end tell
'''
    result = run_applescript(script)
    return result or f"Info for '{path}' retrieved."


_finder_skill = Skill(
    name="finder",
    description="Search files via macOS Spotlight (mdfind), list recent files, reveal, open, create folders, and inspect items via Finder",
    app_names={"finder", "finder.app", "spotlight", "mdfind"},
)

_finder_skill.add_intent(
    SkillIntent(
        name="spotlight_search",
        description="Search for files or documents by name or content using macOS Spotlight (mdfind) and return their exact POSIX paths",
        params=["query"],
        handler=_spotlight_search,
    )
)
_finder_skill.add_intent(
    SkillIntent(
        name="find_recent_files",
        description="List the most recently modified files in a folder (defaults to ~/Downloads)",
        params=[],
        handler=_find_recent_files,
    )
)
_finder_skill.add_intent(
    SkillIntent(
        name="reveal_file",
        description="Reveal a file or folder in a Finder window so the user can see it",
        params=["path"],
        handler=_reveal_file,
    )
)
_finder_skill.add_intent(
    SkillIntent(
        name="open_path",
        description="Open a file or folder in Finder (equivalent to double-clicking it)",
        params=["path"],
        handler=_open_path,
    )
)
_finder_skill.add_intent(
    SkillIntent(
        name="new_folder",
        description="Create a new folder at the given absolute path",
        params=["path"],
        handler=_new_folder,
    )
)
_finder_skill.add_intent(
    SkillIntent(
        name="get_info",
        description="Get basic info (name, kind, size) about a file or folder",
        params=["path"],
        handler=_get_info,
    )
)

register_skill(_finder_skill)
