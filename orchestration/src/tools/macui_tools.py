import re
import subprocess
from collections.abc import Iterable
from typing import Any

from langchain_core.tools import BaseTool
from src.memory import SessionMemory
from src.tools.utils.ax_locator import (
    AXNode,
    _ax_attr,
    _ax_attrs_batch,
    activate_app,
    build_locator_tree,
    find_app_pid,
    find_by_locator,
    find_by_text,
    get_all_locators,
    get_cached_ax_node,
)

_memory = SessionMemory()

_MOD_MAP = {
    0: "Cmd",
    1: "Cmd+Shift",
    2: "Cmd+Option",
    3: "Cmd+Option+Shift",
    4: "Ctrl+Cmd",
    8: "Fn",
    24: "Cmd+Ctrl+Option",
    28: "Cmd+Ctrl+Option+Fn",
}

_CLICKABLE_ROLES: frozenset[str] = frozenset(
    {
        "AXButton",
        "AXCheckBox",
        "AXComboBox",
        "AXDisclosureTriangle",
        "AXIncrementor",
        "AXLink",
        "AXMenuBarItem",
        "AXMenuItem",
        "AXPopUpButton",
        "AXRadioButton",
        "AXSlider",
        "AXTab",
        "AXTextField",
        "AXTextArea",
        "AXToolbarButton",
    }
)

_TEXT_INPUT_ROLES: frozenset[str] = frozenset(
    {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"}
)


def _resolve_ax_node(
    target_str: str,
    preferred_roles: Iterable[str] = _CLICKABLE_ROLES,
    *,
    use_cache: bool = True,
) -> tuple[AXNode | None, str | None]:
    if use_cache and "/" in target_str and "[" in target_str:
        cached = get_cached_ax_node(target_str)
        if cached is not None:
            return cached, None

    target_pid = None
    m_app = re.match(r"^AXApplication\[([^\]]+)\]", target_str)
    if m_app:
        target_pid = find_app_pid(m_app.group(1).strip())

    tree = build_locator_tree(pid=target_pid)
    if tree is None:
        return None, "Error: could not build accessibility tree."

    node = None
    if "/" in target_str and "[" in target_str:
        node = find_by_locator(tree, target_str)

    if node is None:
        role_set = set(preferred_roles)
        results = find_by_text(tree, target_str)
        preferred = [n for n in results if n.role in role_set]
        node = preferred[0] if preferred else (results[0] if results else None)

    if node is None:
        return None, f"Error: no element found for '{target_str}'."
    return node, None


class FindMacApplicationsTool(BaseTool):
    name: str = "FindMacApplicationsTool"
    description: str = "A tool for finding applications on a Mac system."

    def _run(self, input: str = "") -> str:
        from src.graph.app_knowledge import AppKnowledge

        return "\n".join(AppKnowledge().scan_and_save())


class OpenMacApplicationTool(BaseTool):
    name: str = "OpenMacApplicationTool"
    description: str = "A tool for opening applications on a Mac system."
    installed_apps: list[str] | None = None

    def _run(self, input: str) -> str:
        from src.graph.app_knowledge import AppKnowledge

        exact_app_name, err = AppKnowledge().resolve_app(
            input, custom_apps=self.installed_apps or []
        )
        if not exact_app_name:
            return f"Error opening {input}: {err or 'application not allowed'}"

        try:
            subprocess.run(
                ["open", "-a", exact_app_name],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as e:
            err_msg = (e.stderr or b"").decode("utf-8", errors="replace")
            return f"Error opening {exact_app_name}: {err_msg or 'unknown error'}"

        activate_app(exact_app_name)
        result = f"Opened {exact_app_name} successfully."
        _memory.record_action(
            "native", "OpenMacApplicationTool", exact_app_name, result
        )
        return result


class GetAppShortcutsTool(BaseTool):
    name: str = "GetAppShortcutsTool"
    description: str = (
        "Get keyboard shortcuts for the frontmost (currently focused) macOS app. "
        "Returns a list like 'Search -> Cmd+L', 'New Tab -> Cmd+T'. "
        "Use this BEFORE clicking — if a shortcut exists for the action you need, "
        "use KeyComboTool instead of clicking.  Call with no arguments."
    )

    _APPLESCRIPT: str = """
tell application "System Events"
    set frontApp to name of first application process whose frontmost is true
    tell process frontApp
        set outputList to ""
        repeat with topMenu in menu bar items of menu bar 1
            if (count of menus of topMenu) > 0 then
                set currentMenu to menu 1 of topMenu
                repeat with currentItem in menu items of currentMenu
                    try
                        set cmdChar to value of attribute "AXMenuItemCmdChar" of currentItem
                        set cmdMods to value of attribute "AXMenuItemCmdModifiers" of currentItem
                        set itemName to name of currentItem
                        if cmdChar is not missing value and cmdChar is not "" then
                            set outputList to outputList & itemName & " -> " & cmdChar & " (Mods: " & cmdMods & ")" & linefeed
                        end if
                    end try
                end repeat
            end if
        end repeat
        return frontApp & linefeed & outputList
    end tell
end tell
"""

    def _extract_via_mach_ipc(self, target_app: str = "") -> tuple[str, list[str]] | None:
        try:
            import ApplicationServices as _AS
            import Cocoa as _Cocoa

            ws = getattr(_Cocoa, "NSWorkspace").sharedWorkspace()
            pid = find_app_pid(target_app) if target_app else None
            app_label = target_app.strip()
            if pid is None:
                front = ws.frontmostApplication()
                if front is None:
                    return None
                pid = int(front.processIdentifier())
                app_label = str(front.localizedName() or "Unknown")

            ax_app = getattr(_AS, "AXUIElementCreateApplication")(pid)
            if ax_app is None:
                return None

            menu_bar = _ax_attr(ax_app, "AXMenuBar")
            if menu_bar is None:
                return None

            top_items = _ax_attr(menu_bar, "AXChildren") or []
            shortcuts: list[str] = []
            item_attrs = ("AXTitle", "AXMenuItemCmdChar", "AXMenuItemCmdModifiers")

            for top_item in top_items:
                menus = _ax_attr(top_item, "AXChildren") or []
                for menu in menus:
                    menu_items = _ax_attr(menu, "AXChildren") or []
                    for mi in menu_items:
                        vals = _ax_attrs_batch(mi, item_attrs)
                        cmd_char = vals.get("AXMenuItemCmdChar")
                        title = vals.get("AXTitle")
                        if not cmd_char or not title or not isinstance(cmd_char, str):
                            continue
                        mods_raw = vals.get("AXMenuItemCmdModifiers")
                        mod_int = int(mods_raw) if isinstance(mods_raw, (int, float)) else 0
                        mod_label = _MOD_MAP.get(mod_int, f"Mods({mod_int})")
                        shortcuts.append(f"{str(title).strip()} -> {mod_label}+{cmd_char.strip()}")

            return app_label, shortcuts
        except Exception:
            return None

    def _run(self, input: str = "", use_mach_ipc: bool = True) -> str:
        if use_mach_ipc:
            ipc_res = self._extract_via_mach_ipc((input or "").strip())
            if ipc_res is not None:
                app_name, shortcuts = ipc_res
                if shortcuts:
                    header = f"Shortcuts for {app_name} ({len(shortcuts)} found):\n"
                    return header + "\n".join(shortcuts)

        try:
            result = subprocess.run(
                ["osascript", "-e", self._APPLESCRIPT],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as exc:
            return f"Error running AppleScript: {exc}"

        if result.returncode != 0:
            return f"Error getting shortcuts: {result.stderr.strip()}"

        raw = result.stdout.strip()
        if not raw:
            return "No shortcuts found for the frontmost app."

        lines = raw.split("\n")
        app_name = lines[0].strip() if lines else "Unknown"
        shortcuts = []
        for line in lines[1:]:
            line = line.strip()
            if not line or " -> " not in line:
                continue
            name_part, rest = line.split(" -> ", 1)
            if "(Mods:" in rest:
                char_part = rest.split("(Mods:")[0].strip()
                mod_str = rest.split("(Mods:")[1].rstrip(")").strip()
                try:
                    mod_int = int(mod_str)
                except ValueError:
                    mod_int = 0
                mod_label = _MOD_MAP.get(mod_int, f"Mods({mod_int})")
                shortcuts.append(f"{name_part.strip()} -> {mod_label}+{char_part}")
            else:
                shortcuts.append(f"{name_part.strip()} -> {rest}")

        header = f"Shortcuts for {app_name} ({len(shortcuts)} found):\n"
        return header + "\n".join(shortcuts)


class AXLocatorSnapshotTool(BaseTool):
    name: str = "AXLocatorSnapshotTool"
    description: str = (
        "Take a snapshot of the frontmost app's UI using the macOS Accessibility "
        "tree.  Returns every visible element with a stable locator path, role, "
        "label, position, and interactability status.  Locators are deterministic "
        "and can be used with AXLocatorClickTool to click elements reliably "
        "without OCR.  Call with no arguments, or pass a PID to target a "
        "specific app."
    )

    def _run(self, input: Any = "") -> str:
        pid = int(str(input).strip()) if input and str(input).strip().isdigit() else None
        tree = build_locator_tree(pid=pid)
        if tree is None:
            return (
                "Error: could not build accessibility tree. Check that:\n"
                "  - Running on macOS\n"
                "  - Terminal has Accessibility permission\n"
                "  - An app is in the foreground"
            )

        elements = get_all_locators(tree)
        labeled = [e for e in elements if e.get("label")]

        lines = [
            f"App: {tree.title or tree.description or 'Unknown'}",
            f"Total elements: {len(elements)} | Labeled: {len(labeled)}",
            "",
        ]

        for el in labeled[:60]:
            loc = el["locator"]
            label = el.get("label", "")[:50]
            role = el["role"]
            cx, cy = el.get("center_x", "?"), el.get("center_y", "?")
            inter = " [clickable]" if el.get("interactable") else ""
            lines.append(f"  {loc}")
            lines.append(f'    "{label}" ({role}) at ({cx},{cy}){inter}')

        if len(labeled) > 60:
            lines.append(f"  ... and {len(labeled) - 60} more labeled elements")

        return "\n".join(lines)


class AXLocatorFindTool(BaseTool):
    name: str = "AXLocatorFindTool"
    description: str = (
        "Find UI elements by text label in the frontmost app's accessibility "
        "tree.  Returns matching elements with their stable locator paths and "
        "screen coordinates.  Pass the text to search for as a plain string.  "
        "Matching is case-insensitive with substring support."
    )

    def _run(self, input: Any = "") -> str:
        query = str(input).strip() if input else ""
        if not query:
            return "Error: pass a text label to search for."

        tree = build_locator_tree()
        if tree is None:
            return "Error: could not build accessibility tree."

        results = find_by_text(tree, query)
        if not results:
            return f"No elements found matching '{query}'."

        lines = [f"Found {len(results)} element(s) matching '{query}':"]
        for node in results[:15]:
            inter = " [clickable]" if node.role in _CLICKABLE_ROLES else ""
            center = node.center
            pos = f"({center[0]},{center[1]})" if center else "(no position)"
            lines.append(f"  {node.locator_path}")
            lines.append(f'    "{node.label}" ({node.role}) at {pos}{inter}')

        if len(results) > 15:
            lines.append(f"  ... and {len(results) - 15} more")

        return "\n".join(lines)


class AXLocatorClickTool(BaseTool):
    name: str = "AXLocatorClickTool"
    description: str = (
        "Click a UI element using its stable locator path from AXLocatorSnapshotTool "
        "or AXLocatorFindTool.  Pass the full locator path (e.g. "
        "'AXApplication[Finder]/AXMenuBar[_NS:1088]/AXMenuBarItem[File]') or "
        "just a text label to search and click.  Performs the AX 'Press' action "
        "when available, otherwise clicks the element's center coordinates."
    )

    def _run(self, input: Any = "") -> str:
        import pyautogui

        target_str = str(input).strip() if input else ""
        if not target_str:
            return "Error: pass a locator path or text label."

        if target_str.startswith("coord:"):
            try:
                coords_part = target_str[len("coord:") :].strip()
                cx_str, cy_str = coords_part.split(",", 1)
                cx, cy = int(float(cx_str)), int(float(cy_str))
                pyautogui.click(cx, cy)
                result = f"Clicked coordinate ({cx},{cy}) via visual fallback."
                _memory.record_action("native", "AXLocatorClickTool", target_str, result)
                return result
            except Exception as exc:
                return f"Error clicking coordinate '{target_str}': {exc}"

        node, err = _resolve_ax_node(target_str, _CLICKABLE_ROLES)
        if err or node is None:
            return err or f"Error: no element found for '{target_str}'."

        for attempt in range(2):
            if node._ax_ref is not None:
                try:
                    import ApplicationServices as _AS

                    AXUIElementPerformAction = getattr(_AS, "AXUIElementPerformAction")
                    if AXUIElementPerformAction(node._ax_ref, "AXPress") == 0:
                        result = (
                            f"Pressed '{node.label}' via AXPress action. "
                            f"Locator: {node.locator_path}"
                        )
                        _memory.record_action(
                            "native", "AXLocatorClickTool", node.label, result
                        )
                        return result
                except Exception:
                    pass
            if attempt == 0 and "/" in target_str and "[" in target_str:
                fresh_node, _ = _resolve_ax_node(target_str, _CLICKABLE_ROLES, use_cache=False)
                if fresh_node is not None:
                    node = fresh_node
                    continue
            break

        center = node.center
        if center is None:
            return f"Error: element '{node.label}' has no screen position."

        pyautogui.click(center[0], center[1])
        result = (
            f"Clicked '{node.label}' at ({center[0]},{center[1]}). "
            f"Locator: {node.locator_path}"
        )
        _memory.record_action("native", "AXLocatorClickTool", node.label, result)
        return result


class AXLocatorSetValueTool(BaseTool):
    name: str = "AXLocatorSetValueTool"
    description: str = (
        "Directly set the text value (AXValue) and focus (AXFocused) of a macOS "
        "Accessibility element using its locator path or label. Much faster and "
        "more reliable than simulated keystrokes. Pass 'locator_or_label ||| text'."
    )

    def _run(self, input: Any = "") -> str:
        raw = str(input) if input else ""
        if "|||" not in raw:
            return "Error: expected 'locator ||| text' format."
        target_str, text = raw.split("|||", 1)
        target_str = target_str.strip()

        if not target_str or target_str.startswith("coord:"):
            return "Error: AXValue requires an AX element locator or label."

        node, err = _resolve_ax_node(target_str, _TEXT_INPUT_ROLES)
        if err or node is None or node._ax_ref is None:
            return err or f"Error: no element found for '{target_str}'."

        try:
            import ApplicationServices as _AS

            AXUIElementSetAttributeValue = getattr(_AS, "AXUIElementSetAttributeValue")
            AXUIElementSetAttributeValue(node._ax_ref, "AXFocused", True)
            code = AXUIElementSetAttributeValue(node._ax_ref, "AXValue", text)
            if code != 0 and "/" in target_str and "[" in target_str:
                fresh_node, _ = _resolve_ax_node(target_str, _TEXT_INPUT_ROLES, use_cache=False)
                if fresh_node is not None and fresh_node._ax_ref is not None:
                    node = fresh_node
                    AXUIElementSetAttributeValue(node._ax_ref, "AXFocused", True)
                    code = AXUIElementSetAttributeValue(node._ax_ref, "AXValue", text)
            if code == 0:
                result = (
                    f"Set AXValue on '{node.label or node.role}' ({len(text)} chars). "
                    f"Locator: {node.locator_path}"
                )
                _memory.record_action("native", "AXLocatorSetValueTool", node.locator_path, result)
                return result
            return f"Error: AXUIElementSetAttributeValue returned code {code}"
        except Exception as exc:
            return f"Error setting AXValue: {exc}"
