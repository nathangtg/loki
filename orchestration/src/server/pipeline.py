from __future__ import annotations

import threading
import time
from typing import Any, Callable

from src.graph.checkpoint import delete_state
from src.server.config import (
    ASK_USER_TIMEOUT_SECONDS,
    LOGGER,
    MAX_MESSAGES_PER_JOB,
    MAX_STEPS_PER_JOB,
    get_system_tts_adapter,
    job_done_events,
    job_events,
    jobs,
    jobs_lock,
    pipeline_lock,
)
from src.server.job_store import append_bounded


def _speak_system_async(text: str, session_id: str, goal: str = "") -> None:
    from src.audio.tts.system import format_for_speech

    segments = format_for_speech(text, goal=goal)
    if not segments:
        return

    def _runner() -> None:
        try:
            adapter = get_system_tts_adapter()
            if not adapter.is_loaded:
                adapter.load()
            for segment in segments:
                handle = adapter.synthesize(segment, session_id)
                handle.wait()
        except Exception as exc:
            LOGGER.debug("pipeline: system TTS playback stopped or failed for %s: %s", session_id, exc)

    threading.Thread(
        target=_runner,
        daemon=True,
        name=f"loki-tts-{session_id[:8]}",
    ).start()


def stop_system_speech() -> bool:
    try:
        adapter = get_system_tts_adapter()
        if adapter.is_loaded:
            return adapter.cancel()
    except Exception as exc:
        LOGGER.debug("pipeline: stop_system_speech failed: %s", exc)
    return False


def run_pipeline(
    command: str,
    on_step: Callable[..., Any] | None = None,
    on_message: Callable[..., Any] | None = None,
    ask_user: Callable[..., Any] | None = None,
    job_id: str | None = None,
    interruptible: bool = False,
) -> dict[str, Any]:
    from src.graph import run_graph

    return run_graph(
        command,
        on_step=on_step,
        on_message=on_message,
        ask_user=ask_user,
        job_id=job_id,
        interruptible=interruptible,
    )


def resume_pipeline(
    job_id: str,
    answer: str,
    on_step: Callable[..., Any] | None = None,
    on_message: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    from src.graph import resume_graph

    return resume_graph(job_id, answer, on_step=on_step, on_message=on_message)


def run_pipeline_async(
    job_id: str,
    command: str,
    *,
    speak_response: bool = False,
    client_handles_tts: bool = False,
) -> None:
    def _on_step(action: str, target: str, result: str) -> None:
        with jobs_lock:
            job = jobs.get(job_id)
            if job is not None:
                append_bounded(
                    job["steps"],
                    {
                        "action": action,
                        "target": target[:80],
                        "result": result[:120],
                    },
                    MAX_STEPS_PER_JOB,
                )

    def _on_message(msg_type: str, content: str) -> None:
        with jobs_lock:
            job = jobs.get(job_id)
            if job is not None:
                append_bounded(
                    job["messages"],
                    {
                        "type": msg_type,
                        "content": content,
                    },
                    MAX_MESSAGES_PER_JOB,
                )

    def _wait_for_answer(question: str) -> str | None:
        """Block (without holding pipeline_lock) until the user answers.

        Returns None on timeout or if the job was evicted while waiting.
        """
        event = threading.Event()
        job_events[job_id] = event

        with jobs_lock:
            job = jobs.get(job_id)
            if job is None:
                job_events.pop(job_id, None)
                return None
            job["question"] = question
            job["answer"] = ""
            append_bounded(
                job["messages"],
                {"type": "question", "content": question},
                MAX_MESSAGES_PER_JOB,
            )
            done_ev = job_done_events.get(job_id)
            if done_ev is not None:
                done_ev.set()

        LOGGER.info("Pipeline asking user: %s", question)
        if not client_handles_tts:
            _speak_system_async(question, job_id)
        answered = event.wait(timeout=ASK_USER_TIMEOUT_SECONDS)
        if not client_handles_tts:
            stop_system_speech()

        with jobs_lock:
            job = jobs.get(job_id)
            job_events.pop(job_id, None)
            if job is None:
                return None
            if not answered:
                job["question"] = None
                return None
            answer = job.get("answer", "") or ""
            if answer:
                append_bounded(
                    job["messages"],
                    {"type": "answer", "content": answer},
                    MAX_MESSAGES_PER_JOB,
                )
        LOGGER.info("User answered: %s", answer[:60])
        return answer

    try:
        with pipeline_lock:
            result = run_pipeline(
                command,
                on_step=_on_step,
                on_message=_on_message,
                job_id=job_id,
                interruptible=True,
            )
        while isinstance(result, dict) and result.get("status") == "waiting_for_user":
            answer = _wait_for_answer(str(result.get("question", "")))
            if answer is None:
                delete_state(job_id)
                with jobs_lock:
                    still_tracked = job_id in jobs
                if not still_tracked:
                    return
                result = {"status": "timeout", "message": "Timed out waiting for your answer."}
                break
            with pipeline_lock:
                result = resume_pipeline(job_id, answer, on_step=_on_step, on_message=_on_message)

        timed_out = isinstance(result, dict) and result.get("status") == "timeout"
        if not timed_out and isinstance(result, dict):
            from src.audio.tts.system import format_for_speech

            raw_msg = (result.get("message") or "").strip()
            spoken_segments = format_for_speech(raw_msg, goal=command) if raw_msg else []
            if spoken_segments:
                result["spoken_message"] = " ".join(spoken_segments)
                result["message"] = result["spoken_message"]
        _finish_job(job_id, "error" if timed_out else "done", result)
        if not timed_out and speak_response and not client_handles_tts:
            reply_msg = (result or {}).get("message", "") or ""
            if reply_msg.strip():
                _speak_system_async(reply_msg, job_id, goal=command)
    except Exception as exc:
        LOGGER.exception("Pipeline failed for job %s", job_id)
        delete_state(job_id)
        _finish_job(job_id, "error", {"status": "error", "message": str(exc)})


def _finish_job(job_id: str, status: str, result: Any) -> None:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return
        job["status"] = status
        job["finished_at"] = time.time()
        job["result"] = result
        done_ev = job_done_events.get(job_id)
        if done_ev is not None:
            done_ev.set()
