from __future__ import annotations

import threading
from typing import Any, Callable

from src.server.config import (
    LOGGER,
    get_system_tts_adapter,
    job_done_events,
    job_events,
    jobs,
    jobs_lock,
    pipeline_lock,
)


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
) -> dict[str, Any]:
    from src.graph import run_graph

    return run_graph(
        command,
        on_step=on_step,
        on_message=on_message,
        ask_user=ask_user,
    )


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
                job["steps"].append(
                    {
                        "action": action,
                        "target": target[:80],
                        "result": result[:120],
                    }
                )

    def _on_message(msg_type: str, content: str) -> None:
        with jobs_lock:
            job = jobs.get(job_id)
            if job is not None:
                job["messages"].append(
                    {
                        "type": msg_type,
                        "content": content,
                    }
                )

    def _ask_user(question: str) -> str:
        event = threading.Event()
        job_events[job_id] = event

        with jobs_lock:
            job = jobs.get(job_id)
            if job is not None:
                job["question"] = question
                job["messages"].append({"type": "question", "content": question})
            done_ev = job_done_events.get(job_id)
            if done_ev is not None:
                done_ev.set()

        LOGGER.info("Pipeline asking user: %s", question)
        if not client_handles_tts:
            _speak_system_async(question, job_id)
        event.wait(timeout=120)
        if not client_handles_tts:
            stop_system_speech()

        with jobs_lock:
            job = jobs.get(job_id)
            answer = job.get("answer", "") if job else ""
            if job and answer:
                job["messages"].append({"type": "answer", "content": answer})

        job_events.pop(job_id, None)
        LOGGER.info("User answered: %s", answer[:60])
        return answer

    with pipeline_lock:
        try:
            result = run_pipeline(
                command,
                on_step=_on_step,
                on_message=_on_message,
                ask_user=_ask_user,
            )
            if isinstance(result, dict):
                from src.audio.tts.system import format_for_speech

                raw_msg = (result.get("message") or "").strip()
                spoken_segments = format_for_speech(raw_msg, goal=command) if raw_msg else []
                if spoken_segments:
                    result["spoken_message"] = " ".join(spoken_segments)
                    result["message"] = result["spoken_message"]
            with jobs_lock:
                jobs[job_id]["status"] = "done"
                jobs[job_id]["result"] = result
                done_ev = job_done_events.get(job_id)
                if done_ev is not None:
                    done_ev.set()
            if speak_response and not client_handles_tts:
                reply_msg = (result or {}).get("message", "") or ""
                if reply_msg.strip():
                    _speak_system_async(reply_msg, job_id, goal=command)
        except Exception as exc:
            LOGGER.exception("Pipeline failed for job %s", job_id)
            with jobs_lock:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["result"] = {"status": "error", "message": str(exc)}
                done_ev = job_done_events.get(job_id)
                if done_ev is not None:
                    done_ev.set()
