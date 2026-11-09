from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import urlparse

LOGGER = logging.getLogger(__name__)

MAX_RECENT_ACTIONS = 12
MAX_SUMMARY_CHARS = 600


@dataclass
class ActionRecord:
    timestamp: float
    agent: str
    tool: str
    input: str
    result: str

    def oneliner(self) -> str:
        inp = self.input[:80].replace("\n", " ")
        res = self.result[:80].replace("\n", " ")
        return f"[{self.agent}/{self.tool}] {inp} → {res}"


class SessionMemory:

    _instance: SessionMemory | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init = False
        return cls._instance

    def __init__(self):
        if self._init:
            return
        self._init = True
        self.actions: list[ActionRecord] = []
        self.condensed: str = ""
        self.current_url: str = ""
        self.current_page_title: str = ""
        self.current_app: str = ""
        self.logged_in: dict[str, str] = {}
        self.command_history: list[str] = []

    def record_command(self, user_command: str) -> None:
        self.command_history.append(user_command)

    def record_action(self, agent: str, tool: str, inp: str, result: str) -> None:
        self.actions.append(
            ActionRecord(
                timestamp=time.time(),
                agent=agent,
                tool=tool,
                input=inp,
                result=result,
            )
        )
        self._detect_state(agent, tool, inp, result)
        if len(self.actions) > MAX_RECENT_ACTIONS * 2:
            self._condense()

    def _detect_state(self, agent: str, tool: str, inp: str, result: str) -> None:
        result_lower = (result or "").lower()
        input_lower = (inp or "").lower()

        if tool in ("BrowserNavigateTool", "navigate", "read_url"):
            if "Navigated to:" in result:
                parts = result.split("Navigated to:")
                if len(parts) > 1:
                    url_part = parts[1].strip()
                    self.current_url = url_part.split("—")[0].strip()
                    if "Title:" in url_part:
                        self.current_page_title = url_part.split("Title:")[1].strip()
            elif inp.strip().startswith(("http://", "https://")) and "error" not in result_lower:
                self.current_url = inp.strip()

        if tool in ("OpenMacApplicationTool", "open_app") and "error" not in result_lower:
            if "opened" in result_lower or tool == "open_app":
                self.current_app = inp.strip()

        if tool in ("BrowserTypeTool", "type_text") and (
            "password" in input_lower or "{{password}}" in input_lower or "••••••••" in result
        ):
            if self.current_url:
                domain = urlparse(self.current_url).netloc
                if domain:
                    self.logged_in[domain] = "active"

        if tool in ("BrowserClickTool", "BrowserNavigateTool", "click", "navigate", "read_url"):
            if any(w in result_lower for w in ["dashboard", "welcome", "home", "logged in"]):
                if self.current_url:
                    domain = urlparse(self.current_url).netloc
                    if domain:
                        self.logged_in[domain] = "confirmed"

    def _condense(self) -> None:
        keep = MAX_RECENT_ACTIONS
        if len(self.actions) <= keep:
            return

        old = self.actions[:-keep]
        self.actions = self.actions[-keep:]

        lines = [a.oneliner() for a in old]
        old_summary = "\n".join(lines)
        if len(old_summary) > MAX_SUMMARY_CHARS:
            old_summary = old_summary[-MAX_SUMMARY_CHARS:]

        if self.condensed:
            combined = self.condensed + "\n" + old_summary
            if len(combined) > MAX_SUMMARY_CHARS:
                combined = combined[-MAX_SUMMARY_CHARS:]
            self.condensed = combined
        else:
            self.condensed = old_summary

        LOGGER.info("Condensed %d old actions into summary", len(old))

    def get_state_only(self) -> str:
        state_lines = self._format_state_lines()
        if not state_lines:
            return ""

        return (
            "SESSION STATE:\n"
            + "\n".join(state_lines)
            + "\n\n"
            "Use this state to avoid repeating work.\n\n"
        )

    def _format_state_lines(self) -> list[str]:
        lines = []
        if self.current_url:
            lines.append(f"Current browser page: {self.current_url}")
            if self.current_page_title:
                lines.append(f"  Page title: {self.current_page_title}")
        if self.current_app:
            lines.append(f"Current native app: {self.current_app}")
        if self.logged_in:
            logins = ", ".join(f"{d} ({s})" for d, s in self.logged_in.items())
            lines.append(f"Logged in to: {logins}")
        return lines

