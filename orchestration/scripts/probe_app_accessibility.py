from __future__ import annotations

import argparse
import csv
import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from AppKit import NSRunningApplication
from ApplicationServices import (
    AXIsProcessTrusted,
    AXUIElementCopyActionNames,
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXUIElementSetAttributeValue,
    AXUIElementSetMessagingTimeout,
    kAXErrorSuccess,
)

APP_DIRS = (
    Path("/System/Applications"),
    Path("/System/Applications/Utilities"),
    Path("/Applications"),
    Path("/Applications/Utilities"),
)


ALLOWLIST: dict[str, str] = {
    "Calculator": "no personal data; standard AppKit controls",
    "Clock": "Catalyst app; shows world clock defaults only",
    "Dictionary": "reference app; no account",
    "Font Book": "lists installed fonts, not personal data (rows are not read)",
    "Chess": "game; no account",
    "Activity Monitor": "process table is a content container and is never descended",
    "Disk Utility": "device list is a content container and is never descended",
    "Grapher": "opens a template chooser; no personal files",
    "Audio MIDI Setup": "device list is a content container and is never descended",
    "ColorSync Utility": "no personal data",
    "System Settings": "sidebar is an outline and is never descended (contains account name)",
    "Terminal": "shell window; text area is never descended or read",
    "draw.io": "Electron; recent-files list is not read. Default exposure only",
    "MuseScore 4": "Qt; start screen is not read. Default exposure only",
}

# Foreign-toolkit apps for which Loki's own path sets the accessibility switches.
SWITCH_ELIGIBLE = {"draw.io", "MuseScore 4"}
LOKI_AX_SWITCHES = (
    "AXManualAccessibility",
    "AXEnhancedUserInterface",
)  # as in ax_locator.py
SWITCH_SETTLE_S = 6.0

CONTROL_ROLES = {
    "AXButton",
    "AXCheckBox",
    "AXRadioButton",
    "AXPopUpButton",
    "AXMenuButton",
    "AXComboBox",
    "AXSlider",
    "AXIncrementor",
    "AXDisclosureTriangle",
    "AXLink",
    "AXTabGroup",
    "AXSegmentedControl",
    "AXColorWell",
    "AXSwitch",
    "AXStepper",
    "AXMenuBarItem",
    "AXToolbar",
    "AXTextField",
    "AXSearchField",
    "AXSecureTextField",
}
# Roles whose children are user content. Counted, never entered.
NO_DESCEND_ROLES = {
    "AXTable",
    "AXOutline",
    "AXList",
    "AXBrowser",
    "AXTextArea",
    "AXWebArea",
    "AXRow",
    "AXCell",
    "AXColumn",
    "AXStaticText",
    "AXImage",
    "AXLayoutArea",
    "AXScrollBar",
    "AXGrid",
    "AXCollection",
}
GENERIC_ROLES = {"AXGroup", "AXUnknown", "AXSplitGroup", "AXLayoutItem"}

MAX_NODES = 600
MAX_DEPTH = 14
AX_TIMEOUT_S = 2.0
WINDOW_WAIT_S = 12.0
QUIT_WAIT_S = 5.0
QUIT_ATTEMPTS = 8  # MuseScore needed 6+ polite requests

FIELDS = [
    "app",
    "bundle_id",
    "switch_state",
    "status",
    "reason",
    "seconds_to_first_window",
    "windows",
    "menubar_items",
    "nodes_visited",
    "truncated",
    "max_depth",
    "controls_total",
    "controls_labelled",
    "labelled_ratio",
    "controls_with_action",
    "generic_ratio",
    "content_containers_skipped",
    "web_areas",
    "verdict",
    "left_running",
]

ATTRIBUTES_READ = [
    "AXWindows",
    "AXMenuBar",
    "AXChildren",
    "AXRole",
    "AXTitle / AXDescription / AXTitleUIElement (only whether any is non-empty)",
    "AX action NAMES (never performed)",
]
NEVER_READ = [
    "AXValue",
    "AXSelectedText",
    "AXDocument",
    "AXURL",
    "AXFilename",
    "window titles",
    "any text content",
    "screenshots",
    "keystrokes",
    "mouse input",
]


def find_app(name: str) -> Path | None:
    for base in APP_DIRS:
        candidate = base / f"{name}.app"
        if candidate.is_dir():
            return candidate
    return None


def bundle_id_of(app_path: Path) -> str:
    import plistlib

    try:
        with (app_path / "Contents" / "Info.plist").open("rb") as fh:
            return str(plistlib.load(fh).get("CFBundleIdentifier", ""))
    except Exception:
        return ""


def running(bundle_id: str):
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
    return apps[0] if apps else None


def ax_get(element, attribute):
    err, value = AXUIElementCopyAttributeValue(element, attribute, None)
    return value if err == kAXErrorSuccess else None


def has_accessible_name(element) -> bool:
    """True if the element exposes any non-empty name. The string is discarded."""
    for attr in ("AXTitle", "AXDescription"):
        value = ax_get(element, attr)
        if isinstance(value, str) and value.strip():
            return True
    return ax_get(element, "AXTitleUIElement") is not None


def has_action(element) -> bool:
    err, names = AXUIElementCopyActionNames(element, None)
    return err == kAXErrorSuccess and bool(names)


def walk(root, stats: dict) -> None:
    stack = [(root, 0)]
    while stack:
        element, depth = stack.pop()
        if stats["nodes_visited"] >= MAX_NODES:
            stats["truncated"] = True
            return
        stats["nodes_visited"] += 1
        stats["max_depth"] = max(stats["max_depth"], depth)

        role = ax_get(element, "AXRole")
        role = role if isinstance(role, str) else "AXUnknown"
        stats["roles"][role] = stats["roles"].get(role, 0) + 1

        if role == "AXWebArea":
            stats["web_areas"] += 1
        if role in GENERIC_ROLES:
            stats["generic"] += 1
        if role in CONTROL_ROLES:
            stats["controls_total"] += 1
            if has_accessible_name(element):
                stats["controls_labelled"] += 1
            if has_action(element):
                stats["controls_with_action"] += 1

        if role in NO_DESCEND_ROLES:
            stats["content_containers_skipped"] += 1
            continue
        if depth >= MAX_DEPTH:
            stats["truncated"] = True
            continue
        children = ax_get(element, "AXChildren") or []
        for child in reversed(list(children)):
            stack.append((child, depth + 1))


def verdict_for(stats: dict, windows: int) -> str:
    if windows == 0:
        return "no_window"
    total = stats["controls_total"]
    if total < 1:
        return "opaque"
    ratio = stats["controls_labelled"] / total
    if total >= 5 and ratio >= 0.8:
        return "usable"
    if ratio >= 0.4:
        return "partial"
    return "poorly_labelled"


def set_loki_switches(pid: int) -> bool:
    """Set the same two attributes Loki's ax_locator sets. Returns True on success."""
    app_el = AXUIElementCreateApplication(pid)
    results = [
        AXUIElementSetAttributeValue(app_el, attr, True) for attr in LOKI_AX_SWITCHES
    ]
    time.sleep(SWITCH_SETTLE_S)
    return results[0] == kAXErrorSuccess


def probe_running_app(pid: int) -> dict:
    app_el = AXUIElementCreateApplication(pid)
    AXUIElementSetMessagingTimeout(app_el, AX_TIMEOUT_S)
    deadline = time.monotonic() + WINDOW_WAIT_S
    started = time.monotonic()
    windows = []
    while time.monotonic() < deadline:
        windows = list(ax_get(app_el, "AXWindows") or [])
        if windows:
            break
        time.sleep(0.4)
    first_window_s = round(time.monotonic() - started, 1) if windows else ""

    stats = {
        "nodes_visited": 0,
        "truncated": False,
        "max_depth": 0,
        "controls_total": 0,
        "controls_labelled": 0,
        "controls_with_action": 0,
        "generic": 0,
        "content_containers_skipped": 0,
        "web_areas": 0,
        "roles": {},
    }
    for window in windows:
        walk(window, stats)

    menubar = ax_get(app_el, "AXMenuBar")
    menubar_items = (
        len(list(ax_get(menubar, "AXChildren") or [])) if menubar is not None else 0
    )

    total = stats["controls_total"]
    visited = stats["nodes_visited"]
    return {
        "windows": len(windows),
        "seconds_to_first_window": first_window_s,
        "menubar_items": menubar_items,
        "nodes_visited": visited,
        "truncated": stats["truncated"],
        "max_depth": stats["max_depth"],
        "controls_total": total,
        "controls_labelled": stats["controls_labelled"],
        "labelled_ratio": round(stats["controls_labelled"] / total, 2) if total else "",
        "controls_with_action": stats["controls_with_action"],
        "generic_ratio": round(stats["generic"] / visited, 2) if visited else "",
        "content_containers_skipped": stats["content_containers_skipped"],
        "web_areas": stats["web_areas"],
        "verdict": verdict_for(stats, len(windows)),
    }


def quit_if_launched(app, bundle_id: str, launched_by_probe: bool) -> bool:
    """Ask a probe-launched app to quit. Returns True if it is still running.

    NSRunningApplication.isTerminated() is only refreshed by a run loop, so the
    check polls the live process list by bundle id instead.
    """
    if not launched_by_probe:
        return False
    # Electron and Qt apps often ignore the first quit request (it dismisses a
    # splash or start window), so ask politely a few times. Never force-kill.
    for _ in range(QUIT_ATTEMPTS):
        app.terminate()
        deadline = time.monotonic() + QUIT_WAIT_S
        while time.monotonic() < deadline:
            if running(bundle_id) is None:
                return False
            time.sleep(0.3)
    return True


def probe_app(name: str, match_loki_switches: bool = False) -> list[dict]:
    """Return one row (default exposure) or two (plus Loki's switches)."""
    row = {f: "" for f in FIELDS}
    row["app"] = name
    row["switch_state"] = "default"
    path = find_app(name)
    if path is None:
        row.update(status="skipped", reason="not installed")
        return [row]
    bundle_id = bundle_id_of(path)
    row["bundle_id"] = bundle_id

    if running(bundle_id) is not None:
        row.update(status="skipped", reason="already running; left untouched")
        return [row]

    subprocess.run(
        ["open", "-g", "-a", str(path)],
        check=False,
        timeout=15,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 15
    app = None
    while time.monotonic() < deadline:
        app = running(bundle_id)
        if app is not None:
            break
        time.sleep(0.3)
    if app is None:
        row.update(status="skipped", reason="did not launch within 15s")
        return [row]

    rows = [row]
    try:
        row.update(probe_running_app(app.processIdentifier()))
        row.update(status="probed", reason="")
        if match_loki_switches and name in SWITCH_ELIGIBLE:
            after = {f: "" for f in FIELDS}
            after.update(app=name, bundle_id=bundle_id, switch_state="loki_switches")
            if set_loki_switches(app.processIdentifier()):
                after.update(probe_running_app(app.processIdentifier()))
                after.update(status="probed", reason="")
            else:
                after.update(status="error", reason="switch not accepted")
            rows.append(after)
    except Exception as exc:  # report the type only; never the message
        row.update(status="error", reason=type(exc).__name__)
    finally:
        left = quit_if_launched(app, bundle_id, launched_by_probe=True)
        for r in rows:
            r["left_running"] = left
    return rows


def print_plan(apps: list[str]) -> None:
    print("DRY RUN. Nothing was launched. Re-run with --enable-probe to execute.\n")
    print("Apps that would be launched (only if not already running):")
    for name in apps:
        print(f"  - {name}: {ALLOWLIST[name]}")
    print("\nAttributes read:")
    for item in ATTRIBUTES_READ:
        print(f"  + {item}")
    print(
        "\nOptional write (only with --match-loki-ax-switches, Electron/Qt apps only):"
    )
    for attr in LOKI_AX_SWITCHES:
        print(
            f"  ! set {attr}=true on {', '.join(sorted(SWITCH_ELIGIBLE))} (same as ax_locator.py)"
        )
    print("\nNever read or stored:")
    for item in NEVER_READ:
        print(f"  x {item}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--enable-probe",
        action="store_true",
        help="actually launch apps and read accessibility metadata",
    )
    parser.add_argument(
        "--match-loki-ax-switches",
        action="store_true",
        help="for Electron/Qt apps only, also measure after setting the two "
        "accessibility attributes Loki's own path sets (the only write)",
    )
    parser.add_argument(
        "--apps",
        nargs="+",
        metavar="NAME",
        help="subset of the built-in allowlist (cannot add apps)",
    )
    parser.add_argument(
        "--out", type=Path, default=Path("../research/data/ax-probe.csv")
    )
    args = parser.parse_args()

    apps = list(ALLOWLIST)
    if args.apps:
        unknown = [a for a in args.apps if a not in ALLOWLIST]
        if unknown:
            parser.error(f"not in the allowlist: {', '.join(unknown)}")
        apps = args.apps

    if not args.enable_probe:
        print_plan(apps)
        return 0

    if not AXIsProcessTrusted():
        print(
            "Accessibility permission is not granted to this process. Nothing was launched.\n"
            "Enable it for your terminal in System Settings > Privacy & Security > Accessibility.",
            file=sys.stderr,
        )
        return 3

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for name in apps:
        print(f"probing {name} ...", flush=True)
        rows.extend(probe_app(name, args.match_loki_ax_switches))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    meta = {
        "started_utc": started,
        "macos": platform.mac_ver()[0],
        "apps_requested": apps,
        "apps_probed": sorted({r["app"] for r in rows if r["status"] == "probed"}),
        "apps_skipped": {
            r["app"]: r["reason"] for r in rows if r["status"] != "probed"
        },
        "attributes_read": ATTRIBUTES_READ,
        "never_read_or_stored": NEVER_READ,
        "limits": {
            "max_nodes": MAX_NODES,
            "max_depth": MAX_DEPTH,
            "ax_timeout_s": AX_TIMEOUT_S,
        },
        "switches_set": bool(args.match_loki_ax_switches),
        "switch_attributes": list(LOKI_AX_SWITCHES)
        if args.match_loki_ax_switches
        else [],
        "switch_eligible_apps": sorted(SWITCH_ELIGIBLE)
        if args.match_loki_ax_switches
        else [],
    }
    args.out.with_suffix(".json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    print(f"wrote {args.out} and {args.out.with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
