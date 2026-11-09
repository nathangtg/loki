from __future__ import annotations

import platform
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AXNode:
    role: str
    title: str
    description: str
    value: str
    identifier: str
    role_description: str
    position: tuple[int, int] | None
    size: tuple[int, int] | None
    subrole: str
    enabled: bool
    focused: bool

    locator_segment: str = ""
    locator_path: str = ""

    children: list["AXNode"] = field(default_factory=list)
    parent: "AXNode | None" = field(default=None, repr=False)
    _ax_ref: Any = field(default=None, repr=False)

    @property
    def label(self) -> str:
        return self.title or self.description or self.value or ""

    @property
    def center(self) -> tuple[int, int] | None:
        if self.position and self.size:
            return (
                self.position[0] + self.size[0] // 2,
                self.position[1] + self.size[1] // 2,
            )
        return None

    @property
    def bounds(self) -> dict[str, int] | None:
        if self.position and self.size:
            return {
                "x": self.position[0],
                "y": self.position[1],
                "width": self.size[0],
                "height": self.size[1],
            }
        return None

    def container_label(self) -> str:
        curr = self.parent
        hops = 0
        while curr is not None and hops < 4:
            if curr.role in (
                "AXSheet",
                "AXToolbar",
                "AXTabGroup",
                "AXOutline",
                "AXTable",
                "AXGroup",
                "AXSplitGroup",
                "AXScrollArea",
            ):
                cand = (curr.title or curr.description or curr.identifier or "").strip()
                if cand and len(cand) <= 32 and not cand.startswith("_NS:"):
                    return cand
                if curr.role in ("AXToolbar", "AXSheet", "AXTabGroup"):
                    return curr.role.removeprefix("AX")
            curr = curr.parent
            hops += 1
        return ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "locator": self.locator_path,
            "role": self.role,
            "label": self.label,
        }
        container = self.container_label()
        if container:
            result["container"] = container
        if self.identifier:
            result["identifier"] = self.identifier
        if self.center:
            result["center_x"], result["center_y"] = self.center
        if self.bounds:
            result.update(self.bounds)
        result["enabled"] = self.enabled
        result["interactable"] = self.role in _INTERACTABLE_ROLES
        return result


_INTERACTABLE_ROLES: frozenset[str] = frozenset(
    {
        "AXButton",
        "AXCheckBox",
        "AXComboBox",
        "AXDisclosureTriangle",
        "AXImage",
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

_NODE_ATTR_NAMES: tuple[str, ...] = (
    "AXRole",
    "AXTitle",
    "AXDescription",
    "AXValue",
    "AXIdentifier",
    "AXRoleDescription",
    "AXSubrole",
    "AXEnabled",
    "AXFocused",
    "AXPosition",
    "AXSize",
    "AXChildren",
)

_AS_CACHE: tuple[Any, Any, Any, Any] | None = None
_LAST_LOCATOR_CACHE: dict[str, Any] = {}
_MANUAL_AX_PIDS: set[int] = set()


def enable_manual_accessibility(pid: int, settle_s: float = 0.06) -> bool:
    if platform.system() != "Darwin" or not pid:
        return False
    try:
        import ApplicationServices as _AS

        ax_app = getattr(_AS, "AXUIElementCreateApplication")(int(pid))
        if ax_app is None:
            return False
        set_attr = getattr(_AS, "AXUIElementSetAttributeValue")
        rc_man = set_attr(ax_app, "AXManualAccessibility", True)
        set_attr(ax_app, "AXEnhancedUserInterface", True)
        if rc_man == 0 and int(pid) not in _MANUAL_AX_PIDS:
            _MANUAL_AX_PIDS.add(int(pid))
            if settle_s > 0:
                time.sleep(settle_s)
            return True
        _MANUAL_AX_PIDS.add(int(pid))
        return rc_man == 0
    except Exception:
        return False


def cache_locator_tree(root: AXNode, pid: int | None = None) -> None:
    by_locator: dict[str, AXNode] = {}

    def _index(node: AXNode) -> None:
        if node.locator_path:
            by_locator[node.locator_path] = node
        for child in node.children:
            _index(child)

    _index(root)
    _LAST_LOCATOR_CACHE["pid"] = pid
    _LAST_LOCATOR_CACHE["timestamp"] = time.monotonic()
    _LAST_LOCATOR_CACHE["tree"] = root
    _LAST_LOCATOR_CACHE["by_locator"] = by_locator


def get_cached_ax_node(locator_path: str, max_age_s: float = 5.0) -> AXNode | None:
    if not locator_path or "/" not in locator_path:
        return None
    ts = _LAST_LOCATOR_CACHE.get("timestamp", 0.0)
    if (time.monotonic() - ts) > max_age_s:
        return None
    by_locator = _LAST_LOCATOR_CACHE.get("by_locator")
    if not isinstance(by_locator, dict):
        return None
    return by_locator.get(locator_path)


def clear_locator_cache() -> None:
    _LAST_LOCATOR_CACHE.clear()


def wait_for_safari_ready(timeout: float = 1.5, poll_interval: float = 0.08) -> bool:
    if platform.system() != "Darwin" or timeout <= 0:
        return False
    script = (
        'tell application "Safari"\n'
        '    if (count of documents) is 0 then return "empty"\n'
        '    try\n'
        '        return (do JavaScript "document.readyState" in front document)\n'
        '    on error\n'
        '        if (text of front document) is not "" then return "complete"\n'
        '        return "loading"\n'
        '    end try\n'
        'end tell'
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            res = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=0.5,
            )
            state = (res.stdout or "").strip().lower()
            if state in ("complete", "interactive"):
                return True
        except Exception:
            pass
        time.sleep(poll_interval)
    return False


def _get_as_funcs() -> tuple[Any, Any, Any, Any] | None:
    global _AS_CACHE
    if _AS_CACHE is not None:
        return _AS_CACHE
    try:
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCopyMultipleAttributeValues,
            AXValueGetType,
            kAXValueTypeAXError,
        )

        _AS_CACHE = (
            AXUIElementCopyAttributeValue,
            AXUIElementCopyMultipleAttributeValues,
            AXValueGetType,
            kAXValueTypeAXError,
        )
        return _AS_CACHE
    except Exception:
        return None


def find_app_pid(app_name: str) -> int | None:
    if not app_name or platform.system() != "Darwin":
        return None
    target_lower = app_name.strip().lower()
    try:
        import Cocoa

        ws = getattr(Cocoa, "NSWorkspace").sharedWorkspace()
        front = ws.frontmostApplication()
        if front and (front.localizedName() or "").lower() == target_lower:
            return int(front.processIdentifier())
        for app in ws.runningApplications():
            if (app.localizedName() or "").lower() == target_lower:
                return int(app.processIdentifier())
    except Exception:
        pass
    return None


def activate_app(app_name: str, wait_timeout: float = 0.0) -> int | None:
    if not app_name or platform.system() != "Darwin":
        return None
    target_lower = app_name.strip().lower()
    try:
        import Cocoa

        ws = getattr(Cocoa, "NSWorkspace").sharedWorkspace()
        front = ws.frontmostApplication()
        if front and (front.localizedName() or "").lower() == target_lower:
            return int(front.processIdentifier())
    except Exception:
        ws = None

    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                "on run argv",
                "-e",
                "if (count of argv) > 0 then tell application (item 1 of argv) to activate",
                "-e",
                "end run",
                "--",
                app_name,
            ],
            capture_output=True,
            timeout=3,
        )
    except Exception:
        pass

    if ws is None:
        return None

    if wait_timeout <= 0:
        return find_app_pid(app_name)

    deadline = time.monotonic() + wait_timeout
    target_pid: int | None = None
    while time.monotonic() < deadline:
        try:
            for app in ws.runningApplications():
                if (app.localizedName() or "").lower() == target_lower:
                    target_pid = int(app.processIdentifier())
                    if app.isActive():
                        return target_pid
        except Exception:
            break
        time.sleep(0.05)
    return target_pid


def build_locator_tree(
    pid: int | None = None,
    max_depth: int = 22,
    use_batch_ipc: bool = True,
    prune_menus: bool = True,
) -> AXNode | None:
    if platform.system() != "Darwin":
        return None

    try:
        import ApplicationServices as _AS
        import Cocoa as _Cocoa

        AXUIElementCreateApplication = getattr(_AS, "AXUIElementCreateApplication")
        NSWorkspace = getattr(_Cocoa, "NSWorkspace")
    except (ImportError, AttributeError):
        return None

    if pid is None:
        try:
            ws = NSWorkspace.sharedWorkspace()
            app = ws.frontmostApplication()
            pid = app.processIdentifier()
        except Exception:
            return None

    ax_app = AXUIElementCreateApplication(pid)
    if ax_app is None:
        return None

    if pid is not None and int(pid) not in _MANUAL_AX_PIDS:
        enable_manual_accessibility(int(pid))

    root = _build_node(
        ax_app,
        depth=0,
        max_depth=max_depth,
        use_batch_ipc=use_batch_ipc,
        prune_menus=prune_menus,
    )
    if root is None:
        return None

    _assign_locators(root, parent_path="")
    cache_locator_tree(root, pid=pid)
    return root


def _ax_attr(element: Any, attr: str) -> Any:
    funcs = _get_as_funcs()
    if funcs is None:
        return None
    try:
        err, value = funcs[0](element, attr, None)
        if err == 0:
            return value
    except Exception:
        pass
    return None


def _ax_attrs_batch(element: Any, attrs: tuple[str, ...] = _NODE_ATTR_NAMES) -> dict[str, Any]:
    funcs = _get_as_funcs()
    if funcs is not None:
        _, copy_multi, get_type, err_type = funcs
        try:
            err, values = copy_multi(element, attrs, 0, None)
            if err == 0 and values is not None and len(values) == len(attrs):
                out: dict[str, Any] = {}
                for name, val in zip(attrs, values):
                    if val is None or isinstance(val, (str, int, float, bool, list, tuple)):
                        out[name] = val
                        continue
                    if name in ("AXPosition", "AXSize"):
                        out[name] = None if get_type(val) == err_type else val
                    elif name == "AXChildren":
                        out[name] = val if hasattr(val, "__iter__") else None
                    else:
                        out[name] = None if get_type(val) == err_type else val
                return out
        except Exception:
            pass

    return {name: _ax_attr(element, name) for name in attrs}


def _parse_pos_val(pos: Any) -> tuple[int, int] | None:
    if pos is None:
        return None
    try:
        return (int(pos.x), int(pos.y))
    except Exception:
        s = str(pos)
        m = re.search(r"x:([\d.-]+)\s+y:([\d.-]+)", s)
        if m:
            return (int(float(m.group(1))), int(float(m.group(2))))
    return None


def _parse_size_val(size: Any) -> tuple[int, int] | None:
    if size is None:
        return None
    try:
        w, h = int(size.width), int(size.height)
        return (w, h) if w > 0 and h > 0 else None
    except Exception:
        s = str(size)
        m = re.search(r"w:([\d.]+)\s+h:([\d.]+)", s)
        if m:
            w, h = int(float(m.group(1))), int(float(m.group(2)))
            return (w, h) if w > 0 and h > 0 else None
    return None


def _extract_pos(element: Any) -> tuple[int, int] | None:
    return _parse_pos_val(_ax_attr(element, "AXPosition"))


def _extract_size(element: Any) -> tuple[int, int] | None:
    return _parse_size_val(_ax_attr(element, "AXSize"))


def _build_node(
    element: Any,
    depth: int,
    max_depth: int,
    use_batch_ipc: bool = True,
    prune_menus: bool = True,
) -> AXNode | None:
    if depth > max_depth:
        return None

    if use_batch_ipc:
        attrs = _ax_attrs_batch(element)
        role = attrs.get("AXRole") or ""
        if not role:
            return None
        title = attrs.get("AXTitle") or ""
        desc = attrs.get("AXDescription") or ""
        value = attrs.get("AXValue")
        identifier = attrs.get("AXIdentifier") or ""
        role_desc = attrs.get("AXRoleDescription") or ""
        subrole = attrs.get("AXSubrole") or ""
        enabled = attrs.get("AXEnabled")
        focused = attrs.get("AXFocused")
        position = _parse_pos_val(attrs.get("AXPosition"))
        size = _parse_size_val(attrs.get("AXSize"))
        children_raw = attrs.get("AXChildren")
    else:
        role = _ax_attr(element, "AXRole") or ""
        if not role:
            return None
        title = _ax_attr(element, "AXTitle") or ""
        desc = _ax_attr(element, "AXDescription") or ""
        value = _ax_attr(element, "AXValue")
        identifier = _ax_attr(element, "AXIdentifier") or ""
        role_desc = _ax_attr(element, "AXRoleDescription") or ""
        subrole = _ax_attr(element, "AXSubrole") or ""
        enabled = _ax_attr(element, "AXEnabled")
        focused = _ax_attr(element, "AXFocused")
        position = _extract_pos(element)
        size = _extract_size(element)
        children_raw = _ax_attr(element, "AXChildren")

    value_str = str(value) if isinstance(value, str) else ""
    role_str = role if isinstance(role, str) else str(role)
    is_focused = bool(focused) if focused is not None else False

    node = AXNode(
        role=role_str,
        title=title if isinstance(title, str) else "",
        description=desc if isinstance(desc, str) else "",
        value=value_str,
        identifier=identifier if isinstance(identifier, str) else "",
        role_description=role_desc if isinstance(role_desc, str) else "",
        position=position,
        size=size,
        subrole=subrole if isinstance(subrole, str) else "",
        enabled=bool(enabled) if enabled is not None else True,
        focused=is_focused,
        _ax_ref=element,
    )

    if prune_menus and role_str in ("AXMenuBarItem", "AXMenu") and not is_focused:
        return node

    if children_raw:
        for child_ref in children_raw:
            child_node = _build_node(
                child_ref,
                depth + 1,
                max_depth,
                use_batch_ipc=use_batch_ipc,
                prune_menus=prune_menus,
            )
            if child_node is not None:
                child_node.parent = node
                node.children.append(child_node)

    return node


def _sanitize_segment(text: str) -> str:
    return text.replace("/", "|").replace("[", "(").replace("]", ")").strip()


def _assign_locators(node: AXNode, parent_path: str) -> None:
    if not parent_path:
        label = _sanitize_segment(node.title or node.description or "root")
        node.locator_segment = f"{node.role}[{label}]"
        node.locator_path = node.locator_segment

    role_groups: dict[str, list[AXNode]] = {}
    for child in node.children:
        role_groups.setdefault(child.role, []).append(child)

    for role, siblings in role_groups.items():
        candidates: list[str] = []
        for child in siblings:
            if child.identifier:
                candidates.append(child.identifier)
            elif child.title:
                candidates.append(child.title)
            elif child.description:
                candidates.append(child.description)
            else:
                candidates.append("")

        label_counts: dict[str, int] = {}
        for c in candidates:
            label_counts[c] = label_counts.get(c, 0) + 1

        index_counters: dict[str, int] = {}
        for i, child in enumerate(siblings):
            candidate = candidates[i]
            sanitized = _sanitize_segment(candidate)

            if candidate and label_counts[candidate] == 1:
                child.locator_segment = f"{role}[{sanitized}]"
            else:
                idx = index_counters.get(role, 0)
                index_counters[role] = idx + 1
                if sanitized:
                    child.locator_segment = f"{role}[{sanitized}#{idx}]"
                else:
                    child.locator_segment = f"{role}[{idx}]"

            child.locator_path = f"{node.locator_path}/{child.locator_segment}"

        for child in siblings:
            _assign_locators(child, child.locator_path)


def get_all_locators(root: AXNode) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    _collect_all(root, results)
    return results


def _collect_all(node: AXNode, results: list[dict[str, Any]]) -> None:
    if node.position and node.size:
        results.append(node.to_dict())
    for child in node.children:
        _collect_all(child, results)


def find_by_locator(root: AXNode, locator_path: str) -> AXNode | None:
    segments = locator_path.split("/")
    if not segments or root.locator_segment != segments[0]:
        return None
    if len(segments) == 1:
        return root

    current = root
    for seg in segments[1:]:
        found = next((c for c in current.children if c.locator_segment == seg), None)
        if found is None:
            return None
        current = found
    return current


def find_by_text(
    root: AXNode,
    text: str,
    *,
    role_filter: str | None = None,
    exact: bool = False,
) -> list[AXNode]:
    results: list[tuple[int, AXNode]] = []
    _search_text(root, text.lower() if not exact else text, role_filter, exact, results)
    results.sort(key=lambda t: t[0])
    return [node for _, node in results]


def _search_text(
    node: AXNode,
    query: str,
    role_filter: str | None,
    exact: bool,
    results: list[tuple[int, AXNode]],
) -> None:
    if not role_filter or node.role == role_filter:
        label = node.label
        if exact:
            if label == query:
                results.append((0, node))
        else:
            label_lower = label.lower()
            if label_lower == query:
                results.append((0, node))
            elif query in label_lower:
                results.append((1, node))

    for child in node.children:
        _search_text(child, query, role_filter, exact, results)


def find_by_role(root: AXNode, role: str) -> list[AXNode]:
    results: list[AXNode] = []
    _search_role(root, role, results)
    return results


def _search_role(node: AXNode, role: str, results: list[AXNode]) -> None:
    if node.role == role:
        results.append(node)
    for child in node.children:
        _search_role(child, role, results)


def locate(
    text: str,
    *,
    role: str | None = None,
    pid: int | None = None,
) -> list[dict[str, Any]]:
    tree = build_locator_tree(pid=pid)
    if tree is None:
        return []
    nodes = find_by_text(tree, text, role_filter=role)
    return [n.to_dict() for n in nodes]


def snapshot(pid: int | None = None) -> list[dict[str, Any]]:
    tree = build_locator_tree(pid=pid)
    if tree is None:
        return []
    return get_all_locators(tree)
