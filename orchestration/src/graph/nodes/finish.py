from __future__ import annotations

import logging
from typing import Any

from src.graph.config import LokiState

LOGGER = logging.getLogger(__name__)


def _is_generic_completion_text(text: str) -> bool:
    cleaned = text.strip().rstrip(".!?").lower()
    if not cleaned:
        return True
    if cleaned in {
        "done",
        "task completed",
        "completed",
        "finished",
        "task complete",
        "all done",
        "all set! i've taken care of that for you",
    }:
        return True
    return (
        cleaned.startswith("done:")
        or cleaned.startswith("done. completed ")
        or cleaned.startswith("completed:")
        or cleaned.startswith("all set! i've pulled up the search results")
    )


def finish_node(state: LokiState) -> dict[str, Any]:
    from src.audio.tts.system import humanize_goal_completion
    from src.graph.nodes.reflect import (
        _goal_needs_spoken_answer,
        _synthesize_answer_or_complete,
    )

    history = state.get("history", [])
    goal = (state.get("goal") or "").strip()
    existing = (state.get("result") or "").strip()
    if existing and (not goal or not _is_generic_completion_text(existing)):
        msg = existing
    elif _goal_needs_spoken_answer(goal):
        msg = _synthesize_answer_or_complete(state, history)
    else:
        msg = humanize_goal_completion(goal, history=history)

    is_web_session = bool(state.get("current_url")) or any(h.get("url") for h in history)
    if not is_web_session:
        return {"result": msg}

    import os
    import threading

    def _run_site_learning() -> None:
        try:
            from src.graph.site_knowledge import SiteKnowledge

            sk = SiteKnowledge()
            sk.learn_from_session(
                goal=state.get("goal", ""),
                history=history,
                current_url=state.get("current_url", ""),
            )
        except Exception as exc:
            LOGGER.warning("SiteKnowledge learning failed: %s", exc)

    if os.getenv("PYTEST_CURRENT_TEST"):
        _run_site_learning()
    else:
        threading.Thread(
            target=_run_site_learning,
            daemon=True,
            name="loki-site-learn",
        ).start()

    return {"result": msg}
