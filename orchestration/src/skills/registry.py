from __future__ import annotations

import importlib
import logging

from src.skills.base import Skill

LOGGER = logging.getLogger(__name__)

_BUILTIN_SKILL_MODULES = (
    "mail_skill",
    "spotify_skill",
    "calendar_skill",
    "notes_skill",
    "reminders_skill",
    "finder_skill",
    "file_organizer_skill",
    "shortcuts_skill",
    "documents_skill",
    "comms_media_skill",
    "system_control_skill",
    "web_search_skill",
)

_REGISTRY: list[Skill] = []
_builtins_loaded = False
_discovered_cache: dict[str, "Skill | None"] = {}


def register_skill(skill: Skill) -> None:
    _REGISTRY.append(skill)
    LOGGER.info("skills: registered '%s' for apps=%s", skill.name, sorted(skill.app_names))


def all_skills() -> list[Skill]:
    return list(_REGISTRY)


def find_skill_for_app(app_name: str) -> Skill | None:
    ensure_builtin_skills_loaded()
    for skill in _REGISTRY:
        if skill.matches_app(app_name):
            return skill
    return None


def find_skill_by_name(skill_name: str) -> Skill | None:
    ensure_builtin_skills_loaded()
    for skill in _REGISTRY:
        if skill.name == skill_name:
            return skill
    return None


def discover_skill_for_app(app_name: str) -> Skill | None:
    key = (app_name or "").strip().lower()
    if not key:
        return None
    if key in _discovered_cache:
        return _discovered_cache[key]

    try:
        from src.skills.sdef_skill_builder import build_skill_from_sdef

        skill = build_skill_from_sdef(app_name)
    except Exception as exc:
        LOGGER.warning("skills: sdef discovery failed for '%s': %s", app_name, exc)
        skill = None

    _discovered_cache[key] = skill
    if skill:
        LOGGER.info(
            "skills: dynamically discovered skill '%s' (%d intents) for app '%s'",
            skill.name, len(skill.intents), app_name,
        )
    return skill


def describe_all_skills() -> str:
    ensure_builtin_skills_loaded()
    if not _REGISTRY:
        return "(no skills registered)"
    return "\n\n".join(s.describe() for s in _REGISTRY)


def ensure_builtin_skills_loaded() -> None:
    global _builtins_loaded
    if _builtins_loaded:
        return
    _builtins_loaded = True
    for module in _BUILTIN_SKILL_MODULES:
        try:
            importlib.import_module(f"src.skills.{module}")
        except Exception as exc:
            LOGGER.warning("skills: failed to load %s: %s", module, exc)
