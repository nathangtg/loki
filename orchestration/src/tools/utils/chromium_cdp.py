from __future__ import annotations

import logging
import platform
import re
import subprocess
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


def get_chromium_app_info() -> dict[str, Any] | None:
    if platform.system() != "Darwin":
        return None

    try:
        from AppKit import NSWorkspace
    except ImportError:
        return None

    ws = NSWorkspace.sharedWorkspace()
    app = ws.frontmostApplication()
    if app is None:
        return None

    pid = app.processIdentifier()
    name = app.localizedName() or ""
    bundle_url = app.bundleURL()
    if bundle_url is None:
        return None

    bundle_path = bundle_url.path()

    framework = _detect_chromium_framework(bundle_path)
    if framework is None:
        return None

    cdp_port = _find_cdp_port(pid, name, bundle_path)

    return {
        "name": name,
        "pid": pid,
        "bundle_path": bundle_path,
        "framework": framework,
        "cdp_port": cdp_port,
    }


def get_cdp_ax_elements(port: int) -> list[dict[str, Any]]:
    cdp_url = f"http://127.0.0.1:{port}"

    if not _is_port_open(port):
        LOGGER.debug("CDP port %d not responding", port)
        return []

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        LOGGER.debug("Playwright not available for CDP connection")
        return []

    elements: list[dict[str, Any]] = []

    try:
        pw = sync_playwright().start()
        try:
            browser = pw.chromium.connect_over_cdp(cdp_url)
            contexts = browser.contexts
            if not contexts:
                LOGGER.debug("No browser contexts found on port %d", port)
                return []

            page = contexts[0].pages[0] if contexts[0].pages else None
            if page is None:
                LOGGER.debug("No pages found in context on port %d", port)
                return []

            elements = _extract_elements_via_cdp(page)
            browser.close()
        finally:
            pw.stop()
    except Exception as exc:
        LOGGER.info("CDP connection to port %d failed: %s", port, exc)

    return elements


def _detect_chromium_framework(bundle_path: str) -> str | None:
    frameworks_dir = Path(bundle_path) / "Contents" / "Frameworks"
    if not frameworks_dir.exists():
        return None

    try:
        entries = [e.name.lower() for e in frameworks_dir.iterdir()]
    except OSError:
        return None

    for entry in entries:
        if "electron" in entry:
            return "electron"
        if "chromium embedded" in entry or "cef" in entry:
            return "cef"
        if "nwjs" in entry:
            return "cef"

    return None


def _find_cdp_port(pid: int, app_name: str, bundle_path: str) -> int | None:
    port = _port_from_cmdline(pid)
    if port:
        return port

    port = _port_from_devtools_file(app_name, bundle_path)
    if port:
        return port

    return None


def _port_from_cmdline(pid: int) -> int | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0:
            return None

        match = re.search(r"--remote-debugging-port=(\d+)", result.stdout)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return None


def _port_from_devtools_file(app_name: str, bundle_path: str) -> int | None:
    home = Path.home()
    app_support = home / "Library" / "Application Support"

    candidates = [
        app_support / app_name / "DevToolsActivePort",
        app_support / app_name.replace(" ", "") / "DevToolsActivePort",
    ]

    bundle_id = _get_bundle_identifier(bundle_path)
    if bundle_id:
        candidates.append(app_support / bundle_id / "DevToolsActivePort")

    if "code" in app_name.lower():
        candidates.append(app_support / "Code" / "DevToolsActivePort")

    for path in candidates:
        if path.exists():
            try:
                content = path.read_text().strip()
                port_str = content.split("\n")[0].strip()
                port = int(port_str)
                if 1024 <= port <= 65535:
                    LOGGER.info("Found DevToolsActivePort: %s → port %d", path, port)
                    return port
            except (ValueError, OSError):
                continue

    return None


def _get_bundle_identifier(bundle_path: str) -> str | None:
    plist_path = Path(bundle_path) / "Contents" / "Info.plist"
    if not plist_path.exists():
        return None
    try:
        import plistlib

        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)
        return plist.get("CFBundleIdentifier")
    except Exception:
        return None


def _is_port_open(port: int) -> bool:
    import urllib.request

    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=2
        )
        return True
    except Exception:
        return False


def _extract_elements_via_cdp(page) -> list[dict[str, Any]]:
    client = None
    methods = [
        ("context.new_cdp_session", lambda: page.context.new_cdp_session(page)),
        (
            "browser.new_browser_cdp_session",
            lambda: page.context.browser.new_browser_cdp_session(),
        ),
    ]
    for method_name, factory in methods:
        try:
            client = factory()
            break
        except Exception as exc:
            LOGGER.debug("CDP session via %s failed: %s", method_name, exc)

    if client is None:
        return []

    try:
        result = client.send("Accessibility.getFullAXTree", {})
        nodes = result.get("nodes", [])
        if not nodes:
            return []

        dom_nodes = _get_dom_boxes(client)

        elements = _ax_nodes_to_elements(nodes, dom_nodes)
        LOGGER.info(
            "CDP AX extraction: %d nodes → %d elements", len(nodes), len(elements)
        )
        return elements
    except Exception as exc:
        LOGGER.debug("CDP AX extraction failed: %s", exc)
        return []
    finally:
        try:
            client.detach()
        except Exception:
            pass


def _get_dom_boxes(client) -> dict[int, dict]:
    boxes: dict[int, dict] = {}
    try:
        doc = client.send("DOM.getDocument", {"depth": 0})
        root_id = doc.get("root", {}).get("nodeId", 0)
        if not root_id:
            return boxes

        flat = client.send(
            "DOM.getFlattenedDocument", {"depth": -1, "pierce": True}
        )
        for node in flat.get("nodes", []):
            backend_id = node.get("backendNodeId", 0)
            if backend_id:
                boxes[backend_id] = node
    except Exception:
        pass
    return boxes


def _ax_nodes_to_elements(
    nodes: list[dict], dom_nodes: dict[int, dict]
) -> list[dict[str, Any]]:

    skip_roles = {
        "none",
        "generic",
        "GenericContainer",
        "InlineTextBox",
        "LineBreak",
        "ignored",
        "presentation",
        "group",
        "paragraph",
        "Section",
    }

    elements: list[dict[str, Any]] = []
    seen_labels: set[str] = set()

    for node in nodes:
        if node.get("ignored", False):
            continue

        role = node.get("role", {}).get("value", "")
        name = (node.get("name", {}).get("value", "") or "").strip()

        if not name:
            continue
        if role in skip_roles:
            continue

        backend_node_id = node.get("backendDOMNodeId")
        bbox = None
        if backend_node_id and backend_node_id in dom_nodes:
            dom_node = dom_nodes[backend_node_id]
            bbox = dom_node.get("boundingBox")

        dedup_key = f"{name}:{role}"
        if dedup_key in seen_labels:
            continue
        seen_labels.add(dedup_key)

        el: dict[str, Any] = {
            "text": name[:120],
            "role": f"AX{role.capitalize()}" if role else "",
            "role_desc": role,
            "source": "cdp_accessibility",
            "confidence": 1.0,
        }

        if bbox:
            x = int(bbox.get("x", 0))
            y = int(bbox.get("y", 0))
            w = int(bbox.get("width", 0))
            h = int(bbox.get("height", 0))
            if w > 0 and h > 0:
                el.update(
                    {
                        "center_x": x + w // 2,
                        "center_y": y + h // 2,
                        "box_x": x,
                        "box_y": y,
                        "width": w,
                        "height": h,
                    }
                )

        elements.append(el)

    return elements


def get_osascript_elements(app_name: str) -> list[dict[str, Any]]:
    script = _build_ui_extraction_script(app_name)

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        LOGGER.debug("osascript timed out for %s", app_name)
        return []
    except Exception as exc:
        LOGGER.debug("osascript failed for %s: %s", app_name, exc)
        return []

    if result.returncode != 0:
        LOGGER.debug("osascript error for %s: %s", app_name, result.stderr.strip())
        return []

    return _parse_osascript_output(result.stdout)


def _build_ui_extraction_script(app_name: str) -> str:
    return f'''
tell application "System Events"
    tell process "{app_name}"
        set frontmost to true
        set outputLines to ""

        -- Get all windows
        repeat with w in windows
            set winName to name of w
            set winPos to position of w
            set winSize to size of w

            -- Walk top-level UI elements in each window (depth 1-2)
            repeat with uiEl in UI elements of w
                try
                    set elRole to role of uiEl
                    set elDesc to description of uiEl
                    set elTitle to ""
                    try
                        set elTitle to title of uiEl
                    end try
                    if elTitle is "" then set elTitle to elDesc
                    try
                        set elTitle to value of attribute "AXTitle" of uiEl
                    end try

                    set elPos to position of uiEl
                    set elSize to size of uiEl
                    set posX to item 1 of elPos
                    set posY to item 2 of elPos
                    set sW to item 1 of elSize
                    set sH to item 2 of elSize

                    if elTitle is not "" and sW > 0 and sH > 0 then
                        set outputLines to outputLines & elRole & "|||" & elTitle & "|||" & posX & "|||" & posY & "|||" & sW & "|||" & sH & linefeed
                    end if

                    -- Depth 2: children of this element
                    repeat with childEl in UI elements of uiEl
                        try
                            set cRole to role of childEl
                            set cTitle to ""
                            try
                                set cTitle to title of childEl
                            end try
                            if cTitle is "" then
                                try
                                    set cTitle to description of childEl
                                end try
                            end if
                            if cTitle is "" then
                                try
                                    set cTitle to value of attribute "AXTitle" of childEl
                                end try
                            end if

                            set cPos to position of childEl
                            set cSize to size of childEl
                            set cX to item 1 of cPos
                            set cY to item 2 of cPos
                            set cW to item 1 of cSize
                            set cH to item 2 of cSize

                            if cTitle is not "" and cW > 0 and cH > 0 then
                                set outputLines to outputLines & cRole & "|||" & cTitle & "|||" & cX & "|||" & cY & "|||" & cW & "|||" & cH & linefeed
                            end if
                        end try
                    end repeat
                end try
            end repeat
        end repeat

        return outputLines
    end tell
end tell
'''


def _parse_osascript_output(raw: str) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    seen: set[str] = set()

    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line or "|||" not in line:
            continue

        parts = line.split("|||")
        if len(parts) < 6:
            continue

        role, title, x_str, y_str, w_str, h_str = parts[:6]
        title = title.strip()
        if not title:
            continue

        key = f"{title}:{role}"
        if key in seen:
            continue
        seen.add(key)

        try:
            x = int(float(x_str))
            y = int(float(y_str))
            w = int(float(w_str))
            h = int(float(h_str))
        except (ValueError, TypeError):
            continue

        if w <= 0 or h <= 0:
            continue

        elements.append(
            {
                "text": title[:120],
                "role": role,
                "role_desc": role.removeprefix("AX").lower(),
                "center_x": x + w // 2,
                "center_y": y + h // 2,
                "box_x": x,
                "box_y": y,
                "width": w,
                "height": h,
                "source": "osascript_accessibility",
                "confidence": 0.9,
            }
        )

    LOGGER.info("osascript extraction: %d elements", len(elements))
    return elements
