from __future__ import annotations

import pytest

import src.tools.research_tools as rt


def test_strip_tags_unescapes_and_collapses_whitespace():
    assert rt._strip_tags("<b>Hello & world</b>") == "Hello & world"
    assert rt._strip_tags("line\n  break") == "line break"


def test_unwrap_ddg_href_decodes_redirect():
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FApple&rut=abc"
    assert rt._unwrap_ddg_href(href) == "https://en.wikipedia.org/wiki/Apple"


def test_unwrap_ddg_href_passes_plain_urls_through():
    assert rt._unwrap_ddg_href("https://example.com/a?b=c") == "https://example.com/a?b=c"


def test_normalize_url_adds_scheme_to_protocol_relative():
    assert rt._normalize_url("//example.com/page") == "https://example.com/page"


_DDG_HTML = """
<div class="result results_links">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FApple">Apple - Wikipedia</a>
    <a rel="nofollow" class="result__a" href="https://www.apple.com">Apple</a>
  </h2>
  <a class="result__snippet" href="#">Apple Inc. is an American technology company.</a>
  <a class="result__snippet" href="#">Official site.</a>
</div>
"""

_DDG_LITE = """
<tr>
  <a rel="nofollow" href="https://example.com/one" class='result-link'>First Result</a>
</tr>
<tr><td class='result-snippet'>First snippet.</td></tr>
"""


def test_extract_results_parses_ddg_html_endpoint():
    results = rt._extract_results(
        _DDG_HTML, link_class="result__a", snippet_classes=("result__snippet",)
    )

    assert len(results) == 2
    assert results[0]["url"] == "https://en.wikipedia.org/wiki/Apple"
    assert results[0]["title"] == "Apple - Wikipedia"
    assert results[0]["snippet"] == "Apple Inc. is an American technology company."
    assert results[1]["url"] == "https://www.apple.com"
    assert results[1]["snippet"] == "Official site."


def test_extract_results_parses_lite_endpoint_with_attribute_order_swapped():
    results = rt._extract_results(
        _DDG_LITE, link_class="result-link", snippet_classes=("result-snippet",)
    )

    assert len(results) == 1
    assert results[0] == {
        "url": "https://example.com/one",
        "title": "First Result",
        "snippet": "First snippet.",
    }


def test_extract_results_skips_non_http_links():
    markup = '<a class="result__a" href="/local/path">Local</a>'
    results = rt._extract_results(
        markup, link_class="result__a", snippet_classes=("result__snippet",)
    )
    assert results == []


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        pass


def test_web_search_uses_lite_fallback_when_html_yields_nothing(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def fake_post(url, **kwargs):
        calls.append(url)
        if "html.duckduckgo.com" in url:
            return _FakeResponse("<html>garbage, no results</html>")
        return _FakeResponse(_DDG_LITE)

    monkeypatch.setattr(rt.requests, "post", fake_post)

    results = rt.web_search("test query")

    assert calls == [
        "https://html.duckduckgo.com/html/",
        "https://lite.duckduckgo.com/lite/",
    ]
    assert results[0]["title"] == "First Result"


def test_web_search_raises_when_both_endpoints_fail(monkeypatch: pytest.MonkeyPatch):
    def fake_post(url, **kwargs):
        raise rt.requests.ConnectionError("down")

    monkeypatch.setattr(rt.requests, "post", fake_post)

    with pytest.raises(RuntimeError, match="web search failed"):
        rt.web_search("test query")


def test_format_search_results_includes_title_url_snippet():
    formatted = rt.format_search_results(
        [{"url": "https://a.test", "title": "A", "snippet": "about a"}]
    )
    assert "[1] A" in formatted
    assert "https://a.test" in formatted
    assert "about a" in formatted


def test_clean_page_text_collapses_blank_runs_and_truncates():
    assert rt.clean_page_text("a\n\n\n\nb") == "a\nb"
    assert len(rt.clean_page_text("x" * 10000)) == rt.MAX_CONTENT_CHARS


def test_web_search_tool_returns_error_string_on_failure(monkeypatch: pytest.MonkeyPatch):
    def boom(query, max_results=rt.MAX_RESULTS):
        raise RuntimeError("no network")

    monkeypatch.setattr(rt, "web_search", boom)

    out = rt.WebSearchTool()._run("anything")

    assert out.startswith("Error:")
    assert "no network" in out


def test_web_search_tool_empty_query_is_rejected():
    assert rt.WebSearchTool()._run("   ").startswith("Error:")


def test_fetch_page_content_tool_rejects_non_http_input():
    assert rt.FetchPageContentTool()._run("not a url").startswith("Error:")


def test_fetch_page_content_tool_formats_result(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        rt, "_fetch_via_browser", lambda url: ("Example", "Body line one.\n\n\nBody line two.")
    )

    out = rt.FetchPageContentTool()._run("https://example.com")

    assert out.startswith("Title: Example")
    assert "URL: https://example.com" in out
    assert "Body line one.\nBody line two." in out


def test_fetch_page_content_tool_detects_bot_block(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        rt, "_fetch_via_browser", lambda url: ("Just a moment...", "Checking your browser")
    )

    out = rt.FetchPageContentTool()._run("https://blocked.test")

    assert out.startswith("Error:")
    assert "blocked" in out


def test_fetch_page_content_tool_reports_fetch_failure(monkeypatch: pytest.MonkeyPatch):
    def boom(url):
        raise RuntimeError("no browser")

    monkeypatch.setattr(rt, "_fetch_via_browser", boom)

    out = rt.FetchPageContentTool()._run("https://example.com")

    assert out.startswith("Error:")
    assert "no browser" in out
