from __future__ import annotations

import logging
import re
import threading

LOGGER = logging.getLogger(__name__)

_compressor = None
_compressor_lock = threading.Lock()
_compressor_failed = False


def _get_compressor():
    global _compressor, _compressor_failed
    if _compressor is not None or _compressor_failed:
        return _compressor

    with _compressor_lock:
        if _compressor is not None or _compressor_failed:
            return _compressor
        try:
            from llmlingua import PromptCompressor

            _compressor = PromptCompressor(
                model_name="microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
                use_llmlingua2=True,
            )
            LOGGER.info("prompt_utils: llmlingua-2 compressor loaded")
        except Exception as exc:
            LOGGER.warning("prompt_utils: llmlingua unavailable, compression disabled: %s", exc)
            _compressor_failed = True
    return _compressor


def compress_page_text(text: str, ratio: float = 0.5, min_chars: int = 400) -> str:
    if len(text) < min_chars:
        return text

    compressor = _get_compressor()
    if compressor is None:
        return text

    try:
        result = compressor.compress_prompt(text, rate=ratio)
        compressed = result.get("compressed_prompt", "") if isinstance(result, dict) else str(result)
        if compressed.strip():
            LOGGER.debug(
                "prompt_utils: compressed page_text %d → %d chars", len(text), len(compressed)
            )
            return compressed
    except Exception as exc:
        LOGGER.debug("prompt_utils: compression failed, using original text: %s", exc)
    return text


_STOPWORDS = frozenset(
    {
        "the", "a", "an", "to", "in", "on", "of", "for", "and", "or", "with", "into",
        "click", "press", "tap", "select", "open", "type", "enter", "fill", "write",
        "button", "field", "input", "link", "section", "page", "app", "application",
    }
)
_TYPING_INTENT_RE = re.compile(r"\b(type|enter|fill|write|input|search|login|log\s+in)\b", re.I)
_CLICK_INTENT_RE = re.compile(r"\b(click|press|tap|select|open|choose|submit)\b", re.I)
_INPUT_ROLES = frozenset({"textfield", "textarea", "combobox", "input", "searchfield"})
_ACTION_ROLES = frozenset({"button", "link", "a", "menuitem", "tab", "toolbarbutton", "popUpbutton", "checkbox", "radiobutton"})


def _tokenize_grounding(text: str) -> list[str]:
    return [
        tok
        for tok in re.findall(r"[a-z0-9+#._-]+", (text or "").lower())
        if tok and tok not in _STOPWORDS
    ]


def compute_lexical_role_scores(
    elements: list[str],
    current_step: str,
    goal: str,
) -> tuple[list[float], bool]:
    step_tokens = set(_tokenize_grounding(current_step))
    goal_tokens = set(_tokenize_grounding(goal)) - step_tokens
    wants_input = bool(_TYPING_INTENT_RE.search(current_step or ""))
    wants_click = bool(_CLICK_INTENT_RE.search(current_step or "")) and not wants_input

    scores: list[float] = []
    has_exact_hit = False

    for line in elements:
        disp = _element_display_text(line).lower()
        el_tokens = set(_tokenize_grounding(disp))
        role_m = re.search(r"<([a-z0-9_:-]+)", line.lower())
        role_tag = (role_m.group(1).split(":")[0] if role_m else "").strip()

        score = 0.0
        if step_tokens:
            overlap = len(step_tokens & el_tokens) / len(step_tokens)
            score += 0.65 * overlap
            step_phrase = " ".join(_tokenize_grounding(current_step))
            if step_phrase and step_phrase in disp:
                score += 0.25
                if overlap >= 1.0:
                    has_exact_hit = True
        if goal_tokens and el_tokens:
            score += 0.15 * (len(goal_tokens & el_tokens) / len(goal_tokens))

        if wants_input and role_tag in _INPUT_ROLES:
            score += 0.20
        elif wants_click and role_tag in _ACTION_ROLES:
            score += 0.15
        if "[static]" in line:
            score -= 0.20

        scores.append(max(0.0, score))

    return scores, has_exact_hit


def rank_elements(
    elements: list[str],
    current_step: str,
    goal: str,
    top_k: int = 10,
    use_fast_gate: bool = True,
) -> list[str]:
    if len(elements) <= top_k:
        return elements

    query_text = f"{current_step} {goal}".strip()
    if not query_text:
        return elements[:top_k]

    lex_scores, has_exact_hit = compute_lexical_role_scores(elements, current_step, goal)
    if use_fast_gate and has_exact_hit:
        ranked_lex = sorted(range(len(elements)), key=lambda i: lex_scores[i], reverse=True)
        return [elements[i] for i in ranked_lex[:top_k]]

    try:
        from src.graph.vector_store import embed_query, embed_documents
        import numpy as np

        query_vec = np.asarray(embed_query(query_text), dtype=np.float32)
        query_norm = float(np.linalg.norm(query_vec)) or 1.0

        el_texts = [_element_display_text(el) for el in elements]
        el_vecs = np.asarray(embed_documents(el_texts), dtype=np.float32)
        el_norms = np.linalg.norm(el_vecs, axis=1)
        el_norms = np.where(el_norms == 0, 1.0, el_norms)

        sims = (el_vecs @ query_vec) / (el_norms * query_norm)
        n = len(elements)
        dense_order = np.argsort(sims)[::-1]
        lex_order = np.argsort(np.asarray(lex_scores, dtype=np.float32))[::-1]

        rrf_scores = np.zeros(n, dtype=np.float32)
        rrf_k = 10.0
        for rank_idx, el_idx in enumerate(dense_order):
            rrf_scores[int(el_idx)] += 0.65 / (rrf_k + rank_idx + 1.0)
        for rank_idx, el_idx in enumerate(lex_order):
            if lex_scores[int(el_idx)] > 0:
                rrf_scores[int(el_idx)] += 0.35 / (rrf_k + rank_idx + 1.0)

        top_indices = np.argsort(rrf_scores)[::-1][:top_k]
        return [elements[int(idx)] for idx in top_indices]
    except Exception as exc:
        LOGGER.debug("rank_elements: embedding failed, falling back to lexical rank: %s", exc)
        ranked_lex = sorted(range(len(elements)), key=lambda i: lex_scores[i], reverse=True)
        return [elements[i] for i in ranked_lex[:top_k]]


def narrow_elements_iterative(
    elements: list[str],
    current_step: str,
    goal: str,
    top_k: int = 10,
    group_size: int = 15,
    use_fast_gate: bool = True,
) -> list[str]:
    if len(elements) <= group_size:
        return rank_elements(elements, current_step, goal, top_k=top_k, use_fast_gate=use_fast_gate)

    query_text = f"{current_step} {goal}".strip()
    if not query_text:
        return elements[:top_k]

    if use_fast_gate:
        lex_scores, has_exact_hit = compute_lexical_role_scores(elements, current_step, goal)
        if has_exact_hit:
            ranked_lex = sorted(range(len(elements)), key=lambda i: lex_scores[i], reverse=True)
            return [elements[i] for i in ranked_lex[:top_k]]

    groups = [elements[i : i + group_size] for i in range(0, len(elements), group_size)]

    try:
        from src.graph.vector_store import embed_query, embed_documents
        import numpy as np

        query_vec = np.asarray(embed_query(query_text), dtype=np.float32)
        query_norm = float(np.linalg.norm(query_vec)) or 1.0

        group_texts = [
            " | ".join(_element_display_text(el) for el in group)
            for group in groups
        ]
        group_vecs = np.asarray(embed_documents(group_texts), dtype=np.float32)
        group_norms = np.linalg.norm(group_vecs, axis=1)
        group_norms = np.where(group_norms == 0, 1.0, group_norms)

        scores = (group_vecs @ query_vec) / (group_norms * query_norm)
        best_idx = int(np.argmax(scores))
        best_score = float(scores[best_idx])
        best_group = groups[best_idx]

        LOGGER.debug(
            "narrow_elements_iterative: coarse pass selected group of %d/%d elements (score=%.3f)",
            len(best_group), len(elements), best_score,
        )
        return rank_elements(best_group, current_step, goal, top_k=top_k)
    except Exception as exc:
        LOGGER.debug("narrow_elements_iterative: coarse pass failed, falling back to flat rank: %s", exc)
        return rank_elements(elements, current_step, goal, top_k=top_k)


def _element_display_text(line: str) -> str:
    text = re.sub(r"^\[\d+\]\s*(\[static\]\s*)?", "", line)
    text = re.sub(r"^<[^>]+>\s*", "", text)
    return text.strip() or line


def compress_elements(elements: list[str]) -> list[str]:
    out = []
    for line in elements:
        m = re.match(r"^(\[\d+\](?:\s*\[static\])?)\s*<(\w+)([^>]*)>\s*(.*)$", line)
        if not m:
            out.append(line)
            continue
        prefix, tag, attrs, trailing = m.groups()

        type_m = re.search(r"type=['\"]?([\w-]+)['\"]?", attrs)
        etype = type_m.group(1) if type_m else ""

        label = trailing.strip()
        if not label or label == "(no text)":
            for attr_name in ("placeholder", "aria-label", "value", "name"):
                am = re.search(rf"{attr_name}=['\"]?([^'\">]+)['\"]?", attrs)
                if am:
                    label = am.group(1)
                    break

        tag_repr = f"{tag}:{etype}" if etype else tag
        label_repr = f' "{label[:60]}"' if label else ""
        out.append(f"{prefix} {tag_repr}{label_repr}")
    return out
