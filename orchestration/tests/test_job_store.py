from __future__ import annotations

import threading
import types

import pytest

from src.server import config, job_store


@pytest.fixture(autouse=True)
def _clean_state():
    config.jobs.clear()
    config.job_events.clear()
    config.job_done_events.clear()
    yield
    config.jobs.clear()
    config.job_events.clear()
    config.job_done_events.clear()


def _add(job_id: str, finished_at: float | None) -> None:
    config.jobs[job_id] = {"status": "done" if finished_at else "running", "finished_at": finished_at}
    config.job_events[job_id] = object()
    config.job_done_events[job_id] = object()


def test_append_bounded_drops_oldest():
    items: list[int] = []
    for i in range(10):
        job_store.append_bounded(items, i, 3)
    assert items == [7, 8, 9]


def test_ttl_evicts_finished_jobs_and_events(monkeypatch):
    monkeypatch.setattr(config, "JOB_TTL_SECONDS", 100)
    monkeypatch.setattr(config, "MAX_RETAINED_JOBS", 1000)
    _add("old", 1000.0)
    _add("fresh", 1950.0)
    _add("running", None)
    removed = job_store.evict_expired_jobs(now=2000.0)
    assert removed == 1
    assert "old" not in config.jobs
    assert "old" not in config.job_events
    assert "old" not in config.job_done_events
    assert "fresh" in config.jobs
    assert "running" in config.jobs


def test_cap_evicts_oldest_finished_but_never_running(monkeypatch):
    monkeypatch.setattr(config, "JOB_TTL_SECONDS", 10**9)
    monkeypatch.setattr(config, "MAX_RETAINED_JOBS", 2)
    _add("running", None)
    _add("a", 1.0)
    _add("b", 2.0)
    _add("c", 3.0)
    job_store.evict_expired_jobs(now=10.0)
    assert set(config.jobs) == {"running", "c"}
    assert set(config.job_done_events) == {"running", "c"}


def test_env_int_fallback(monkeypatch, caplog):
    monkeypatch.setenv("LOKI_TEST_INT", "42")
    assert config._env_int("LOKI_TEST_INT", 7) == 42
    for bad in ("abc", "0", "-5"):
        monkeypatch.setenv("LOKI_TEST_INT", bad)
        assert config._env_int("LOKI_TEST_INT", 7) == 7
    monkeypatch.delenv("LOKI_TEST_INT")
    assert config._env_int("LOKI_TEST_INT", 7) == 7


def test_pipeline_bounds_steps_and_messages(monkeypatch):
    from src.server import pipeline

    monkeypatch.setattr(pipeline, "MAX_STEPS_PER_JOB", 3)
    monkeypatch.setattr(pipeline, "MAX_MESSAGES_PER_JOB", 2)

    def fake_run(command, on_step=None, on_message=None, ask_user=None, job_id=None, interruptible=False):
        for i in range(10):
            on_step("a", str(i), "r")
            on_message("info", str(i))
        return {"message": ""}

    monkeypatch.setattr(pipeline, "run_pipeline", fake_run)
    config.jobs["j"] = {"status": "running", "result": None, "steps": [], "messages": [], "question": None}
    pipeline.run_pipeline_async("j", "cmd", client_handles_tts=True)
    job = config.jobs["j"]
    assert [s["target"] for s in job["steps"]] == ["7", "8", "9"]
    assert [m["content"] for m in job["messages"]] == ["8", "9"]
    assert job["status"] == "done"
    assert isinstance(job["finished_at"], float)


def test_ask_user_timeout_clears_question(monkeypatch):
    from src.server import pipeline

    class _NoWait:
        def __init__(self):
            pass

        def wait(self, timeout=None):
            return False

        def set(self):
            pass

    monkeypatch.setattr(pipeline, "threading", types.SimpleNamespace(Event=_NoWait, Thread=threading.Thread))

    def fake_run(command, on_step=None, on_message=None, ask_user=None, job_id=None, interruptible=False):
        return {"status": "waiting_for_user", "question": "what?"}

    monkeypatch.setattr(pipeline, "run_pipeline", fake_run)
    config.jobs["j"] = {"status": "running", "result": None, "steps": [], "messages": [], "question": None}
    pipeline.run_pipeline_async("j", "cmd", client_handles_tts=True)
    assert config.jobs["j"]["question"] is None


def test_not_found_message_names_job_id():
    from src.server.handler import _job_not_found

    assert "abc-123" in _job_not_found("abc-123")["message"]
