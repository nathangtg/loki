from __future__ import annotations

import html as _html
import logging
import re
import time
import urllib.parse
from typing import Any

import requests
from langchain_core.tools import BaseTool
from src.memory import SessionMemory

LOGGER = logging.getLogger(__name__)
_memory = SessionMemory()

MAX_RESULTS = 5
MAX_CONTENT_CHARS = 6000
_REQUEST_TIMEOUT = 15

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html",
    "Accept-Language": "en-US,en;q=0.9",
}

_ANCHOR_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.DOTALL)
_TD_RE = re.compile(r"<td\b([^>]*)>(.*?)</td>", re.DOTALL)
_ATTR_RE = re.compile(r"""([\w-]+)\s*=\s*["']([^"']*)["']""")

_BLOCKED_MARKERS = (
    "turnstile",
    "cloudflare",
    "captcha",
    "challenge",
    "verify you are human",
    "just a moment",
)


def _parse_attrs(attr_text: str) -> dict[str, str]:
    return {name.lower(): value for name, value in _ATTR_RE.findall(attr_text)}


def _strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", "", fragment)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _unwrap_ddg_href(href: str) -> str:
    if "uddg=" in href:
        raw = href if "://" in href else "https:" + href
        params = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)
        targets = params.get("uddg")
        if targets:
            return urllib.parse.unquote(targets[0])
    return href


def _normalize_url(url: str) -> str:
    url = _unwrap_ddg_href(url)
    if url.startswith("//"):
        url = "https:" + url
    return url


def _is_http_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def _extract_results(
    markup: str, link_class: str, snippet_classes: tuple[str, ...]
) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for attr_text, inner in _ANCHOR_RE.findall(markup):
        attrs = _parse_attrs(attr_text)
        if link_class not in attrs.get("class", "").split():
            continue
        url = _normalize_url(attrs.get("href", ""))
        title = _strip_tags(inner)
        if title and _is_http_url(url):
            links.append({"url": url, "title": title})

    snippets: list[str] = []
    for regex in (_ANCHOR_RE, _TD_RE):
        for attr_text, inner in regex.findall(markup):
            attrs = _parse_attrs(attr_text)
            classes = attrs.get("class", "").split()
            if any(c in classes for c in snippet_classes):
                snippet = _strip_tags(inner)
                if snippet:
                    snippets.append(snippet)

    for i, result in enumerate(links):
        if i < len(snippets):
            result["snippet"] = snippets[i]
    return links


def _ddg_html_results(query: str) -> list[dict[str, str]]:
    response = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers=_HEADERS,
        timeout=_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return _extract_results(
        response.text, link_class="result__a", snippet_classes=("result__snippet",)
    )


def _ddg_lite_results(query: str) -> list[dict[str, str]]:
    response = requests.post(
        "https://lite.duckduckgo.com/lite/",
        data={"q": query},
        headers=_HEADERS,
        timeout=_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return _extract_results(
        response.text, link_class="result-link", snippet_classes=("result-snippet",)
    )


def web_search(query: str, max_results: int = MAX_RESULTS) -> list[dict[str, str]]:
    errors: list[str] = []
    for fetch in (_ddg_html_results, _ddg_lite_results):
        try:
            results = fetch(query)
        except Exception as exc:
            errors.append(f"{fetch.__name__}: {exc}")
            continue
        results = [r for r in results if r.get("title") and r.get("url")]
        if results:
            return results[:max_results]
        errors.append(f"{fetch.__name__}: parsed 0 results")
    raise RuntimeError("web search failed (" + "; ".join(errors) + ")")


def format_search_results(results: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        line = f"[{i}] {r['title']}\n    {r['url']}"
        snippet = r.get("snippet", "")
        if snippet:
            line += f"\n    {snippet[:200]}"
        lines.append(line)
    return "\n".join(lines)


def clean_page_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    cleaned = "\n".join(ln for ln in lines if ln)
    return cleaned[:MAX_CONTENT_CHARS]


def _fetch_via_browser(url: str) -> tuple[str, str]:
    from src.tools.browser_tools import _BrowserConnection

    page = _BrowserConnection.get_page()
    research_page = page.context.new_page()
    _BrowserConnection._page = research_page

    research_page.goto(url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(1.0)
    title = research_page.title()
    body = research_page.inner_text("body", timeout=5000)
    return title, body


class WebSearchTool(BaseTool):
    name: str = "WebSearchTool"
    description: str = (
        "Search the web. Pass a plain-text query (e.g. 'weather in Kuala Lumpur'). "
        "Returns the top results with titles, URLs, and snippets. Use this INSTEAD "
        "of navigating to a search engine — it is faster and needs no page interaction."
    )

    def _run(self, input: Any) -> str:
        query = str(input).strip()
        if not query:
            return "Error: empty search query."
        try:
            results = web_search(query)
        except Exception as exc:
            LOGGER.warning("WebSearchTool failed for '%s': %s", query, exc)
            return f"Error: web search failed: {exc}"
        formatted = format_search_results(results)
        _memory.record_action(
            "browser", "WebSearchTool", query, f"Found {len(results)} results"
        )
        return formatted


class FetchPageContentTool(BaseTool):

    name: str = "FetchPageContentTool"
    description: str = (
        "Open a URL in the user's browser (new tab) and read the page content in one "
        "step. Pass a full URL (e.g. 'https://en.wikipedia.org/wiki/Apple'). Uses the "
        "user's browser session, so logged-in pages work too. Use this instead of "
        "navigate + read_page when you already know the URL."
    )

    def _run(self, input: Any) -> str:
        url = str(input).strip()
        if not _is_http_url(url):
            return f"Error: invalid URL: {url[:120]}"

        try:
            title, body = _fetch_via_browser(url)
        except Exception as exc:
            LOGGER.warning("FetchPageContentTool failed for %s: %s", url[:80], exc)
            return f"Error: could not read {url[:80]}: {exc}"

        content = clean_page_text(body)
        if any(m in (title + content[:400]).lower() for m in _BLOCKED_MARKERS):
            return (
                f"Error: {url[:80]} is blocked by a bot/CAPTCHA challenge — "
                "try navigating there manually instead."
            )
        if not content:
            return f"Error: no readable content on {url[:80]}"

        result = f"Title: {title}\nURL: {url}\n\n{content}"
        _memory.record_action(
            "browser", "FetchPageContentTool", url, f"Read {len(content)} chars"
        )
        return result
