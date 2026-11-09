from __future__ import annotations

import logging

from src.agents.BaseAgent import BaseAgent
from src.tools.browser_tools import (
    BrowserClickTool,
    BrowserGetElementsTool,
    BrowserGetTextTool,
    BrowserNavigateTool,
    BrowserPressKeyTool,
    BrowserScreenshotTool,
    BrowserTypeTool,
    BrowserWaitTool,
)

LOGGER = logging.getLogger(__name__)


class BrowserAgent(BaseAgent):
    def __init__(self):
        super().__init__()
        self.tools = {
            "navigate": BrowserNavigateTool(),
            "click": BrowserClickTool(),
            "type": BrowserTypeTool(),
            "press_key": BrowserPressKeyTool(),
            "get_text": BrowserGetTextTool(),
            "get_elements": BrowserGetElementsTool(),
            "wait": BrowserWaitTool(),
            "screenshot": BrowserScreenshotTool(),
        }
