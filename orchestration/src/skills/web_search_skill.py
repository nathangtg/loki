from __future__ import annotations

import html
import logging
import re
import subprocess
from urllib.parse import quote_plus

import requests

from src.graph.llm import call_llm
from src.skills.applescript_utils import run_applescript
from src.skills.base import Skill, SkillIntent
from src.skills.registry import register_skill

LOGGER = logging.getLogger(__name__)

_WEATHER_RE = re.compile(
    r"(?:weather|temperature|forecast|rain\w*|sunny|hot|cold)(?:\s+(?:in|at|for))?\s+([A-Za-z0-9\s,.-]+)",
    re.IGNORECASE,
)
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


def open_in_safari(url: str) -> None:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        subprocess.run(
            ["open", "-a", "Safari", url],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception as exc:
        LOGGER.warning("web_search_skill: failed to open Safari for %s: %s", url, exc)


def _fetch_weather_summary(location: str) -> str | None:
    loc = location.strip().rstrip(".!?")
    if not loc:
        return None
    try:
        resp = requests.get(
            f"https://wttr.in/{quote_plus(loc)}?format=j1",
            headers={"User-Agent": "curl/8.0"},
            timeout=4.0,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        current = (data.get("current_condition") or [{}])[0]
        area = (data.get("nearest_area") or [{}])[0]
        city = ((area.get("areaName") or [{}])[0].get("value")) or loc.title()
        temp_c = current.get("temp_C")
        feels_c = current.get("FeelsLikeC")
        desc = ((current.get("weatherDesc") or [{}])[0].get("value") or "").lower()
        if temp_c is not None and desc:
            if feels_c and feels_c != temp_c:
                return f"It's currently {temp_c} degrees and {desc} in {city}, feeling like {feels_c} degrees. I've pulled up the full forecast in Safari for you."
            return f"It's currently {temp_c} degrees and {desc} in {city}. I've opened the full forecast in Safari for you."
    except Exception as exc:
        LOGGER.debug("web_search_skill: weather API lookup failed: %s", exc)
    return None


def _fetch_web_snippets(query: str, max_snippets: int = 4) -> list[str]:
    snippets: list[str] = []
    try:
        ddg_api = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            headers={"User-Agent": _USER_AGENT},
            timeout=4.0,
        )
        if ddg_api.status_code == 200:
            payload = ddg_api.json()
            abstract = (payload.get("AbstractText") or "").strip()
            answer = (payload.get("Answer") or "").strip()
            if answer:
                snippets.append(answer)
            if abstract:
                snippets.append(abstract)
            for topic in (payload.get("RelatedTopics") or [])[:3]:
                if isinstance(topic, dict) and topic.get("Text"):
                    snippets.append(str(topic["Text"]).strip())
    except Exception as exc:
        LOGGER.debug("web_search_skill: DDG instant answer failed: %s", exc)

    if snippets:
        return snippets[:max_snippets]

    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": _USER_AGENT},
            timeout=4.5,
        )
        if resp.status_code == 200:
            raw_matches = re.findall(
                r'<a class="result__snippet"[^>]*>(.*?)</a>',
                resp.text,
                flags=re.DOTALL | re.IGNORECASE,
            )
            for raw in raw_matches[:max_snippets]:
                clean = re.sub(r"<[^>]+>", "", raw)
                clean = html.unescape(" ".join(clean.split())).strip()
                if clean:
                    snippets.append(clean)
    except Exception as exc:
        LOGGER.debug("web_search_skill: DDG HTML snippet fetch failed: %s", exc)

    return snippets[:max_snippets]


def execute_web_search(query: str, *, open_browser: bool = True) -> str:
    clean_query = " ".join((query or "").split()).strip().rstrip(".!?")
    if not clean_query:
        raise ValueError("Missing search query")

    search_url = f"https://www.google.com/search?q={quote_plus(clean_query)}"
    if open_browser:
        open_in_safari(search_url)

    weather_match = _WEATHER_RE.search(clean_query)
    if weather_match:
        location = weather_match.group(1).strip()
        weather_reply = _fetch_weather_summary(location)
        if weather_reply:
            return weather_reply

    snippets = _fetch_web_snippets(clean_query)
    if snippets:
        try:
            context = "\n".join(f"- {s}" for s in snippets)
            summary = call_llm(
                system=(
                    "You are Loki, a warm, casual, and upbeat voice assistant on macOS. "
                    "Given search snippets for the user's query, answer their query in 1-2 short, "
                    "natural spoken sentences (no markdown, no bullet points, no URLs). "
                    "Mention casually that you've also pulled up the results in Safari."
                ),
                user=f"Query: {clean_query}\n\nSearch Results:\n{context}",
                num_predict=140,
                temperature=0.1,
            ).strip()
            if summary:
                return summary
        except Exception as exc:
            LOGGER.debug("web_search_skill: LLM snippet summary failed: %s", exc)
        return f"{snippets[0]} I've also opened the search results in Safari for you."

    return f"All set! I've pulled up the search results for {clean_query} in Safari."


def _handle_search_web(params: dict) -> str:
    query = params.get("query") or params.get("direct_parameter") or ""
    return execute_web_search(query, open_browser=True)


def _handle_open_url(params: dict) -> str:
    url = (params.get("url") or params.get("direct_parameter") or "").strip()
    if not url:
        raise ValueError("Missing 'url' parameter")
    open_in_safari(url)
    clean_host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    return f"Here you go! I've opened {clean_host} in Safari for you."


_LIST_SAFARI_TABS_SCRIPT = """
if application "Safari" is not running then return "NOT_RUNNING"
set sep to ASCII character 9
set nl to ASCII character 10
set out to ""
tell application "Safari"
    repeat with w in windows
        try
            set activeIndex to index of current tab of w
            set n to count of tabs of w
            repeat with i from 1 to n
                set marker to "0"
                if i is activeIndex then set marker to "1"
                set t to tab i of w
                set out to out & marker & sep & (name of t) & sep & (URL of t) & nl
            end repeat
        end try
    end repeat
end tell
return out
"""

_MAX_SPOKEN_OTHER_TABS = 5


def _host_of(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url or "").split("/")[0]


def _describe_tab(title: str, url: str) -> str:
    host = _host_of(url)
    title = (title or "").strip()
    if title and host and host.lower() not in title.lower():
        return f"{title} on {host}"
    return title or host or "a blank page"


def describe_safari_tabs(raw: str) -> str:
    if raw.strip() == "NOT_RUNNING":
        return "Safari isn't open right now."

    tabs: list[tuple[bool, str, str]] = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            tabs.append((parts[0] == "1", parts[1], parts[2]))
    if not tabs:
        return "Safari is open, but it doesn't have any windows showing."

    # Safari lists windows front to back, so the first active tab is the one on screen.
    front = next((t for t in tabs if t[0]), tabs[0])
    others = [t for t in tabs if t is not front]
    total = len(tabs)
    sentence = f"You're looking at {_describe_tab(front[1], front[2])} in Safari."
    if not others:
        return sentence

    spoken = [_describe_tab(t[1], t[2]) for t in others[:_MAX_SPOKEN_OTHER_TABS]]
    listed = spoken[0] if len(spoken) == 1 else ", ".join(spoken[:-1]) + f", and {spoken[-1]}"
    extra = len(others) - len(spoken)
    tail = f", plus {extra} more" if extra > 0 else ""
    return f"{sentence} You have {total} tabs open in total. The others are {listed}{tail}."


def _handle_describe_open_tabs(params: dict) -> str:
    try:
        raw = run_applescript(_LIST_SAFARI_TABS_SCRIPT)
    except RuntimeError as exc:
        LOGGER.warning("web_search_skill: reading Safari tabs failed: %s", exc)
        return (
            "I couldn't read Safari's tabs. Please allow Loki to control Safari "
            "in System Settings under Privacy and Security, Automation."
        )
    return describe_safari_tabs(raw)


def _build_web_search_skill() -> Skill:
    skill = Skill(
        name="web_search",
        description="Safari Web Search & Live Answer Fetching — instantly searches the web, fetches weather/facts, and opens results in Safari",
        app_names={"safari", "google", "web", "browser", "edge", "chrome"},
    )
    skill.add_intent(
        SkillIntent(
            name="search_web",
            description="Search the web or Google for information, weather, news, or topics and open in Safari.",
            params=["query"],
            handler=_handle_search_web,
        )
    )
    skill.add_intent(
        SkillIntent(
            name="open_url",
            description="Open a specific website URL directly in Safari.",
            params=["url"],
            handler=_handle_open_url,
        )
    )
    skill.add_intent(
        SkillIntent(
            name="describe_open_tabs",
            description="Tell the user what is open in Safari right now: the page on screen and the other open tabs.",
            params=[],
            handler=_handle_describe_open_tabs,
        )
    )
    return skill


register_skill(_build_web_search_skill())
