from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler
from typing import Any

from src.server.config import (
    LOGGER,
    get_stt_engine,
    get_voice_manager,
    jobs,
    jobs_lock,
    job_events,
    job_done_events,
)
from src.server.pipeline import run_pipeline_async, stop_system_speech
from src.voice.exceptions import VoiceError
from src.voice.session import MAX_AUDIO_CHUNK_BYTES

VOICE_LOGGER = LOGGER.getChild("voice.http")


class LokiHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        path, query = self._split_path()
        if path == "/assist":
            return self._handle_assist()
        if path == "/assist/audio":
            return self._handle_assist_audio()
        if path == "/tts/stop":
            stop_system_speech()
            return self._respond(200, {"status": "ok"})
        if path.startswith("/reply/"):
            return self._handle_reply()
        if path == "/voice/session":
            return self._handle_voice_create_session()
        voice_match = self._match_voice_session_path(path)
        if voice_match:
            session_id, action = voice_match
            if action == "audio":
                return self._handle_voice_push_audio(session_id, query)
            if action == "cancel":
                return self._handle_voice_cancel(session_id)
            if action == "reply":
                return self._handle_voice_reply(session_id)
        self._respond(404, {"status": "error", "message": "Not found"})

    def _split_path(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urllib.parse.urlsplit(self.path)
        return parsed.path, urllib.parse.parse_qs(parsed.query)

    @staticmethod
    def _match_voice_session_path(path: str) -> tuple[str, str] | None:
        prefix = "/voice/session/"
        if not path.startswith(prefix):
            return None
        rest = path[len(prefix):]
        if "/" not in rest:
            return None
        session_id, action = rest.split("/", 1)
        if not session_id or "/" in action:
            return None
        return session_id, action

    def _handle_assist(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._respond(400, {"status": "error", "message": "Invalid JSON"})
            return

        command = body.get("command", "").strip()
        if not command:
            self._respond(400, {"status": "error", "message": "Empty command"})
            return

        job_id = str(uuid.uuid4())
        with jobs_lock:
            job_done_events[job_id] = threading.Event()
            jobs[job_id] = {
                "status": "running",
                "result": None,
                "steps": [],
                "messages": [],
                "question": None,
            }

        LOGGER.info("Accepted job %s: %s", job_id, command)
        client_tts = self.headers.get("X-Loki-Client-TTS", "").strip() in ("1", "true", "yes")
        stop_system_speech()
        threading.Thread(
            target=run_pipeline_async,
            args=(job_id, command),
            kwargs={"client_handles_tts": client_tts},
            daemon=True,
            name=f"loki-pipeline-{job_id[:8]}",
        ).start()

        self._respond(202, {"status": "accepted", "job_id": job_id})

    def _handle_assist_audio(self):
        content_type = self.headers.get("Content-Type", "")

        try:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                self._respond(400, {"status": "error", "message": "Empty request body"})
                return

            if "multipart/form-data" in content_type:
                audio_data = self._extract_multipart_audio(length)
            else:
                audio_data = self.rfile.read(length)

            if not audio_data:
                self._respond(400, {"status": "error", "message": "No audio data received"})
                return

            ext = ".wav"
            if "mp3" in content_type:
                ext = ".mp3"
            elif "flac" in content_type:
                ext = ".flac"

            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(audio_data)
                wav_path = tmp.name

            transcription = ""
            metrics = None
            try:
                from src.voice.wav_utils import (
                    VOICE_SAMPLE_RATE_HZ,
                    read_wav_as_16k_mono_f32,
                )

                engine = get_stt_engine()
                if not engine.is_loaded:
                    engine.load()

                audio_samples = read_wav_as_16k_mono_f32(wav_path)
                session = engine.create_session(f"assist-audio-{uuid.uuid4().hex[:8]}")
                chunk_samples = int(VOICE_SAMPLE_RATE_HZ * 0.1)
                for i in range(0, len(audio_samples), chunk_samples):
                    session.add_audio(audio_samples[i : i + chunk_samples])
                transcript = session.finalize()
                transcription = transcript.text
                metrics = session.get_metrics()
                engine.release_session(session.session_id)
                LOGGER.info(
                    "Transcribed audio via Speculative STT (savings=%.1f%%, verified=%s): %s",
                    metrics.compute_savings_pct,
                    metrics.verification_triggered,
                    transcription[:80],
                )
            except Exception as stt_err:
                LOGGER.warning(
                    "Speculative STT failed, falling back to LLM audio: %s",
                    stt_err,
                )
                from src.graph.llm import call_llm_audio

                transcription = call_llm_audio(wav_path)
            finally:
                if os.path.exists(wav_path):
                    os.unlink(wav_path)

            command = transcription.strip()
            if not command:
                self._respond(400, {"status": "error", "message": "Could not transcribe audio"})
                return

            job_id = str(uuid.uuid4())
            with jobs_lock:
                job_done_events[job_id] = threading.Event()
                jobs[job_id] = {
                    "status": "running",
                    "result": None,
                    "steps": [],
                    "messages": [{"type": "transcription", "content": command}],
                    "question": None,
                }

            LOGGER.info("Accepted audio job %s: %s", job_id, command[:80])
            client_tts = self.headers.get("X-Loki-Client-TTS", "").strip() in ("1", "true", "yes")
            stop_system_speech()
            threading.Thread(
                target=run_pipeline_async,
                args=(job_id, command),
                kwargs={"speak_response": True, "client_handles_tts": client_tts},
                daemon=True,
                name=f"loki-pipeline-{job_id[:8]}",
            ).start()

            resp_payload: dict[str, Any] = {
                "status": "accepted",
                "job_id": job_id,
                "transcription": command,
            }
            if metrics:
                resp_payload["stt_metrics"] = {
                    "compute_savings_pct": round(metrics.compute_savings_pct, 1),
                    "verification_triggered": metrics.verification_triggered,
                    "mean_entropy": round(metrics.mean_entropy, 3),
                    "total_chunks": metrics.total_chunks,
                }

            self._respond(202, resp_payload)

        except Exception as exc:
            LOGGER.exception("Audio assist failed")
            self._respond(500, {"status": "error", "message": str(exc)})

    def _extract_multipart_audio(self, content_length: int) -> bytes | None:
        raw = self.rfile.read(content_length)
        content_type = self.headers.get("Content-Type", "")

        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary=") :].strip('"')
                break

        if not boundary:
            return raw

        boundary_bytes = boundary.encode()
        parts = raw.split(b"--" + boundary_bytes)

        for part in parts:
            if b'name="audio"' in part or b'name="audio"' in part:
                header_end = part.find(b"\r\n\r\n")
                if header_end != -1:
                    body = part[header_end + 4 :]
                    if body.endswith(b"\r\n"):
                        body = body[:-2]
                    if body.endswith(b"--"):
                        body = body[:-2]
                    if body.endswith(b"\r\n"):
                        body = body[:-2]
                    return body

        return None

    def _handle_reply(self):
        job_id = self.path[len("/reply/") :]
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._respond(400, {"status": "error", "message": "Invalid JSON"})
            return

        reply_text = body.get("reply", "").strip()
        with jobs_lock:
            job = jobs.get(job_id)
            if job is None:
                self._respond(404, {"status": "error", "message": "Job not found"})
                return
            job["answer"] = reply_text
            job["question"] = None
            job["messages"].append({"type": "answer", "content": reply_text})

        event = job_events.get(job_id)
        if event:
            event.set()

        self._respond(200, {"status": "ok"})

    _VOICE_ERROR_STATUS = {
        "voice.session_not_found": 404,
        "voice.session_expired": 410,
        "voice.session_limit": 429,
        "voice.backpressure": 429,
        "voice.body_too_large": 413,
        "voice.audio_gap": 409,
        "voice.invalid_state": 409,
    }

    def _respond_voice_error(self, exc: VoiceError) -> None:
        status = self._VOICE_ERROR_STATUS.get(exc.code, 400)
        self._respond(status, {"status": "error", "code": exc.code, "message": exc.message})

    def _handle_voice_create_session(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
        except Exception:
            self._respond(400, {"status": "error", "message": "Invalid JSON"})
            return

        activation_source = body.get("activation_source", "push_to_talk")
        try:
            session = get_voice_manager().create_session(activation_source=activation_source)
        except VoiceError as exc:
            return self._respond_voice_error(exc)
        except Exception as exc:
            VOICE_LOGGER.exception("voice: failed to create session")
            return self._respond(500, {"status": "error", "message": str(exc)})

        self._respond(
            201,
            {"status": "ok", "session_id": session.session_id, "state": session.state.value},
        )

    def _handle_voice_push_audio(self, session_id: str, query: dict[str, list[str]]):
        try:
            sequence = int(query.get("seq", ["0"])[0])
        except (ValueError, IndexError):
            return self._respond(400, {"status": "error", "message": "Invalid or missing 'seq' query parameter"})
        is_final = query.get("final", ["false"])[0].lower() in ("1", "true", "yes")

        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return self._respond(400, {"status": "error", "message": "Empty audio payload"})
        if length > MAX_AUDIO_CHUNK_BYTES:
            return self._respond(
                413, {"status": "error", "message": f"Audio chunk exceeds {MAX_AUDIO_CHUNK_BYTES} bytes"}
            )
        pcm_bytes = self.rfile.read(length)

        try:
            session = get_voice_manager().get_session(session_id)
            ack = session.push_audio(sequence, pcm_bytes, is_final)
        except VoiceError as exc:
            return self._respond_voice_error(exc)
        except Exception as exc:
            VOICE_LOGGER.exception("voice: push_audio failed for %s", session_id)
            return self._respond(500, {"status": "error", "message": str(exc)})

        self._respond(202, {"status": "ok", **ack})

    def _handle_voice_cancel(self, session_id: str):
        try:
            get_voice_manager().cancel_session(session_id)
        except VoiceError as exc:
            return self._respond_voice_error(exc)
        self._respond(200, {"status": "ok"})

    def _handle_voice_reply(self, session_id: str):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
        except Exception:
            return self._respond(400, {"status": "error", "message": "Invalid JSON"})

        reply_text = (body.get("reply") or "").strip()
        try:
            session = get_voice_manager().get_session(session_id)
            session.submit_reply(reply_text)
        except VoiceError as exc:
            return self._respond_voice_error(exc)
        self._respond(200, {"status": "ok"})

    def _handle_voice_poll_events(self, session_id: str, query: dict[str, list[str]]):
        try:
            after = int(query.get("after", ["0"])[0])
        except (ValueError, IndexError):
            return self._respond(400, {"status": "error", "message": "Invalid 'after' query parameter"})

        try:
            session = get_voice_manager().get_session(session_id)
            events, latest, dropped = session.poll_events(after)
        except VoiceError as exc:
            return self._respond_voice_error(exc)

        self._respond(
            200,
            {
                "status": "ok",
                "state": session.state.value,
                "cursor": latest,
                "dropped": dropped,
                "events": [{"sequence": e.sequence, "type": e.type, **e.data} for e in events],
            },
        )

    def _handle_voice_pull_audio(self, session_id: str, query: dict[str, list[str]]):
        try:
            after = int(query.get("after", ["0"])[0])
            limit = int(query.get("limit", ["5"])[0])
        except (ValueError, IndexError):
            return self._respond(400, {"status": "error", "message": "Invalid 'after'/'limit' query parameter"})
        limit = max(1, min(limit, 20))

        try:
            session = get_voice_manager().get_session(session_id)
            chunks, start_sequence, latest, closed, cancelled = session.pull_audio(after, limit)
        except VoiceError as exc:
            return self._respond_voice_error(exc)

        self._respond(
            200,
            {
                "status": "ok",
                "state": session.state.value,
                "cursor": latest,
                "closed": closed,
                "cancelled": cancelled,
                "frames": [
                    {
                        "sequence": start_sequence + i,
                        "pcm_f32le_b64": base64.b64encode(c.pcm_f32le).decode("ascii"),
                        "valid_samples": c.valid_samples,
                        "is_final": c.is_final,
                        "sample_rate": c.sample_rate,
                        "channels": c.channels,
                    }
                    for i, c in enumerate(chunks)
                ],
            },
        )

    def do_GET(self):
        path, query = self._split_path()
        if path == "/health":
            self._respond(200, {"status": "ok"})
            return

        voice_match = self._match_voice_session_path(path)
        if voice_match:
            session_id, action = voice_match
            if action == "events":
                return self._handle_voice_poll_events(session_id, query)
            if action == "audio":
                return self._handle_voice_pull_audio(session_id, query)

        if path.startswith("/result/"):
            job_id = path[len("/result/") :]
            wait_ms = 0
            try:
                wait_ms = max(0, min(2000, int(query.get("wait_ms", ["0"])[0])))
            except (ValueError, IndexError):
                wait_ms = 0

            with jobs_lock:
                job = jobs.get(job_id)
                done_ev = job_done_events.get(job_id)
                should_wait = (
                    wait_ms > 0
                    and job is not None
                    and job.get("status") == "running"
                    and not job.get("question")
                    and done_ev is not None
                )

            if should_wait and done_ev is not None:
                done_ev.wait(timeout=wait_ms / 1000.0)
                done_ev.clear()

            with jobs_lock:
                job = jobs.get(job_id)
            if job is None:
                self._respond(404, {"status": "error", "message": "Job not found"})
            else:
                safe = {k: v for k, v in job.items() if k != "answer"}
                self._respond(200, safe)
            return

        self._respond(404, {"status": "error", "message": "Not found"})

    def _respond(self, code: int, data: dict[str, Any]):
        try:
            body = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        msg = format % args if args else format
        if "GET /result/" in msg:
            return
        LOGGER.info(format, *args)
