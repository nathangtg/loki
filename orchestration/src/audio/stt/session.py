from __future__ import annotations

import enum
import logging
import threading
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Union

from moonshine_voice.transcriber import (
    LineCompleted,
    LineTextChanged,
    Transcriber,
    TranscriptEvent,
)

from src.audio.stt.exceptions import (
    STTCancelledError,
    STTError,
    UtteranceLimitExceededError,
)
from src.audio.stt.normalization import (
    normalize_wake_prefix,
    parse_and_validate_f32le_pcm,
)

LOGGER = logging.getLogger("loki.stt.session")

SAMPLE_RATE_HZ = 16_000
MAX_UTTERANCE_SECONDS = 30.0
MAX_UTTERANCE_SAMPLES = int(SAMPLE_RATE_HZ * MAX_UTTERANCE_SECONDS)
DEFAULT_UPDATE_INTERVAL = 0.2


class SessionState(enum.Enum):
    READY = "ready"
    LISTENING = "listening"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class FinalTranscript:
    text: str
    session_id: str
    activation_source: str = "push_to_talk"
    wake_prefix_removed: bool = False
    duration_seconds: float = 0.0
    num_samples: int = 0
    num_lines: int = 0
    revision: int = 0


class MoonshineStreamSession:
    def __init__(
        self,
        session_id: str,
        transcriber: Transcriber,
        *,
        activation_source: str = "push_to_talk",
        configured_wake_phrase: str = "hey loki",
        update_interval: float = DEFAULT_UPDATE_INTERVAL,
        on_partial: Optional[Callable[[str, int], None]] = None,
        on_final: Optional[Callable[[FinalTranscript], None]] = None,
    ):
        self.session_id = session_id
        self._transcriber = transcriber
        self.activation_source = activation_source
        self.configured_wake_phrase = configured_wake_phrase
        self.update_interval = update_interval
        self._on_partial = on_partial
        self._on_final = on_final

        self.state = SessionState.READY
        self.revision = 0
        self.total_samples = 0
        self._lock = threading.Lock()

        self._completed_lines: list[str] = []
        self._current_partial_text: str = ""
        self._final_transcript: Optional[FinalTranscript] = None

        self._stream = self._transcriber.create_stream(update_interval=self.update_interval)
        self._stream.add_listener(self._on_transcript_event)

    def _on_transcript_event(self, event: TranscriptEvent) -> None:
        if isinstance(event, LineTextChanged):
            self._handle_text_changed(event.line.text)
        elif isinstance(event, LineCompleted):
            self._handle_line_completed(event.line.text)

    def start(self) -> None:
        with self._lock:
            if self.state != SessionState.READY:
                raise STTError(
                    "voice.invalid_state",
                    f"Cannot start session in state {self.state.value}",
                )
            self._stream.start()
            self.state = SessionState.LISTENING
            LOGGER.debug("stt: session %s started", self.session_id)

    def _handle_text_changed(self, text: str) -> None:
        clean = text.strip()
        with self._lock:
            if self.state not in (SessionState.LISTENING, SessionState.FINALIZING):
                return
            self.revision += 1
            rev = self.revision
            self._current_partial_text = clean

        if self._on_partial:
            try:
                self._on_partial(clean, rev)
            except Exception as e:
                LOGGER.warning("stt: error in on_partial callback: %s", e)

    def _handle_line_completed(self, text: str) -> None:
        clean = text.strip()
        with self._lock:
            if clean:
                self._completed_lines.append(clean)
            self._current_partial_text = ""
            self.revision += 1
            rev = self.revision

        if self._on_partial:
            full_interim = " ".join(self._completed_lines)
            try:
                self._on_partial(full_interim, rev)
            except Exception as e:
                LOGGER.warning(
                    "stt: error in on_partial callback during line completion: %s", e
                )

    def add_audio(
        self,
        audio: Union[bytes, bytearray, Sequence[float]],
        is_final: bool = False,
    ) -> None:
        samples = parse_and_validate_f32le_pcm(audio)
        num_new_samples = len(samples)

        with self._lock:
            if self.state != SessionState.LISTENING:
                raise STTError(
                    "voice.invalid_state",
                    f"Cannot add audio in state {self.state.value}",
                )

            if self.total_samples + num_new_samples > MAX_UTTERANCE_SAMPLES:
                self.state = SessionState.ERROR
                raise UtteranceLimitExceededError(MAX_UTTERANCE_SECONDS)

            self.total_samples += num_new_samples

        if samples:
            self._stream.add_audio(samples, SAMPLE_RATE_HZ)

        if is_final:
            self.finalize()

    def finalize(self) -> FinalTranscript:
        with self._lock:
            if self.state == SessionState.COMPLETED and self._final_transcript is not None:
                return self._final_transcript
            if self.state == SessionState.CANCELLED:
                raise STTCancelledError()
            if self.state not in (SessionState.LISTENING, SessionState.FINALIZING):
                raise STTError(
                    "voice.invalid_state",
                    f"Cannot finalize session in state {self.state.value}",
                )
            self.state = SessionState.FINALIZING

        self._stream.stop()

        with self._lock:
            all_lines = list(self._completed_lines)
            if self._current_partial_text and (
                not all_lines or self._current_partial_text != all_lines[-1]
            ):
                all_lines.append(self._current_partial_text)

            raw_text = " ".join(all_lines).strip()

            wake_prefix_removed = False
            normalized_text = raw_text
            if self.activation_source == "wake_word":
                normalized_text, wake_prefix_removed = normalize_wake_prefix(raw_text)

            self.revision += 1
            dur = self.total_samples / SAMPLE_RATE_HZ
            transcript = FinalTranscript(
                text=normalized_text,
                session_id=self.session_id,
                activation_source=self.activation_source,
                wake_prefix_removed=wake_prefix_removed,
                duration_seconds=dur,
                num_samples=self.total_samples,
                num_lines=len(all_lines),
                revision=self.revision,
            )
            self._final_transcript = transcript
            self.state = SessionState.COMPLETED

        LOGGER.info(
            "stt: session %s finalized (duration=%.2fs, lines=%d, wake_trimmed=%s)",
            self.session_id,
            dur,
            len(all_lines),
            wake_prefix_removed,
        )

        if self._on_final:
            try:
                self._on_final(transcript)
            except Exception as e:
                LOGGER.warning("stt: error in on_final callback: %s", e)

        return transcript

    def cancel(self, reason: str = "cancelled") -> None:
        with self._lock:
            if self.state in (SessionState.COMPLETED, SessionState.CANCELLED):
                return
            self.state = SessionState.CANCELLED
            LOGGER.info("stt: session %s cancelled (reason=%s)", self.session_id, reason)

        try:
            self._stream.stop()
        except Exception:
            pass

    def close(self) -> None:
        with self._lock:
            if self.state not in (SessionState.COMPLETED, SessionState.CANCELLED):
                self.state = SessionState.CANCELLED
        try:
            self._stream.close()
        except Exception:
            pass
