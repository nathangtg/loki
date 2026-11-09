from __future__ import annotations

import logging
import time
from typing import Any

from langchain_core.tools import BaseTool
from src.memory import SessionMemory
from src.tools.utils.constants import SCREENSHOTS_DIR

LOGGER = logging.getLogger(__name__)
_memory = SessionMemory()

CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"

EDGE_PATH = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"


class _BrowserConnection:

    _playwright: Any = None
    _browser: Any = None
    _page: Any = None
    _thread_id: int | None = None
    _launch_attempted: bool = False

    @classmethod
    def _launch_edge_with_debugging(cls) -> None:
        raise RuntimeError("Edge CDP is disabled; using native macOS Safari.")

    @classmethod
    def get_page(cls):
        import threading

        current_thread = threading.current_thread().ident

        if cls._thread_id is not None and cls._thread_id != current_thread:
            LOGGER.info(
                "Thread changed (%s → %s) — reconnecting Playwright",
                cls._thread_id,
                current_thread,
            )
            cls._reset()

        if cls._page is not None:
            try:
                if not cls._page.is_closed():
                    return cls._page
            except Exception:
                cls._reset()

        return cls._connect(current_thread)

    @classmethod
    def _connect(cls, thread_id: int | None):
        cls._launch_edge_with_debugging()

        from playwright.sync_api import sync_playwright

        for attempt in range(2):
            try:
                if cls._playwright is None:
                    cls._playwright = sync_playwright().start()

                cls._browser = cls._playwright.chromium.connect_over_cdp(CDP_URL)
                cls._thread_id = thread_id

                contexts = cls._browser.contexts
                if contexts:
                    pages = contexts[0].pages
                    if pages:
                        cls._page = pages[0]
                        LOGGER.info("Connected to existing tab: %s", cls._page.url[:80])
                        return cls._page

                ctx = cls._browser.new_context()
                cls._page = ctx.new_page()
                return cls._page

            except Exception as exc:
                LOGGER.warning(
                    "Playwright connect attempt %d failed: %s", attempt + 1, exc
                )
                cls._reset()
                if attempt == 0:
                    time.sleep(1)

        raise RuntimeError("Could not connect to browser after 2 attempts")

    @classmethod
    def _reset(cls):
        old_pw = cls._playwright

        cls._playwright = None
        cls._browser = None
        cls._page = None
        cls._thread_id = None
        cls._launch_attempted = False

        try:
            if old_pw:
                old_pw.stop()
        except Exception:
            pass

    @classmethod
    def close(cls):
        try:
            if cls._playwright:
                cls._playwright.stop()
        except Exception:
            pass
        cls._playwright = None
        cls._browser = None
        cls._page = None


class BrowserNavigateTool(BaseTool):

    name: str = "BrowserNavigateTool"
    description: str = (
        "Navigate the live browser to a URL. Pass the full URL as a string "
        "(e.g. 'https://izone.sunway.edu.my/'). Waits for the page to load. "
        "If the URL is unknown, pass a search query like 'search: sunway izone portal' "
        "and it will Google it for you."
    )

    def _run(self, input: Any) -> str:
        import json as _json
        from pathlib import Path as _Path

        raw = str(input).strip()

        if raw.lower().startswith("search:"):
            query = raw[7:].strip()
            url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        else:
            url = raw
            if not url.startswith(("http://", "https://")):
                url = "https://" + url

            placeholder_markers = [
                "placeholder",
                "(placeholder)",
                "%20placeholder",
                "url%20",
            ]
            if any(m in url.lower() for m in placeholder_markers) or "%20" in url:
                try:
                    config_path = (
                        _Path(__file__).resolve().parent.parent.parent / "data" / "known_sites.json"
                    )
                    with open(config_path) as f:
                        config = _json.load(f)
                    sites = config.get("sites", {})
                    raw_lower = raw.lower()
                    for name, site_url in sites.items():
                        if name in raw_lower:
                            url = site_url
                            LOGGER.info("Resolved '%s' to known site: %s", raw, url)
                            break
                    else:
                        clean_query = (
                            raw.replace("%20", " ").replace("(placeholder)", "").strip()
                        )
                        url = f"https://www.google.com/search?q={clean_query.replace(' ', '+')}"
                        LOGGER.info(
                            "URL looks like placeholder, searching Google: %s", url
                        )
                except Exception:
                    clean_query = (
                        raw.replace("%20", " ").replace("(placeholder)", "").strip()
                    )
                    url = f"https://www.google.com/search?q={clean_query.replace(' ', '+')}"

        try:
            page = _BrowserConnection.get_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            result = f"Navigated to: {page.url} — Title: {page.title()}"
            _memory.record_action(
                "browser",
                "BrowserNavigateTool",
                raw,
                result,
            )
            return result
        except Exception as exc:
            return f"Error navigating to {url}: {exc}"


class BrowserClickTool(BaseTool):

    name: str = "BrowserClickTool"
    description: str = (
        "Click an element in the live browser. PREFERRED: use the element "
        "index [N] from the interactive elements list, e.g. '[14]' or '14'. "
        "This guarantees clicking the correct element.\n"
        "Fallback selectors: 'text=Sign In', '#username', 'input[name=\"email\"]'"
    )

    @staticmethod
    def _parse_index(selector: str) -> int | None:
        import re

        m = re.match(r"^\[?(\d{1,3})\]?$", selector)
        return int(m.group(1)) if m else None

    def _run(self, input: Any) -> str:
        selector = str(input).strip()
        if not selector:
            return "Error: no selector provided."
        try:
            page = _BrowserConnection.get_page()

            idx = self._parse_index(selector)
            if idx is not None:
                result = self._click_by_index(page, idx)
                if result:
                    return result

            expanded_parent: list[str | None] = [None]

            def _try_click(sel: str) -> bool:
                try:
                    loc = page.locator(sel)
                    count = loc.count()
                    if count == 0:
                        LOGGER.debug("Selector '%s' → 0 matches", sel)
                        return False
                    if count == 1:
                        if loc.is_visible():
                            loc.click(timeout=5000)
                            return True
                        if self._expand_parent_for(page, sel):
                            expanded_parent[0] = sel
                            time.sleep(0.5)
                            loc.click(timeout=5000)
                            return True
                        return False
                    for i in range(min(count, 10)):
                        el = loc.nth(i)
                        if el.is_visible():
                            el.click(timeout=3000)
                            return True
                    if self._expand_parent_for(page, sel):
                        expanded_parent[0] = sel
                        time.sleep(0.5)
                        for i in range(min(count, 10)):
                            el = loc.nth(i)
                            if el.is_visible():
                                el.click(timeout=3000)
                                return True
                    return False
                except Exception as exc:
                    LOGGER.debug("Click attempt failed for '%s': %s", sel, exc)
                    return False

            selectors_to_try = [selector]
            if not selector.startswith(
                ("text=", "#", ".", "//", "input", "button", "a[", "[", "css=")
            ):
                selectors_to_try += [
                    f"text={selector}",
                    f'a:text-is("{selector}")',
                    f'button:text-is("{selector}")',
                    f'input[value="{selector}"]',
                    f'input[value*="{selector}"]',
                    f'[aria-label="{selector}"]',
                    f'[title="{selector}"]',
                ]

            for sel in selectors_to_try:
                if _try_click(sel):
                    if expanded_parent[0]:
                        result = f"Clicked: {sel} (auto-expanded parent menu first)"
                    else:
                        result = f"Clicked: {sel}"
                    _memory.record_action(
                        "browser", "BrowserClickTool", selector, result
                    )
                    return result

            return f"Error: could not find or click element '{selector}'"
        except Exception as exc:
            return f"Error clicking '{selector}': {exc}"

    @staticmethod
    def _click_by_index(page, idx: int) -> str:
        try:
            info = page.evaluate(f"""() => {{
                const el = (window.__loki_elements || [])[{idx}];
                if (!el) return null;
                const rect = el.getBoundingClientRect();
                return {{
                    tag: el.tagName,
                    text: (el.innerText || el.textContent || '').trim().slice(0, 60),
                    visible: rect.width > 0 && rect.height > 0,
                    href: (typeof el.href === 'string') ? el.href.slice(0, 120) : ''
                }};
            }}""")

            if info is None:
                LOGGER.warning(
                    "Element [%d] not in __loki_elements (page may have changed)", idx
                )
                return ""

            clicked = page.evaluate(f"""() => {{
                const el = window.__loki_elements[{idx}];
                if (!el) return false;
                el.scrollIntoView({{block: 'center', behavior: 'instant'}});
                el.click();
                return true;
            }}""")

            if clicked:
                desc = (
                    info.get("text", "") or info.get("href", "") or info.get("tag", "?")
                )
                result = f"Clicked [{idx}]: <{info['tag'].lower()}> {desc}"
                LOGGER.info("Index click [%d] succeeded: %s", idx, result)
                _memory.record_action("browser", "BrowserClickTool", f"[{idx}]", result)
                return result

            return ""
        except Exception as exc:
            LOGGER.debug("Index click [%d] failed: %s", idx, exc)
            return ""

    @staticmethod
    def _expand_parent_for(page, sel: str) -> bool:
        try:
            trigger_sel = page.evaluate(
                """(sel) => {
                // Try CSS first, then manual Playwright-like text search
                let el = document.querySelector(sel);
                if (!el) {
                    // Handle text-based selectors by searching all elements
                    const textMatch = sel.match(/text[=-](?:is\\(")?(.*?)(?:"\\))?$/i);
                    if (textMatch) {
                        const target = textMatch[1];
                        const all = document.querySelectorAll('a, button');
                        for (const a of all) {
                            if (a.innerText.trim() === target) { el = a; break; }
                        }
                    }
                }
                if (!el) return null;
                let parent = el.parentElement;
                let depth = 0;
                while (parent && depth < 10) {
                    const cls = parent.className || '';
                    if (cls.match && cls.match(/(collapse|dropdown|menu|accordion|submenu|panel|nav-item)/i)) {
                        const trigger = parent.previousElementSibling ||
                            parent.parentElement?.querySelector(':scope > a, :scope > button');
                        if (trigger) {
                            const id = trigger.getAttribute('id');
                            if (id) return '#' + id;
                            const text = (trigger.innerText || '').trim().substring(0, 30);
                            if (text) return text;  // return plain text, we'll use text= prefix
                        }
                    }
                    parent = parent.parentElement;
                    depth++;
                }
                return null;
            }""",
                sel,
            )

            if trigger_sel:
                if trigger_sel.startswith("#"):
                    page.click(trigger_sel, timeout=3000)
                else:
                    page.locator(f"text={trigger_sel}").first.click(timeout=3000)
                LOGGER.info("Auto-expanded parent menu: %s", trigger_sel)
                return True
        except Exception as exc:
            LOGGER.debug("Parent expand failed: %s", exc)
        return False


class BrowserTypeTool(BaseTool):

    name: str = "BrowserTypeTool"
    description: str = (
        "Type text into an element in the live browser. Pass input as:\n"
        "  'selector ||| text to type'\n"
        "PREFERRED: use the element index [N] as the selector:\n"
        "  '[7] ||| john@example.com'\n"
        "  '[12] ||| mypassword123'\n"
        "Fallback selectors: '#username', 'input[name=\"email\"]', etc.\n"
        "If the field is ALREADY focused, use just the text without a selector:\n"
        "  'hello world'"
    )

    def _run(self, input: Any) -> str:
        raw = str(input).strip()
        if not raw:
            return "Error: no input provided."

        try:
            page = _BrowserConnection.get_page()

            if "|||" in raw:
                parts = raw.split("|||", 1)
                selector = parts[0].strip()
                text = parts[1].strip()

                idx = BrowserClickTool._parse_index(selector)
                if idx is not None:
                    result = self._type_by_index(page, idx, text)
                    if result:
                        _memory.record_action("browser", "BrowserTypeTool", raw, result)
                        return result

                is_real_selector = (
                    selector.startswith(
                        ("#", ".", "[", "//", "input", "select", "textarea")
                    )
                    or ":text" in selector
                    or "text=" in selector
                    or "[name=" in selector
                    or "[placeholder=" in selector
                )

                if is_real_selector:
                    page.fill(selector, text, timeout=5000)
                    result = f"Typed '{text}' into {selector}"
                else:
                    result = self._type_by_description(page, selector, text)

                _memory.record_action("browser", "BrowserTypeTool", raw, result)
                return result
            else:
                page.keyboard.type(raw)
                result = f"Typed: '{raw}'"
                _memory.record_action("browser", "BrowserTypeTool", raw, result)
                return result
        except Exception as exc:
            return f"Error typing: {exc}"

    @staticmethod
    def _type_by_description(page, description: str, text: str) -> str:
        desc_lower = description.lower()

        inputs = page.evaluate("""() => {
            const els = document.querySelectorAll('input, textarea, select');
            const results = [];
            for (const el of els) {
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                results.push({
                    tag: el.tagName.toLowerCase(),
                    type: el.getAttribute('type') || '',
                    name: el.getAttribute('name') || '',
                    id: el.getAttribute('id') || '',
                    placeholder: el.getAttribute('placeholder') || '',
                    label: el.getAttribute('aria-label') || '',
                    selector: el.id ? '#' + el.id :
                              el.name ? el.tagName.toLowerCase() + '[name=\"' + el.name + '\"]' :
                              el.placeholder ? el.tagName.toLowerCase() + '[placeholder=\"' + el.placeholder + '\"]' :
                              null
                });
            }
            return results;
        }""")

        if not inputs:
            return f"Error: no visible input fields found for '{description}'"

        best = None
        best_score = 0
        for inp in inputs:
            if not inp.get("selector"):
                continue
            score = 0
            attrs = f"{inp.get('name', '')} {inp.get('id', '')} {inp.get('placeholder', '')} {inp.get('label', '')} {inp.get('type', '')}".lower()
            for word in desc_lower.split():
                if len(word) > 2 and word in attrs:
                    score += 1
            if score > best_score:
                best_score = score
                best = inp

        if not best or best_score == 0:
            for inp in inputs:
                if inp.get("selector") and inp.get("type", "text") in (
                    "text",
                    "number",
                    "search",
                    "tel",
                    "email",
                    "password",
                    "",
                ):
                    best = inp
                    break

        if not best or not best.get("selector"):
            available = [
                f"{i.get('id') or i.get('name') or i.get('placeholder') or i.get('type')}"
                for i in inputs[:5]
            ]
            return f"Error: no matching input for '{description}'. Available: {', '.join(available)}"

        sel = best["selector"]
        page.fill(sel, text, timeout=5000)
        return f"Typed '{text}' into {sel} (resolved from '{description}')"

    @staticmethod
    def _type_by_index(page, idx: int, text: str) -> str:
        try:
            success = page.evaluate(f"""() => {{
                const el = (window.__loki_elements || [])[{idx}];
                if (!el) return false;
                el.scrollIntoView({{block: 'center', behavior: 'instant'}});
                el.focus();
                // Clear existing value
                if ('value' in el) {{
                    el.value = '';
                    el.dispatchEvent(new Event('input', {{bubbles: true}}));
                }}
                return true;
            }}""")

            if not success:
                LOGGER.warning("Element [%d] not in __loki_elements for typing", idx)
                return ""

            page.keyboard.type(text, delay=30)
            result = f"Typed '{text}' into [{idx}]"
            LOGGER.info("Index type [%d] succeeded", idx)
            return result
        except Exception as exc:
            LOGGER.debug("Index type [%d] failed: %s", idx, exc)
            return ""


class BrowserPressKeyTool(BaseTool):

    name: str = "BrowserPressKeyTool"
    description: str = (
        "Press a key or shortcut in the browser. Examples:\n"
        "  'Enter' — press Enter\n"
        "  'Tab' — press Tab\n"
        "  'Escape' — press Escape\n"
        "  'Control+a' — select all\n"
        "  'Meta+l' — focus address bar (Cmd+L on Mac)"
    )

    def _run(self, input: Any) -> str:
        key = str(input).strip()
        if not key:
            return "Error: no key specified."
        try:
            page = _BrowserConnection.get_page()
            page.keyboard.press(key)
            result = f"Pressed: {key}"
            _memory.record_action("browser", "BrowserPressKeyTool", key, result)
            return result
        except Exception as exc:
            return f"Error pressing {key}: {exc}"


class BrowserGetTextTool(BaseTool):

    name: str = "BrowserGetTextTool"
    description: str = (
        "Get visible text from the current browser page. Pass a CSS selector to "
        "get text from a specific element, or pass 'page' to get all visible text "
        "on the page (truncated to first 3000 chars). Examples:\n"
        "  'page' — all visible text\n"
        "  '#main-content' — text inside element with id main-content\n"
        "  '.login-form' — text inside the login form"
    )

    def _run(self, input: Any) -> str:
        selector = str(input).strip()
        try:
            page = _BrowserConnection.get_page()
            if not selector or selector.lower() == "page":
                text = page.inner_text("body", timeout=5000)
                if len(text) > 3000:
                    text = text[:3000] + "\n... (truncated)"
                return f"Page text:\n{text}"
            else:
                text = page.inner_text(selector, timeout=5000)
                return f"Text in '{selector}':\n{text}"
        except Exception as exc:
            return f"Error getting text: {exc}"


class BrowserGetElementsTool(BaseTool):

    name: str = "BrowserGetElementsTool"
    description: str = (
        "List interactive elements (inputs, buttons, links) on the current page. "
        "Returns a summary of clickable/typeable elements with their selectors and "
        "visible text. Pass 'all' for everything, or a CSS scope selector to limit "
        "the search (e.g. '.login-form', '#main')."
    )

    def _run(self, input: Any) -> str:
        scope = str(input).strip()
        if not scope or scope.lower() == "all":
            scope = "body"

        try:
            page = _BrowserConnection.get_page()
            elements = page.evaluate(
                """(scope) => {
                const root = document.querySelector(scope) || document.body;
                const results = [];
                const els = root.querySelectorAll(
                    'a, button, input, select, textarea, [role="button"], [role="link"], [onclick]'
                );

                function isVisible(el) {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' ||
                        style.opacity === '0') return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }

                function getParentMenu(el) {
                    let parent = el.parentElement;
                    let depth = 0;
                    while (parent && depth < 10) {
                        const tag = parent.tagName.toLowerCase();
                        const cls = parent.className || '';
                        const role = parent.getAttribute('role') || '';
                        // Detect common menu/accordion/dropdown patterns
                        if (cls.match && cls.match(/(collapse|dropdown|menu|accordion|submenu|panel|nav-item)/i)) {
                            // Find the trigger (toggle button/link) for this container
                            const trigger = parent.previousElementSibling ||
                                            parent.parentElement?.querySelector(':scope > a, :scope > button');
                            if (trigger) {
                                const triggerText = (trigger.innerText || '').trim().substring(0, 40);
                                const triggerId = trigger.getAttribute('id') || '';
                                let triggerSel = '';
                                if (triggerId) triggerSel = '#' + triggerId;
                                else if (triggerText && triggerText.length <= 40) {
                                    const tTag = trigger.tagName.toLowerCase();
                                    triggerSel = `${tTag}:text-is("${triggerText}")`;
                                } else if (triggerText) {
                                    const tTag = trigger.tagName.toLowerCase();
                                    triggerSel = `${tTag}:has-text("${triggerText.substring(0,30)}")`;
                                }
                                if (triggerSel) return { text: triggerText, selector: triggerSel };
                            }
                        }
                        parent = parent.parentElement;
                        depth++;
                    }
                    return null;
                }

                els.forEach((el, i) => {
                    if (results.length > 40) return;
                    const tag = el.tagName.toLowerCase();
                    const type = el.getAttribute('type') || '';
                    const name = el.getAttribute('name') || '';
                    const id = el.getAttribute('id') || '';
                    const placeholder = el.getAttribute('placeholder') || '';
                    const text = (el.innerText || '').trim().substring(0, 80);
                    const role = el.getAttribute('role') || '';
                    const href = el.getAttribute('href') || '';
                    const visible = isVisible(el);

                    let selector = '';
                    if (id) selector = '#' + id;
                    else if (name) selector = `${tag}[name="${name}"]`;
                    else if (placeholder) selector = `${tag}[placeholder="${placeholder}"]`;
                    else if (text && text.length <= 40 && tag === 'button') selector = `button:text-is("${text}")`;
                    else if (text && text.length <= 40 && tag === 'a') selector = `a:text-is("${text}")`;
                    else if (text && tag === 'button') selector = `button:has-text("${text.substring(0,30)}")`;
                    else if (text && tag === 'a') selector = `a:has-text("${text.substring(0,30)}")`;
                    else selector = `${tag}:nth-of-type(${i+1})`;

                    const parentMenu = !visible ? getParentMenu(el) : null;

                    results.push({
                        tag, type, name, id, placeholder, text: text.substring(0, 60),
                        role, href: href.substring(0, 60), selector, visible,
                        parentMenu
                    });
                });
                return results;
            }""",
                scope,
            )

            if not elements:
                return f"No interactive elements found in '{scope}'."

            useful = [
                e
                for e in elements
                if e.get("text") or e.get("id") or e.get("name") or e.get("placeholder")
            ]
            visible_els = [e for e in useful if e.get("visible")]
            hidden_els = [
                e
                for e in useful
                if not e.get("visible") and (e.get("text") or e.get("id"))
            ]

            lines = [f"Found {len(visible_els)} visible + {len(hidden_els)} hidden:"]
            lines.append("VISIBLE:")

            for el in visible_els:
                label = (
                    el.get("text")
                    or el.get("id")
                    or el.get("name")
                    or el.get("placeholder")
                    or "?"
                )
                lines.append(f"  {label}  → {el['selector']}")

            if hidden_els:
                lines.append("HIDDEN:")
                for el in hidden_els:
                    label = el.get("text") or el.get("id") or "?"
                    pm = el.get("parentMenu")
                    expand = f'  [click "{pm["text"]}" first]' if pm else ""
                    lines.append(f"  {label}  → {el['selector']}{expand}")

            return "\n".join(lines)
        except Exception as exc:
            return f"Error listing elements: {exc}"


class BrowserWaitTool(BaseTool):

    name: str = "BrowserWaitTool"
    description: str = (
        "Wait in the browser. Pass a number for seconds (e.g. '2'), or a CSS "
        "selector to wait for that element to appear (e.g. '#dashboard', '.results')."
    )

    def _run(self, input: Any) -> str:
        raw = str(input).strip()
        try:
            seconds = float(raw)
            time.sleep(min(seconds, 30))
            return f"Waited {seconds:.1f}s."
        except ValueError:
            pass

        try:
            page = _BrowserConnection.get_page()
            page.wait_for_selector(raw, timeout=15000)
            return f"Element '{raw}' appeared."
        except Exception as exc:
            return f"Error waiting for '{raw}': {exc}"


class BrowserScreenshotTool(BaseTool):

    name: str = "BrowserScreenshotTool"
    description: str = (
        "Take a screenshot of the current browser page. Pass a filename "
        "(e.g. 'login_page') or leave empty for auto-naming. "
        "Screenshot is saved to the screenshots/ directory."
    )

    def _run(self, input: Any) -> str:
        name = str(input).strip() or f"browser_{int(time.time())}"
        if not name.endswith(".png"):
            name += ".png"
        path = SCREENSHOTS_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)

        try:
            page = _BrowserConnection.get_page()
            page.screenshot(path=str(path), full_page=False)
            return f"Screenshot saved: {path}"
        except Exception as exc:
            return f"Error taking screenshot: {exc}"
