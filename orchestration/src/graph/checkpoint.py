from __future__ import annotations

import contextlib
import contextvars
from typing import Any, Callable, Iterator

from langgraph.checkpoint.memory import MemorySaver

_CHECKPOINTER = MemorySaver()
_CALLBACKS: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("loki_callbacks", default={})
CALLBACK_KEYS = ("on_step", "on_message", "ask_user")


def get_checkpointer() -> MemorySaver:
    return _CHECKPOINTER


def thread_config(job_id: str, **extra: Any) -> dict[str, Any]:
    return {"configurable": {"thread_id": job_id}, **extra}


def _graph():
    from src.graph import build_loki_graph

    return build_loki_graph()


def save_state(job_id: str, values: dict[str, Any]) -> None:
    _graph().update_state(thread_config(job_id), values)


def load_state(job_id: str) -> dict[str, Any] | None:
    snapshot = _graph().get_state(thread_config(job_id))
    if snapshot is None or not snapshot.values:
        return None
    return dict(snapshot.values)


def delete_state(job_id: str) -> None:
    _CHECKPOINTER.delete_thread(job_id)


@contextlib.contextmanager
def runtime_callbacks(**callbacks: Callable[..., Any] | None) -> Iterator[None]:
    token = _CALLBACKS.set({k: v for k, v in callbacks.items() if v is not None})
    try:
        yield
    finally:
        _CALLBACKS.reset(token)


def inject_callbacks(state: Any) -> dict[str, Any]:
    merged = dict(state)
    for key, fn in _CALLBACKS.get().items():
        if merged.get(key) is None:
            merged[key] = fn
    return merged
