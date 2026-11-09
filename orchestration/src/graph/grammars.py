from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Iterable, Optional

LOGGER = logging.getLogger(__name__)

_COMMON = r"""
ws        ::= [ \t\n]{0,8}
digits    ::= [0-9]+
string    ::= "\"" ( [^"\\] | "\\" ["\\bfnrt/] )* "\""
bool      ::= "true" | "false"
"""

ACTION_GRAMMAR = _COMMON + r"""
root         ::= ws action ws
action       ::= navigate | click | type-text | key-combo | read-page | scroll | open-app | web-search | read-url | write-content | done | fail
navigate     ::= "{\"action\":\"navigate\",\"url\":" string "}"
click        ::= "{\"action\":\"click\",\"target\":" string "}"
type-text    ::= "{\"action\":\"type_text\",\"selector\":" string ",\"text\":" string "}"
key-combo    ::= "{\"action\":\"key_combo\",\"combo\":" string "}"
read-page    ::= "{\"action\":\"read_page\"}"
scroll       ::= "{\"action\":\"scroll\",\"direction\":" string "}"
open-app     ::= "{\"action\":\"open_app\",\"name\":" string "}"
web-search   ::= "{\"action\":\"web_search\",\"query\":" string "}"
read-url     ::= "{\"action\":\"read_url\",\"url\":" string "}"
write-content ::= "{\"action\":\"write_content\",\"destination\":" string ",\"description\":" string "}"
done         ::= "{\"action\":\"done\",\"summary\":" string "}"
fail         ::= "{\"action\":\"fail\",\"reason\":" string "}"
"""

RESEARCH_GRAMMAR = _COMMON + r"""
root       ::= ws action ws
action     ::= web-search | read-url | done | fail
web-search ::= "{\"action\":\"web_search\",\"query\":" string "}"
read-url   ::= "{\"action\":\"read_url\",\"url\":" string "}"
done       ::= "{\"action\":\"done\",\"summary\":" string "}"
fail       ::= "{\"action\":\"fail\",\"reason\":" string "}"
"""

NAVIGATE_ONLY_GRAMMAR = _COMMON + r"""
root       ::= ws navigate ws
navigate   ::= "{\"action\":\"navigate\",\"url\":" string "}"
"""

TYPE_OR_CLICK_GRAMMAR = _COMMON + r"""
root         ::= ws action ws
action       ::= click | type-text | key-combo | read-page | web-search | read-url | write-content | done | fail
click        ::= "{\"action\":\"click\",\"target\":" string "}"
type-text    ::= "{\"action\":\"type_text\",\"selector\":" string ",\"text\":" string "}"
key-combo    ::= "{\"action\":\"key_combo\",\"combo\":" string "}"
read-page    ::= "{\"action\":\"read_page\"}"
web-search   ::= "{\"action\":\"web_search\",\"query\":" string "}"
read-url     ::= "{\"action\":\"read_url\",\"url\":" string "}"
write-content ::= "{\"action\":\"write_content\",\"destination\":" string ",\"description\":" string "}"
done         ::= "{\"action\":\"done\",\"summary\":" string "}"
fail         ::= "{\"action\":\"fail\",\"reason\":" string "}"
"""

CLICK_OR_SCROLL_GRAMMAR = _COMMON + r"""
root       ::= ws action ws
action     ::= click | scroll | read-page | web-search | read-url | done | fail
click      ::= "{\"action\":\"click\",\"target\":" string "}"
scroll     ::= "{\"action\":\"scroll\",\"direction\":" string "}"
read-page  ::= "{\"action\":\"read_page\"}"
web-search ::= "{\"action\":\"web_search\",\"query\":" string "}"
read-url   ::= "{\"action\":\"read_url\",\"url\":" string "}"
done       ::= "{\"action\":\"done\",\"summary\":" string "}"
fail       ::= "{\"action\":\"fail\",\"reason\":" string "}"
"""

OPEN_APP_ONLY_GRAMMAR = _COMMON + r"""
root       ::= ws action ws
action     ::= open-app | done | fail
open-app   ::= "{\"action\":\"open_app\",\"name\":" string "}"
done       ::= "{\"action\":\"done\",\"summary\":" string "}"
fail       ::= "{\"action\":\"fail\",\"reason\":" string "}"
"""

PLAN_GRAMMAR = _COMMON + r"""
root       ::= ws (chat | automation) ws
chat       ::= "{\"mode\":\"chat\",\"reply\":" string "}"
automation ::= "{\"mode\":\"automation\",\"steps\":[" ws step-list ws "]}"
step-list  ::= string (ws "," ws string)*
"""

AUTOMATION_STEPS_GRAMMAR = _COMMON + r"""
root       ::= ws "{\"steps\":[" ws step-list ws "]}" ws
step-list  ::= string (ws "," ws string)*
"""

REFLECT_GRAMMAR = _COMMON + r"""
root        ::= ws "{" ws "\"step_complete\":" ws bool ws "," ws "\"goal_complete\":" ws bool ws "," ws "\"adjust\":" ws adjust-val ws "," ws "\"new_steps\":[" ws step-list? ws "]" ws "," ws "\"reasoning\":" ws string ws "}" ws
adjust-val  ::= "\"none\"" | "\"insert\"" | "\"replace\"" | "\"skip\""
step-list   ::= string (ws "," ws string)*
"""

GENERIC_JSON_OBJECT_GRAMMAR = _COMMON + r"""
root   ::= ws object ws
object ::= "{" ws (pair (ws "," ws pair)*)? ws "}"
pair   ::= string ws ":" ws value
value  ::= object | array | string | number | bool | "null"
array  ::= "[" ws (value (ws "," ws value)*)? ws "]"
number ::= "-"? digits ("." digits)?
"""

SKILL_ROUTE_GRAMMAR = _COMMON + r"""
root   ::= ws "{" ws "\"skill\":" ws string ws "," ws "\"intent\":" ws string ws "," ws "\"params\":" ws object ws "}" ws
object ::= "{" ws (pair (ws "," ws pair)*)? ws "}"
pair   ::= string ws ":" ws string
"""


def build_skill_route_grammar_text(candidates: Optional[Iterable[object]] = None) -> str:
    if not candidates:
        return SKILL_ROUTE_GRAMMAR
    skill_names = ["none"]
    intent_names = ["none"]
    for s in candidates:
        s_name = getattr(s, "name", "")
        if s_name and s_name not in skill_names:
            skill_names.append(s_name)
        intents_dict = getattr(s, "intents", {}) or {}
        for i_name in intents_dict.keys():
            if i_name and i_name not in intent_names:
                intent_names.append(i_name)
    if len(skill_names) <= 1 or len(intent_names) <= 1:
        return SKILL_ROUTE_GRAMMAR

    skill_rule = " | ".join(f'"\\"{name}\\""' for name in skill_names)
    intent_rule = " | ".join(f'"\\"{name}\\""' for name in intent_names)
    return (
        _COMMON
        + r"""
root        ::= ws "{" ws "\"skill\":" ws skill-enum ws "," ws "\"intent\":" ws intent-enum ws "," ws "\"params\":" ws object ws "}" ws
object      ::= "{" ws (pair (ws "," ws pair)*)? ws "}"
pair        ::= string ws ":" ws string
"""
        + f"skill-enum  ::= {skill_rule}\n"
        + f"intent-enum ::= {intent_rule}\n"
    )


def grammar_for_skill_candidates(candidates: Optional[Iterable[object]] = None) -> Optional[object]:
    return load_grammar(build_skill_route_grammar_text(candidates))


@lru_cache(maxsize=32)
def _compile(grammar_text: str):
    from llama_cpp import LlamaGrammar

    return LlamaGrammar.from_string(grammar_text, verbose=False)


def load_grammar(grammar_text: str):
    try:
        return _compile(grammar_text)
    except Exception as exc:
        LOGGER.warning("Grammar compilation failed, falling back to unconstrained: %s", exc)
        return None


_OPEN_STEP = re.compile(r"^\s*open\s+(?:the\s+)?(.+?)\s*(?:app(?:lication)?)?\s*$", re.IGNORECASE)
_TYPING_STEP = re.compile(r"\b(type|enter|fill|log\s?in|write|compose|draft)\b", re.IGNORECASE)
_RESEARCH_STEP = re.compile(
    r"\b("
    r"search\s+(the\s+)?web|web\s*search|research|look\s?up|"
    r"search\s+google|google\s+search|google\s+it|"
    r"find\s+(out|information|info)"
    r")\b",
    re.IGNORECASE,
)
_ELEMENT_IDX_RE = re.compile(r"^\[(\d+)\]", re.MULTILINE)


def extract_element_indices(elements_str: str) -> tuple[str, ...]:
    if not elements_str:
        return ()
    seen: list[str] = []
    for raw in _ELEMENT_IDX_RE.findall(elements_str):
        num_str = str(int(raw))
        padded_str = f"{int(raw):02d}"
        for tok in (f"[{raw}]", f"[{num_str}]", f"[{padded_str}]"):
            if tok not in seen:
                seen.append(tok)
    return tuple(seen)


def _constrain_index_grammar(grammar_text: str, valid_indices: tuple[str, ...]) -> str:
    if not valid_indices:
        return grammar_text
    idx_rule = " | ".join(f'"\\"{idx}\\""' for idx in valid_indices)
    updated = grammar_text.replace(
        'click      ::= "{\\"action\\":\\"click\\",\\"target\\":" string "}"',
        'click      ::= "{\\"action\\":\\"click\\",\\"target\\":" idx-token "}"',
    ).replace(
        'click        ::= "{\\"action\\":\\"click\\",\\"target\\":" string "}"',
        'click        ::= "{\\"action\\":\\"click\\",\\"target\\":" idx-token "}"',
    ).replace(
        'type-text    ::= "{\\"action\\":\\"type_text\\",\\"selector\\":" string ",\\"text\\":" string "}"',
        'type-text    ::= "{\\"action\\":\\"type_text\\",\\"selector\\":" idx-token ",\\"text\\":" string "}"',
    )
    if "idx-token" in updated:
        updated += f"\nidx-token  ::= {idx_rule}\n"
    return updated


def _names_installed_app(step: str, installed_apps: Optional[Iterable[str]]) -> bool:
    match = _OPEN_STEP.match(step)
    if not match or not installed_apps:
        return False
    target = match.group(1).lower()
    return any(
        target == (a[:-4] if a.lower().endswith(".app") else a).strip().lower()
        for a in installed_apps
    )


def grammar_for_step(
    current_step: str,
    installed_apps: Optional[Iterable[str]] = None,
    valid_indices: Optional[Iterable[str]] = None,
) -> Optional[object]:
    step = (current_step or "").strip()
    indices_tuple = tuple(valid_indices) if valid_indices else ()
    if not step:
        return load_grammar(_constrain_index_grammar(ACTION_GRAMMAR, indices_tuple))
    if _names_installed_app(step, installed_apps):
        return load_grammar(OPEN_APP_ONLY_GRAMMAR)
    if _RESEARCH_STEP.search(step):
        return load_grammar(RESEARCH_GRAMMAR)
    lowered = step.lower()
    if lowered.startswith(("open", "navigate", "go to")):
        return load_grammar(_constrain_index_grammar(ACTION_GRAMMAR, indices_tuple))
    if _TYPING_STEP.search(lowered):
        return load_grammar(_constrain_index_grammar(TYPE_OR_CLICK_GRAMMAR, indices_tuple))
    if "click" in lowered and "navigate" not in lowered:
        return load_grammar(_constrain_index_grammar(CLICK_OR_SCROLL_GRAMMAR, indices_tuple))
    return load_grammar(_constrain_index_grammar(ACTION_GRAMMAR, indices_tuple))
