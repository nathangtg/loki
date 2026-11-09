from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from src.skills.applescript_utils import run_applescript, escape_as_string
from src.skills.base import Skill, SkillIntent
from src.skills.sdef_parser import fetch_sdef_xml, parse_sdef_commands, parse_sdef_properties

LOGGER = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_CACHE_FILE = _DATA_DIR / "discovered_skills.json"


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    return slug or "unknown"


def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        try:
            with open(_CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception as exc:
            LOGGER.warning("sdef_skill_builder: failed to load cache: %s", exc)
    return {}


def _save_cache(cache: dict) -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception as exc:
        LOGGER.warning("sdef_skill_builder: failed to save cache: %s", exc)


def _quote_value(value, param_type: str) -> str:
    ptype = (param_type or "text").lower()
    if ptype in ("integer", "real", "number"):
        try:
            return str(float(value) if "." in str(value) else int(value))
        except (ValueError, TypeError):
            pass
    elif ptype == "boolean":
        sval = str(value).strip().lower()
        return "true" if sval in ("true", "1", "yes") else "false"
    return f'"{escape_as_string(str(value))}"'


def _make_handler(app_name: str, command: dict):

    command_name = command["name"]
    direct_param = command.get("direct_parameter")
    parameters = command.get("parameters", [])

    def handler(params: dict) -> str:
        parts = [command_name]

        if direct_param is not None:
            dp_value = params.get("direct_parameter") or params.get("target") or params.get("value")
            if dp_value:
                parts.append(_quote_value(dp_value, direct_param.get("type", "text")))
            elif not direct_param.get("optional", False):
                raise ValueError(f"Command '{command_name}' requires a direct parameter")

        for p in parameters:
            val = params.get(p["name"])
            if val:
                parts.append(p["name"])
                parts.append(_quote_value(val, p.get("type", "text")))
            elif not p.get("optional", True):
                raise ValueError(f"Command '{command_name}' requires parameter '{p['name']}'")

        cmd_line = " ".join(parts)
        script = f'tell application "{app_name}"\n    {cmd_line}\nend tell'
        result = run_applescript(script)
        return result or f"{command_name} executed successfully"

    return handler


def _make_prop_getter(app_name: str, prop: dict):
    prop_name = prop["name"]
    prop_type = (prop.get("type") or "text").lower()

    def handler(params: dict) -> str:
        if prop_type == "track":
            script = (
                f'tell application "{app_name}"\n'
                f'    return (name of {prop_name}) & " by " & (artist of {prop_name})\n'
                f'end tell'
            )
        else:
            script = f'tell application "{app_name}" to return ({prop_name} as string)'
        result = run_applescript(script)
        return result or f"{prop_name}: (empty)"

    return handler


def _make_prop_setter(app_name: str, prop: dict):
    prop_name = prop["name"]
    prop_type = (prop.get("type") or "text").lower()

    def handler(params: dict) -> str:
        val = params.get("value")
        if val is None or str(val).strip() == "":
            raise ValueError(f"Property '{prop_name}' requires a 'value' parameter")
        quoted = _quote_value(val, prop_type)
        script = f'tell application "{app_name}" to set {prop_name} to {quoted}'
        run_applescript(script)
        return f"Set {prop_name} of {app_name} to {val}"

    return handler


def _commands_to_skill(
    app_name: str,
    commands: list[dict],
    properties: list[dict] | None = None,
) -> Skill | None:
    properties = properties or []
    if not commands and not properties:
        return None

    skill = Skill(
        name=_slugify(app_name),
        description=f"Dynamically discovered from {app_name}'s AppleScript dictionary (.sdef)",
        app_names={app_name.strip().lower()},
    )

    for command in commands:
        intent_params = []
        if command.get("direct_parameter") is not None:
            intent_params.append("direct_parameter")
        intent_params += [p["name"] for p in command.get("parameters", []) if not p.get("optional", True)]

        skill.add_intent(SkillIntent(
            name=_slugify(command["name"]),
            description=command["description"],
            params=intent_params,
            handler=_make_handler(app_name, command),
        ))

    for prop in properties:
        slug = _slugify(prop["name"])
        if prop.get("readable", True):
            get_name = f"get_{slug}"
            if get_name not in skill.intents:
                skill.add_intent(SkillIntent(
                    name=get_name,
                    description=f"Get {prop['name']}: {prop.get('description', '')}",
                    params=[],
                    handler=_make_prop_getter(app_name, prop),
                ))
        if prop.get("writable", False):
            set_name = f"set_{slug}"
            if set_name not in skill.intents:
                skill.add_intent(SkillIntent(
                    name=set_name,
                    description=f"Set {prop['name']}: {prop.get('description', '')}",
                    params=["value"],
                    handler=_make_prop_setter(app_name, prop),
                ))

    return skill


def build_skill_from_sdef(app_name: str, use_cache: bool = True) -> Skill | None:
    key = app_name.strip().lower()
    cache = _load_cache() if use_cache else {}

    if use_cache and key in cache:
        cached_entry = cache[key]
        if isinstance(cached_entry, dict):
            commands = cached_entry.get("commands", [])
            properties = cached_entry.get("properties", [])
            LOGGER.info(
                "sdef_skill_builder: loaded '%s' from cache (%d commands, %d properties)",
                app_name, len(commands), len(properties),
            )
            return _commands_to_skill(app_name, commands, properties)

    xml_text = fetch_sdef_xml(app_name)
    if not xml_text:
        if use_cache:
            cache[key] = {"commands": [], "properties": []}
            _save_cache(cache)
        return None

    commands = parse_sdef_commands(xml_text)
    properties = parse_sdef_properties(xml_text)
    LOGGER.info(
        "sdef_skill_builder: discovered %d command(s) and %d property(s) for '%s' via sdef",
        len(commands), len(properties), app_name,
    )

    if use_cache:
        cache[key] = {"commands": commands, "properties": properties}
        _save_cache(cache)

    return _commands_to_skill(app_name, commands, properties)
