from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

LOGGER = logging.getLogger(__name__)

_KNOWN_APPS_FILE = (
    Path(__file__).resolve().parent.parent.parent / "data" / "known_apps.json"
)

FORBIDDEN_CHARS = {
    ";", "&", "|", "$", "`", "<", ">", "(", ")", '"', "'",
    "\\", "*", "?", "!", "{", "}", "~", "%", "[", "]", "^",
    "#", "=", "\n", "\r", "\t", "\0", "/",
}

MAC_APP_DIRS = [
    "/Applications",
    "/System/Applications",
    "/System/Applications/Utilities",
    os.path.expanduser("~/Applications"),
]


def validate_app_name(name: str) -> tuple[bool, str | None]:
    if not isinstance(name, str):
        return False, "Application name must be a string."

    name = name.strip()
    if not name:
        return False, "Application name cannot be empty."

    if len(name) > 255:
        return False, "Application name exceeds maximum length."

    if name.startswith("-"):
        return False, "Application name cannot start with a dash or flag prefix."

    for char in name:
        if ord(char) < 32 or ord(char) == 127:
            return False, f"Application name contains control character: {char!r}"
        if char in FORBIDDEN_CHARS:
            return False, f"Application name contains forbidden character: {char!r}"

    return True, None


def canonicalize_app_name(name: str) -> str:
    clean = name.strip()
    if clean.lower().endswith(".app"):
        clean = clean[:-4].strip()
    return clean


def get_installed_mac_apps(search_dirs: list[str] | None = None) -> list[str]:
    dirs = search_dirs if search_dirs is not None else MAC_APP_DIRS
    seen: set[str] = set()
    apps: list[str] = []

    for d in dirs:
        if not os.path.isdir(d):
            continue
        try:
            with os.scandir(d) as entries:
                for entry in sorted(entries, key=lambda e: e.name.lower()):
                    entry_name = entry.name.strip()
                    if entry_name.endswith(".app") and entry_name not in seen:
                        seen.add(entry_name)
                        apps.append(entry_name)
        except OSError as exc:
            LOGGER.warning("Could not scan directory %s: %s", d, exc)

    if "Finder.app" not in seen and os.path.isdir("/System/Library/CoreServices/Finder.app"):
        apps.append("Finder.app")

    return apps


class AppKnowledge:
    _instance: AppKnowledge | None = None

    def __new__(cls) -> AppKnowledge:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
        return cls._instance

    def __init__(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._installed_apps: list[str] = []
        self._canonical_apps: list[str] = []
        self._canonical_set: set[str] = set()
        self._lower_map: dict[str, list[str]] = {}
        self._load()

    def _load(self) -> None:
        if _KNOWN_APPS_FILE.exists():
            try:
                with open(_KNOWN_APPS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                apps = data.get("apps", [])
                if apps:
                    self._build_index(apps)
                    LOGGER.debug(
                        "Loaded known_apps.json with %d applications", len(apps)
                    )
                    return
            except Exception as exc:
                LOGGER.warning("Failed to load known_apps.json: %s", exc)

        self.scan_and_save()

    def scan_and_save(self, search_dirs: list[str] | None = None) -> list[str]:
        apps = get_installed_mac_apps(search_dirs)
        self._build_index(apps)
        try:
            _KNOWN_APPS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(_KNOWN_APPS_FILE, "w", encoding="utf-8") as f:
                json.dump({"apps": apps}, f, indent=2)
            LOGGER.info("Persisted %d applications to %s", len(apps), _KNOWN_APPS_FILE)
        except Exception as exc:
            LOGGER.warning("Could not persist known_apps.json: %s", exc)
        return apps

    def _build_index(self, apps: list[str]) -> None:
        self._installed_apps = list(apps)
        canonical_list: list[str] = []
        canonical_set: set[str] = set()
        lower_map: dict[str, list[str]] = {}

        for raw in apps:
            canon = canonicalize_app_name(raw)
            if not canon or canon in canonical_set:
                continue
            is_valid, _ = validate_app_name(canon)
            if not is_valid:
                continue
            canonical_set.add(canon)
            canonical_list.append(canon)
            lower_map.setdefault(canon.lower(), []).append(canon)

        self._canonical_apps = canonical_list
        self._canonical_set = canonical_set
        self._lower_map = lower_map

    def get_installed_apps(self) -> list[str]:
        return list(self._installed_apps)

    def get_canonical_names(self) -> list[str]:
        return list(self._canonical_apps)

    def resolve_app(
        self,
        target: str,
        custom_apps: list[str] | None = None,
    ) -> tuple[str | None, str | None]:
        is_valid, err = validate_app_name(target)
        if not is_valid:
            return None, f"Malformed application name: {err}"

        if custom_apps is not None:
            canon_list: list[str] = []
            canon_set: set[str] = set()
            l_map: dict[str, list[str]] = {}
            for raw in custom_apps:
                canon = canonicalize_app_name(raw)
                if not canon or canon in canon_set:
                    continue
                val, _ = validate_app_name(canon)
                if not val:
                    continue
                canon_set.add(canon)
                canon_list.append(canon)
                l_map.setdefault(canon.lower(), []).append(canon)
        else:
            canon_list = self._canonical_apps
            canon_set = self._canonical_set
            l_map = self._lower_map

        if not canon_list:
            return None, f"No installed applications available to resolve {target!r}"

        target_canon = canonicalize_app_name(target)
        target_lower = target_canon.lower()

        if target_canon in canon_set:
            return target_canon, None

        lower_matches = l_map.get(target_lower, [])
        if len(lower_matches) == 1:
            return lower_matches[0], None
        elif len(lower_matches) > 1:
            return None, (
                f"Ambiguous application name {target!r}: matches multiple case variants "
                f"{sorted(lower_matches)}"
            )

        word_matches = [
            c for c in canon_list
            if target_lower in [w.lower() for w in re.split(r"[\s_\-]+", c)]
        ]
        if len(word_matches) == 1:
            return word_matches[0], None
        elif len(word_matches) > 1:
            return None, (
                f"Ambiguous application name {target!r}: matches multiple applications "
                f"{sorted(word_matches)}"
            )

        sub_matches = [c for c in canon_list if target_lower in c.lower()]
        if len(sub_matches) == 1:
            return sub_matches[0], None
        elif len(sub_matches) > 1:
            return None, (
                f"Ambiguous application name {target!r}: matches multiple applications "
                f"{sorted(sub_matches)}"
            )

        return None, f"Unknown application: {target!r} is not installed"

    def format_for_prompt(self, limit: int = 60) -> str:
        apps = self._canonical_apps[:limit]
        if not apps:
            return "INSTALLED APPLICATIONS: None detected."
        lines = [f"- {a}" for a in apps]
        return "INSTALLED APPLICATIONS (choose ONLY from this list):\n" + "\n".join(lines)
