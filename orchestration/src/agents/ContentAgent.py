from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool

from src.agents.BaseAgent import BaseAgent

LOGGER = logging.getLogger(__name__)


class ReadPageTool(BaseTool):

    name: str = "ReadPageTool"
    description: str = (
        "Read visible text content from the current screen or browser page. "
        "Returns up to 4000 characters of text found on screen."
        "Call this first to get the page context before writing a summary or fact."
    )

    def _run(self, input: Any = "") -> str:
        try:
            from src.tools.mapper_tools import _get_ax_elements

            ax = _get_ax_elements()
            if ax:
                text = " ".join(e.get("text", "") for e in ax if e.get("text"))
                if len(text) > 200:
                    return text[:4000]

            try:
                from src.tools.browser_tools import _BrowserConnection

                page = _BrowserConnection.get_page()
                if page and not page.is_closed():
                    body_text = page.inner_text("body", timeout=3000)
                    return body_text[:4000]
            except Exception:
                pass

            from src.tools.mapper_tools import (
                capture_screen,
                timestamped_screenshot_path,
            )
            from src.tools.utils.ocr import run_ocr

            path = timestamped_screenshot_path()
            err = capture_screen(path)
            if err:
                return f"Error capturing screen: {err}"
            lines = run_ocr(path)
            text = " ".join(lines)
            return text[:4000] or "No text found on screen."

        except Exception as exc:
            return f"Error reading page: {exc}"


_SYSTEM_PROMPT = (
    "You are a writing assistant. Given page content and a description of what "
    "to write or extract, output ONLY the exact text to type — no preamble, "
    "no labels, no markdown, no explanation. Raw text only.\n\n"
    "CONTENT RULES:\n"
    "• 'fun facts': Write 3-5 specific, interesting, non-obvious facts. "
    "Number each fact. Each fact should be 1-2 sentences.\n"
    "• 'summary': Write 2-4 concise sentences covering the main points.\n"
    "• 'key points' / 'highlights': Use numbered items, each 1 sentence.\n"
    "• 'note' / 'write about': A short paragraph (3-5 sentences) on the topic.\n"
    "• Extraction (a song name, price, title, amount): output ONLY that exact value.\n"
    "• If the page content is sparse or irrelevant, use your general knowledge "
    "about the topic to write accurate, interesting content.\n\n"
    "NEVER output fewer than 50 words for fun facts, summaries, or notes.\n\n"
    "WHAT YOU MUST NEVER DO:\n"
    "• Never include 'Here is...', 'Sure!', 'Based on the page...' or any preamble.\n"
    "• Never add headers, bold, or markdown formatting.\n"
    "• Never exceed 150 words unless the user explicitly asked for more."
)


class ContentAgent(BaseAgent):
    def __init__(self):
        super().__init__(temperature=0.5, num_predict=800)
        self._reader = ReadPageTool()

    def generate(self, description: str) -> str:
        try:
            page_text = self._reader._run("")
            if page_text.startswith("Error"):
                LOGGER.warning("ReadPageTool failed: %s", page_text[:80])
                page_text = ""
            return self.generate_with_context(description, page_text)
        except Exception as exc:
            LOGGER.warning(
                "ContentAgent.generate failed for '%s': %s", description, exc
            )
            return description

    def generate_with_context(self, description: str, page_text: str) -> str:
        user = (
            f"Page content:\n{page_text[:4000]}\n\n"
            f"Task: {description}\n\n"
            "Output ONLY the requested text:"
        )

        try:
            result = self._call_llm(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ]
            )
            result = self._clean(result, description)
            LOGGER.info(
                "ContentAgent generated %d chars for: %s", len(result), description
            )
            return result or description
        except Exception as exc:
            LOGGER.warning("ContentAgent failed: %s", exc)
            return description

    @staticmethod
    def format_findings(findings: list[dict], page_text: str = "") -> str:
        parts: list[str] = []
        for f in findings:
            kind = f.get("type", "read")
            head = str(f.get("query_or_url", ""))[:80]
            content = str(f.get("content", ""))[:2500]
            parts.append(f"Source ({kind}): {head}\n{content}")
        if not parts:
            return page_text
        return "\n\n".join(parts)

    def generate_from_findings(
        self, description: str, findings: list[dict], page_text: str = ""
    ) -> str:
        context = self.format_findings(findings, page_text)
        if not context:
            return self.generate(description)
        return self.generate_with_context(description, context)

    @staticmethod
    def _clean(result: str, description: str) -> str:
        result = result.strip()
        for prefix in (
            "Here is",
            "Sure",
            "Based on",
            "The fun fact",
            "Summary:",
            "Fee owed:",
            "Amount owed:",
        ):
            if result.lower().startswith(prefix.lower()):
                cut = result.find(":")
                if 0 < cut < 80:
                    result = result[cut + 1 :].strip()
                break
        return result or description
