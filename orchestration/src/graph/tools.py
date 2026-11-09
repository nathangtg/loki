from __future__ import annotations

_native: dict | None = None
_browser: dict | None = None


def get_native_tools() -> dict:
    global _native
    if _native is None:
        from src.tools.mapper_tools import (
            ClickByLabelTool,
            ClickCoordinatesTool,
            ExtractScreenshotTextTool,
            ScreenMapperTool,
            ScreenshotTool,
        )
        from src.tools.macui_tools import (
            FindMacApplicationsTool,
            GetAppShortcutsTool,
            OpenMacApplicationTool,
        )
        from src.agents.StepAgent import (
            KeyComboTool,
            ScrollTool,
            TypeTextTool,
            WaitTool,
        )

        click_label = ClickByLabelTool()
        _native = {
            "screen_map": ScreenMapperTool(),
            "click_label": click_label,
            "screenshot": ScreenshotTool(),
            "extract_text": ExtractScreenshotTextTool(),
            "click_coords": ClickCoordinatesTool(),
            "click": click_label,
            "find_apps": FindMacApplicationsTool(),
            "open_app": OpenMacApplicationTool(),
            "shortcuts": GetAppShortcutsTool(),
            "type": TypeTextTool(),
            "key_combo": KeyComboTool(),
            "wait": WaitTool(),
            "scroll": ScrollTool(),
        }
    return _native


def get_browser_tools() -> dict:
    global _browser
    if _browser is None:
        from src.agents.BrowserAgent import BrowserAgent

        browser = BrowserAgent()
        _browser = browser.tools
    return _browser


def live_url() -> str:
    try:
        from src.tools.browser_tools import _BrowserConnection

        page = _BrowserConnection.get_page()
        return page.url if page and not page.is_closed() else ""
    except Exception:
        return ""


def live_title() -> str:
    try:
        from src.tools.browser_tools import _BrowserConnection

        page = _BrowserConnection.get_page()
        return page.title() if page and not page.is_closed() else ""
    except Exception:
        return ""
