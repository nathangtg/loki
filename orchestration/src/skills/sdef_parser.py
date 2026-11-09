from __future__ import annotations

import logging
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

LOGGER = logging.getLogger(__name__)

_SKIP_SUITES = {
    "standard suite",
    "text suite",
    "type names suite",
    "generic suite",
}

_DENYLIST_COMMANDS = {
    "delete", "quit", "empty trash", "erase", "reset", "restart",
    "shut down", "log out", "do shell script", "run script", "eject",
}

_APP_SEARCH_DIRS = [
    "/Applications",
    "/System/Applications",
    "/System/Applications/Utilities",
    "/System/Library/CoreServices",
]

_PRIMITIVE_PROP_TYPES = {"text", "integer", "real", "number", "boolean"}
_SKIP_APP_PROPERTIES = {
    "class",
    "properties",
    "frontmost",
    "version",
    "name",
    "id",
    "selection",
    "clipboard",
}


def find_app_bundle_path(app_name: str) -> str | None:
    name = (app_name or "").strip()
    if not name:
        return None
    bare = name[:-4] if name.lower().endswith(".app") else name

    for d in _APP_SEARCH_DIRS:
        candidate = Path(d) / f"{bare}.app"
        if candidate.exists():
            return str(candidate)

    home_apps = Path.home() / "Applications" / f"{bare}.app"
    if home_apps.exists():
        return str(home_apps)

    for d in _APP_SEARCH_DIRS:
        try:
            for entry in Path(d).iterdir():
                if entry.suffix == ".app" and entry.stem.lower() == bare.lower():
                    return str(entry)
        except OSError:
            continue
    return None


def _read_bundled_sdef_file(bundle_path: str) -> str | None:
    resources = Path(bundle_path) / "Contents" / "Resources"
    if not resources.is_dir():
        return None
    try:
        sdef_files = list(resources.glob("*.sdef"))
    except OSError:
        return None
    if not sdef_files:
        return None
    try:
        return sdef_files[0].read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        LOGGER.warning("sdef_parser: failed to read %s: %s", sdef_files[0], exc)
        return None


def _run_sdef_cli(bundle_path: str, timeout: float) -> str | None:
    try:
        result = subprocess.run(
            ["sdef", bundle_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        LOGGER.info("sdef_parser: sdef CLI unavailable: %s", exc)
        return None

    if result.returncode != 0 or not (result.stdout or "").strip():
        return None
    return result.stdout


def fetch_sdef_xml(app_name: str, timeout: float = 10.0) -> str | None:
    bundle_path = find_app_bundle_path(app_name)
    if not bundle_path:
        LOGGER.info("sdef_parser: could not locate app bundle for '%s'", app_name)
        return None

    xml_text = _read_bundled_sdef_file(bundle_path)
    if xml_text:
        return xml_text

    xml_text = _run_sdef_cli(bundle_path, timeout)
    if xml_text:
        return xml_text

    LOGGER.info("sdef_parser: '%s' has no scripting dictionary (not scriptable)", app_name)
    return None


def _parse_bool_attr(elem, attr: str, default: bool = False) -> bool:
    val = elem.get(attr)
    if val is None:
        return default
    return val.strip().lower() in ("yes", "true", "1")


def parse_sdef_commands(xml_text: str, max_commands: int = 25) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        LOGGER.warning("sdef_parser: failed to parse sdef XML: %s", exc)
        return []

    commands: list[dict] = []

    for suite in root.iter("suite"):
        suite_name = (suite.get("name") or "").strip().lower()
        if suite_name in _SKIP_SUITES:
            continue

        for cmd in suite.findall("command"):
            name = (cmd.get("name") or "").strip()
            if not name or name.lower() in _DENYLIST_COMMANDS:
                continue

            direct_param = None
            dp_elem = cmd.find("direct-parameter")
            if dp_elem is not None:
                direct_param = {
                    "type": dp_elem.get("type", "text"),
                    "optional": _parse_bool_attr(dp_elem, "optional", default=False),
                }

            parameters = []
            for p in cmd.findall("parameter"):
                p_name = (p.get("name") or "").strip()
                if not p_name:
                    continue
                parameters.append({
                    "name": p_name,
                    "type": p.get("type", "text"),
                    "optional": _parse_bool_attr(p, "optional", default=True),
                    "description": (p.get("description") or "").strip(),
                })

            commands.append({
                "name": name,
                "description": (cmd.get("description") or "").strip() or name,
                "direct_parameter": direct_param,
                "parameters": parameters,
            })

            if len(commands) >= max_commands:
                return commands

    return commands


def parse_sdef_properties(xml_text: str, max_properties: int = 15) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        LOGGER.warning("sdef_parser: failed to parse sdef XML for properties: %s", exc)
        return []

    props: list[dict] = []
    seen_names: set[str] = set()

    for suite in root.iter("suite"):
        suite_name = (suite.get("name") or "").strip().lower()
        if suite_name in _SKIP_SUITES:
            continue

        for cls in suite.findall("class"):
            cls_name = (cls.get("name") or "").strip().lower()
            if cls_name != "application":
                continue

            for prop in cls.findall("property"):
                p_name = (prop.get("name") or "").strip()
                if not p_name or p_name.lower() in _SKIP_APP_PROPERTIES:
                    continue
                if p_name.lower() in seen_names:
                    continue

                p_type = (prop.get("type") or "text").strip().lower()
                access = (prop.get("access") or "rw").strip().lower()
                desc = (prop.get("description") or "").strip() or f"{p_name} of application"

                seen_names.add(p_name.lower())
                props.append({
                    "name": p_name,
                    "type": p_type,
                    "access": access,
                    "writable": "w" in access and p_type in _PRIMITIVE_PROP_TYPES,
                    "readable": "r" in access,
                    "description": desc,
                })
                if len(props) >= max_properties:
                    return props

    return props
