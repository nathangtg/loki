from __future__ import annotations

import argparse
import csv
import plistlib
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

APP_ROOTS = (
    Path("/Applications"),
    Path("/System/Applications"),
    Path("/System/Applications/Utilities"),
    Path("/Applications/Utilities"),
    Path("/System/Library/CoreServices"),  # Finder, System Events
    Path("/System/Library/CoreServices/Applications"),
    Path.home() / "Applications",
)

# Suites every scriptable app inherits; they say little about app-specific control.
GENERIC_SUITES = {"standard suite", "text suite", "type definitions", "type suite"}

# Bundle directory names that indicate a non-native UI toolkit.
TOOLKIT_MARKERS = {
    "Electron Framework.framework": "electron",
    "Chromium Embedded Framework.framework": "cef",
    "Qt": "qt",
    "QtCore.framework": "qt",
    "Flutter.framework": "flutter",
    "FlutterMacOS.framework": "flutter",
    "UnityPlayer.dylib": "unity",
    "GodotSharp": "godot",
    "jre": "java",
    "jbr": "java",  # JetBrains Runtime
}

FIELDS = [
    "name",
    "bundle_id",
    "version",
    "location",
    "category",
    "toolkit",
    "scriptable",
    "sdef_specific_commands",
    "sdef_specific_classes",
    "sdef_total_items",
    "app_intents",
    "url_schemes",
    "tier",
    "tier_reason",
]


def _load_plist(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            data = plistlib.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _find_sdef(bundle: Path, info: dict) -> Path | None:
    resources = bundle / "Contents" / "Resources"
    declared = info.get("OSAScriptingDefinition")
    if isinstance(declared, str):
        candidate = resources / declared
        if candidate.suffix != ".sdef":
            candidate = candidate.with_suffix(".sdef")
        if candidate.is_file():
            return candidate
    if resources.is_dir():
        found = sorted(resources.glob("*.sdef"))
        if found:
            return found[0]
    return None


def _parse_sdef(path: Path) -> tuple[int, int, int]:
    """Return (app-specific commands, app-specific classes, total items)."""
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return 0, 0, 0
    commands = classes = total = 0
    for suite in root.iter("suite"):
        n_cmd = len(suite.findall("command"))
        n_cls = len(suite.findall("class")) + len(suite.findall("class-extension"))
        n_evt = len(suite.findall("event"))
        total += n_cmd + n_cls + n_evt
        if (suite.get("name") or "").strip().lower() in GENERIC_SUITES:
            continue
        commands += n_cmd + n_evt
        classes += n_cls
    return commands, classes, total


def _toolkit(bundle: Path, info: dict) -> str:
    frameworks = bundle / "Contents" / "Frameworks"
    names: set[str] = set()
    for folder in (
        frameworks,
        bundle / "Contents",
        bundle / "Contents" / "MacOS",
        bundle / "Contents" / "Resources",
    ):
        if folder.is_dir():
            try:
                names.update(p.name for p in folder.iterdir())
            except OSError:
                pass
    for marker, label in TOOLKIT_MARKERS.items():
        if label and marker in names:
            return label
    if any(re.match(r"^(libjvm|libjli)", n) for n in names):
        return "java"
    if (bundle / "Contents" / "iOSSupport").exists() or info.get("UIDeviceFamily"):
        return "catalyst"
    if (bundle / "Wrapper").is_dir() or (bundle / "WrappedBundle").exists():
        return "ios-on-mac"
    return "native"


def _app_intents(bundle: Path) -> bool:
    return (bundle / "Contents" / "Resources" / "Metadata.appintents").exists()


def _url_schemes(info: dict) -> list[str]:
    schemes: list[str] = []
    for entry in info.get("CFBundleURLTypes") or []:
        if isinstance(entry, dict):
            schemes.extend(
                s for s in entry.get("CFBundleURLSchemes") or [] if isinstance(s, str)
            )
    return sorted({s for s in schemes if s.lower() not in {"http", "https"}})


FOREIGN_TOOLKITS = {"electron", "cef", "qt", "flutter", "unity", "godot", "java"}


def classify(row: dict) -> tuple[str, str]:
    """Static heuristic. Thresholds are judgement calls, not measurements."""
    specific = int(row["sdef_specific_commands"]) + int(row["sdef_specific_classes"])
    classes = int(row["sdef_specific_classes"])
    toolkit = row["toolkit"]
    scriptable = row["scriptable"] == "yes"
    if scriptable and (specific >= 8 or classes >= 4):
        return (
            "T1",
            f"scripting dictionary with an object model ({classes} classes, {specific} items)",
        )
    if scriptable or row["app_intents"] == "yes":
        parts = []
        if scriptable:
            parts.append(f"thin scripting dictionary ({specific} app-specific items)")
        if row["app_intents"] == "yes":
            parts.append("App Intents metadata")
        return "T2", " + ".join(parts)
    if toolkit in FOREIGN_TOOLKITS:
        return "T4", f"{toolkit} UI; accessibility tree quality unknown" + (
            "; URL scheme gives deep links only" if row["url_schemes"] else ""
        )
    if row["url_schemes"]:
        return "T2", "custom URL scheme (open/navigate only)"
    return "T3", f"{toolkit} app with no scripting or intents; accessibility tree only"


def audit_bundle(bundle: Path) -> dict | None:
    info = _load_plist(bundle / "Contents" / "Info.plist")
    if not info:
        return None
    sdef = _find_sdef(bundle, info)
    if (info.get("LSUIElement") or info.get("LSBackgroundOnly")) and sdef is None:
        return None  # helper/agent bundles are not user-facing apps
    commands = classes = total = 0
    scriptable = "no"
    if sdef is not None or info.get("NSAppleScriptEnabled"):
        scriptable = "yes"
    if sdef is not None:
        commands, classes, total = _parse_sdef(sdef)

    row = {
        "name": bundle.stem,
        "bundle_id": info.get("CFBundleIdentifier", ""),
        "version": info.get("CFBundleShortVersionString", ""),
        "location": str(bundle.parent),
        "category": (info.get("LSApplicationCategoryType") or "").replace(
            "public.app-category.", ""
        ),
        "toolkit": _toolkit(bundle, info),
        "scriptable": scriptable,
        "sdef_specific_commands": commands,
        "sdef_specific_classes": classes,
        "sdef_total_items": total,
        "app_intents": "yes" if _app_intents(bundle) else "no",
        "url_schemes": ";".join(_url_schemes(info)),
    }
    row["tier"], row["tier_reason"] = classify(row)
    return row


# /System/Library/CoreServices is full of system helpers; only these are user-relevant.
CORE_SERVICES = Path("/System/Library/CoreServices")
CORE_SERVICES_ALLOWLIST = {"Finder.app", "System Events.app"}


def iter_bundles() -> list[Path]:
    seen: set[Path] = set()
    bundles: list[Path] = []
    for root in APP_ROOTS:
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if root == CORE_SERVICES and entry.name not in CORE_SERVICES_ALLOWLIST:
                continue
            if entry.suffix == ".app" and entry.resolve() not in seen:
                seen.add(entry.resolve())
                bundles.append(entry)
    return bundles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--out", type=Path, default=Path("research/data/app-automatability.csv")
    )
    args = parser.parse_args()

    rows = [r for b in iter_bundles() if (r := audit_bundle(b))]
    rows.sort(
        key=lambda r: (
            r["tier"],
            -int(r["sdef_specific_commands"]) - int(r["sdef_specific_classes"]),
            r["name"].lower(),
        )
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    counts = Counter(r["tier"] for r in rows)
    print(f"audited {len(rows)} apps -> {args.out}")
    for tier in sorted(counts):
        print(f"  {tier}: {counts[tier]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
