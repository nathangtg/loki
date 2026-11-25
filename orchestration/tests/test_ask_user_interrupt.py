from __future__ import annotations

import threading
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from src.graph import checkpoint
from src.graph.nodes.act import _handle_ask_user
from src.server import config, pipeline
from src.server.job_store import evict_expired_jobs


class _S(TypedDict, total=False):
    next_action: dict
    interruptible: bool
    user_answer: str
    history: list


def _ask_node(state: _S) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    _handle_ask_user(state.get("next_action", {}), state, updates)
    return updates


def _mini_graph():
    from langgraph.checkpoint.memory import MemorySaver

    g = StateGraph(_S)
    g.add_node("ask", _ask_node)
    g.add_edge(START, "ask")
    g.add_edge("ask", END)
    return g.compile(checkpointer=MemorySaver())


def _cfg(job_id: str) -> dict:
    return {"configurable": {"thread_id": job_id}}


def _new_job(job_id: str) -> None:
    config.jobs[job_id] = {
        "status": "running",
        "result": None,
        "steps": [],
        "messages": [],
        "question": None,
    }


def test_ask_user_interrupts_and_resumes_with_answer():
    graph = _mini_graph()
    out = graph.invoke({"next_action": {"question": "Which file?"}, "interruptible": True}, _cfg("j1"))
    pending = out["__interrupt__"]
    assert pending[0].value == {"question": "Which file?"}

    final = graph.invoke(Command(resume="report.pdf"), _cfg("j1"))
    assert final["user_answer"] == "report.pdf"


def test_ask_user_without_interactive_mode_returns_empty_answer():
    updates: dict[str, Any] = {}
    msg = _handle_ask_user({"question": "q"}, {}, updates)
    assert updates["user_answer"] == ""
    assert "Cannot ask user" in msg


def test_ask_user_blocking_callback_still_used():
    updates: dict[str, Any] = {}
    _handle_ask_user({"question": "q"}, {"ask_user": lambda q: "yes"}, updates)
    assert updates["user_answer"] == "yes"


def test_pipeline_lock_released_while_waiting_and_resumed_with_answer(monkeypatch):
    job_id = "lock-job"
    _new_job(job_id)
    seen: dict[str, Any] = {}

    def fake_run(command, on_step=None, on_message=None, ask_user=None, job_id=None, interruptible=False):
        assert interruptible is True
        return {"status": "waiting_for_user", "question": "name?"}

    def fake_resume(jid, answer, on_step=None, on_message=None):
        seen["answer"] = answer
        return {"status": "ok", "message": f"hello {answer}"}

    monkeypatch.setattr(pipeline, "run_pipeline", fake_run)
    monkeypatch.setattr(pipeline, "resume_pipeline", fake_resume)

    t = threading.Thread(target=pipeline.run_pipeline_async, args=(job_id, "cmd"), kwargs={"client_handles_tts": True})
    t.start()
    deadline = time.time() + 5
    while config.jobs[job_id]["question"] is None and time.time() < deadline:
        time.sleep(0.01)
    assert config.jobs[job_id]["question"] == "name?"

    acquired = config.pipeline_lock.acquire(timeout=1)
    assert acquired, "pipeline_lock must be free while waiting for the user"
    config.pipeline_lock.release()

    config.jobs[job_id]["answer"] = "Loki"
    config.job_events[job_id].set()
    t.join(5)

    assert seen["answer"] == "Loki"
    assert config.jobs[job_id]["status"] == "done"


def test_timeout_deletes_checkpoint_and_errors_job(monkeypatch):
    job_id = "timeout-job"
    _new_job(job_id)
    deleted: list[str] = []
    monkeypatch.setattr(pipeline, "ASK_USER_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(pipeline, "delete_state", deleted.append)
    monkeypatch.setattr(
        pipeline,
        "run_pipeline",
        lambda *a, **k: {"status": "waiting_for_user", "question": "q"},
    )

    pipeline.run_pipeline_async(job_id, "cmd", client_handles_tts=True)

    assert job_id in deleted
    job = config.jobs[job_id]
    assert job["status"] == "error"
    assert job["question"] is None


def test_eviction_deletes_checkpoint(monkeypatch):
    deleted: list[str] = []
    monkeypatch.setattr(checkpoint, "delete_state", deleted.append)
    _new_job("old-job")
    config.jobs["old-job"]["finished_at"] = time.time() - config.JOB_TTL_SECONDS - 10

    assert evict_expired_jobs() >= 1
    assert "old-job" in deleted
    assert "old-job" not in config.jobs


def test_ask_user_timeout_default_and_env(monkeypatch):
    assert config.ASK_USER_TIMEOUT_SECONDS == 120 or "LOKI_ASK_USER_TIMEOUT_SECONDS" in __import__("os").environ
    monkeypatch.setenv("LOKI_ASK_USER_TIMEOUT_SECONDS", "7")
    assert config._env_int("LOKI_ASK_USER_TIMEOUT_SECONDS", 120) == 7


def test_checkpoint_save_load_delete_round_trip():
    checkpoint.delete_state("rt-job")
    assert checkpoint.load_state("rt-job") is None
    checkpoint.save_state("rt-job", {"goal": "g", "turn": 3})
    loaded = checkpoint.load_state("rt-job")
    assert loaded and loaded["goal"] == "g" and loaded["turn"] == 3
    checkpoint.delete_state("rt-job")
    assert checkpoint.load_state("rt-job") is None
