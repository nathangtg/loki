from __future__ import annotations

import enum
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

from src.audio.stt.exceptions import STTCancelledError, STTError
from src.audio.tts.exceptions import TTSError
from src.voice.audio_queue import VoicePCMQueue
from src.voice.events import VoiceEventLog
from src.voice.exceptions import VoiceInvalidStateError, VoiceSequenceGapError

LOGGER = logging.getLogger("loki.voice.session")

DEFAULT_SESSION_TTL_SECONDS = 90.0
MAX_AUDIO_CHUNK_BYTES = 32_000
REPLY_WAIT_TIMEOUT_SECONDS = 120.0


class VoiceSessionState(str, enum.Enum):
    LISTENING = "listening"
    FINALIZING = "finalizing"
    PROCESSING = "processing"
    AWAITING_REPLY = "awaiting_reply"
    SPEAKING = "speaking"
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class VoiceSessionHandles:
    stt_engine: Any
    tts_adapter: Any
    pipeline_lock: threading.Lock
    run_pipeline: Callable[..., dict]


class VoiceSession:
    def __init__(
        self,
        session_id: str,
        handles: VoiceSessionHandles,
        *,
        activation_source: str = "push_to_talk",
        ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
        text_only: bool = False,
    ):
        self.session_id = session_id
        self._handles = handles
        self.activation_source = activation_source
        self.ttl_seconds = ttl_seconds
        self.text_only = text_only

        self.events = VoiceEventLog()
        self.output_audio = VoicePCMQueue()

        self.state = VoiceSessionState.LISTENING
        self._next_input_sequence = 0
        self._lock = threading.RLock()
        self._last_activity = time.monotonic()
        self._worker: Optional[threading.Thread] = None
        self._reply_event: Optional[threading.Event] = None
        self._pending_reply = ""

        self._stt_session = (
            None
            if text_only
            else handles.stt_engine.create_session(
                session_id=f"voice-{session_id}",
                activation_source=activation_source,
                on_partial=self._on_partial_transcript,
            )
        )

    def touch(self) -> None:
        with self._lock:
            self._last_activity = time.monotonic()

    def is_expired(self, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            if self.state in (
                VoiceSessionState.DONE,
                VoiceSessionState.CANCELLED,
                VoiceSessionState.ERROR,
            ):
                return (now - self._last_activity) > self.ttl_seconds
            return (now - self._last_activity) > self.ttl_seconds

    def _on_partial_transcript(self, text: str, revision: int) -> None:
        self.events.emit("transcript.partial", text=text, revision=revision)

    def push_audio(self, sequence: int, pcm_bytes: bytes, is_final: bool) -> dict:
        if self.text_only:
            raise VoiceInvalidStateError("text_only", "push_audio")
        if len(pcm_bytes) > MAX_AUDIO_CHUNK_BYTES:
            from src.voice.exceptions import VoiceBodyTooLargeError

            raise VoiceBodyTooLargeError(MAX_AUDIO_CHUNK_BYTES)

        with self._lock:
            self.touch()
            if self.state != VoiceSessionState.LISTENING:
                raise VoiceInvalidStateError(self.state.value, "push_audio")

            expected = self._next_input_sequence
            if sequence < expected:
                return {"accepted": True, "duplicate": True, "next_sequence": expected}
            if sequence > expected:
                self._fail_locked("voice.audio_gap")
                raise VoiceSequenceGapError(expected, sequence)
            self._next_input_sequence = expected + 1

        try:
            self._stt_session.add_audio(pcm_bytes, is_final=False)
        except (STTError, STTCancelledError) as exc:
            with self._lock:
                self._fail_locked(getattr(exc, "code", "voice.stt_error"))
            raise

        if is_final:
            self._finalize_locked_entry()

        return {"accepted": True, "duplicate": False, "next_sequence": expected + 1}

    def finalize(self) -> None:
        if self.text_only:
            raise VoiceInvalidStateError("text_only", "finalize")
        with self._lock:
            self.touch()
            if self.state != VoiceSessionState.LISTENING:
                raise VoiceInvalidStateError(self.state.value, "finalize")
        self._finalize_locked_entry()

    def _finalize_locked_entry(self) -> None:
        with self._lock:
            self.state = VoiceSessionState.FINALIZING

        try:
            transcript = self._stt_session.finalize()
        except (STTError, STTCancelledError) as exc:
            with self._lock:
                self._fail_locked(getattr(exc, "code", "voice.stt_error"))
            return

        self.events.emit(
            "transcript.final",
            text=transcript.text,
            duration_seconds=transcript.duration_seconds,
        )

        self._start_processing(transcript.text)

    def finalize_with_transcript(self, text: str) -> None:
        with self._lock:
            self.touch()
            if self.state != VoiceSessionState.LISTENING:
                raise VoiceInvalidStateError(self.state.value, "finalize_with_transcript")
            self.state = VoiceSessionState.FINALIZING
        if self._stt_session is not None:
            try:
                self._stt_session.cancel("bypassed")
            except Exception:
                pass
        self.events.emit("transcript.final", text=text, duration_seconds=0.0)
        self._start_processing(text)

    def _start_processing(self, text: str) -> None:
        if not text.strip():
            with self._lock:
                self.state = VoiceSessionState.DONE
            self.events.emit("voice.no_speech")
            return

        with self._lock:
            self.state = VoiceSessionState.PROCESSING
        self._worker = threading.Thread(
            target=self._run_pipeline_and_speak,
            args=(text,),
            daemon=True,
            name=f"loki-voice-{self.session_id[:8]}",
        )
        self._worker.start()

    def _run_pipeline_and_speak(self, command: str) -> None:
        start = time.monotonic()

        def on_step(action: str, target: str, result: str) -> None:
            self.events.emit("step", action=action, target=target[:80], result=result[:160])

        def on_message(msg_type: str, content: str) -> None:
            self.events.emit("message", message_type=msg_type, content=content)

        def ask_user(question: str) -> str:
            return self._ask_user(question)

        try:
            with self._handles.pipeline_lock:
                if self._is_terminal():
                    return
                result = self._handles.run_pipeline(
                    command,
                    on_step=on_step,
                    on_message=on_message,
                    ask_user=ask_user,
                )
        except Exception as exc:
            LOGGER.exception("voice: pipeline failed for session %s", self.session_id)
            with self._lock:
                self._fail_locked("voice.pipeline_failed")
            self.events.emit("error", code="voice.pipeline_failed", message=str(exc))
            return

        if self._is_terminal():
            return

        message = (result or {}).get("message", "") or ""
        self.events.emit(
            "assistant.text",
            text=message,
            elapsed_seconds=round(time.monotonic() - start, 3),
        )

        if not message.strip():
            with self._lock:
                self.state = VoiceSessionState.DONE
            self.output_audio.close()
            return

        self._speak(message, goal=command)

    def _ask_user(self, question: str) -> str:
        with self._lock:
            if self._is_terminal():
                return ""
            self.state = VoiceSessionState.AWAITING_REPLY
            self._reply_event = threading.Event()
            self._pending_reply = ""

        self.events.emit("question", text=question)
        try:
            self._speak(question, final_turn=False)
        except TTSError:
            LOGGER.warning("voice: failed to speak question for session %s", self.session_id)

        assert self._reply_event is not None
        self._reply_event.wait(timeout=REPLY_WAIT_TIMEOUT_SECONDS)

        with self._lock:
            reply = self._pending_reply
            self._reply_event = None
            if not self._is_terminal():
                self.state = VoiceSessionState.PROCESSING

        self.events.emit("answer", text=reply)
        return reply

    def submit_reply(self, reply_text: str) -> None:
        with self._lock:
            if self.state != VoiceSessionState.AWAITING_REPLY or self._reply_event is None:
                raise VoiceInvalidStateError(self.state.value, "submit_reply")
            self._pending_reply = reply_text
            event = self._reply_event
        event.set()

    def _speak(self, text: str, *, final_turn: bool = True, goal: str = "") -> None:
        with self._lock:
            if self._is_terminal():
                return
            previous_state = self.state
            self.state = VoiceSessionState.SPEAKING

        try:
            from src.audio.tts.system import format_for_speech

            segments = format_for_speech(text, goal=goal) or [text]
            is_system_speech = type(self._handles.tts_adapter).__name__ == "MacOSSystemSpeechAdapter"
            self.events.emit(
                "speaking.started",
                backend="system" if is_system_speech else "kokoro",
                text=" ".join(segments),
            )
            if is_system_speech:
                for segment in segments:
                    if self._is_terminal():
                        return
                    handle = self._handles.tts_adapter.synthesize(segment, self.session_id)
                    handle.wait()
            else:
                result = self._handles.tts_adapter.synthesize(" ".join(segments), self.session_id)
                if hasattr(result, "wait") and not hasattr(result, "__iter__"):
                    result.wait()
                else:
                    stream = result
                    for chunk in stream:
                        if self._is_terminal():
                            close_fn = getattr(stream, "close", None)
                            if close_fn is not None:
                                close_fn()
                            return
                        if not self.output_audio.put(chunk):
                            close_fn = getattr(stream, "close", None)
                            if close_fn is not None:
                                close_fn()
                            if not self._is_terminal():
                                self.events.emit("error", code="voice.audio_backpressure", message="client stopped consuming audio")
                                with self._lock:
                                    self._fail_locked("voice.audio_backpressure")
                            return
        except TTSError as exc:
            LOGGER.warning("voice: TTS failed for session %s: %s", self.session_id, exc)
            self.events.emit("error", code=getattr(exc, "code", "voice.tts_error"), message=str(exc))
            with self._lock:
                self._fail_locked(getattr(exc, "code", "voice.tts_error"))
            return

        with self._lock:
            if self._is_terminal():
                pass
            elif final_turn:
                self.state = VoiceSessionState.DONE
            else:
                self.state = previous_state

        if final_turn:
            self.output_audio.close()
            self.events.emit("speaking.done")

    def poll_events(self, after: int = 0):
        self.touch()
        return self.events.poll(after)

    def pull_audio(self, after: int = 0, limit: int = 5):
        self.touch()
        return self.output_audio.poll(after, limit)

    def _is_terminal(self) -> bool:
        return self.state in (
            VoiceSessionState.DONE,
            VoiceSessionState.CANCELLED,
            VoiceSessionState.ERROR,
        )

    def _fail_locked(self, code: str) -> None:
        if self._is_terminal():
            return
        self.state = VoiceSessionState.ERROR
        self.output_audio.cancel()
        try:
            self._stt_session.cancel(code)
        except Exception:
            pass
        reply_event = self._reply_event
        if reply_event is not None:
            reply_event.set()

    def cancel(self, reason: str = "cancelled") -> None:
        with self._lock:
            if self._is_terminal():
                return
            self.state = VoiceSessionState.CANCELLED
            reply_event = self._reply_event
        self.events.emit("cancelled", reason=reason)
        self.output_audio.cancel()
        try:
            self._handles.tts_adapter.cancel()
        except Exception:
            pass
        try:
            self._stt_session.cancel(reason)
        except Exception:
            pass
        if reply_event is not None:
            reply_event.set()

    def close(self) -> None:
        self.cancel("session_closed") if not self._is_terminal() else None
        try:
            self._stt_session.close()
        except Exception:
            pass


def new_session_id() -> str:
    return uuid.uuid4().hex
