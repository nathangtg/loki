from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Union

import numpy as np

from src.audio.stt.exceptions import (
    ModelNotLoadedError,
    SessionNotFoundError,
    STTCancelledError,
    STTError,
    UtteranceLimitExceededError,
)
from src.audio.stt.normalization import (
    normalize_wake_prefix,
    parse_and_validate_f32le_pcm,
)
from src.audio.stt.session import (
    DEFAULT_UPDATE_INTERVAL,
    MAX_UTTERANCE_SAMPLES,
    MAX_UTTERANCE_SECONDS,
    SAMPLE_RATE_HZ,
    FinalTranscript,
    SessionState,
)
from src.audio.stt.adapter import MoonshineStreamingAdapter
from src.audio.stt.artifacts import MoonshineVariant

LOGGER = logging.getLogger("loki.stt.speculative")

DEFAULT_ENTROPY_THRESHOLD = 0.35
DEFAULT_AMBIGUITY_RATIO_TRIGGER = 0.15
SILENCE_RMS_THRESHOLD = 0.005


def compute_spectral_entropy(samples: Sequence[float]) -> float:
    arr = np.asarray(samples, dtype=np.float32)
    if len(arr) == 0:
        return 0.0

    rms = float(np.sqrt(np.mean(arr**2)))
    if rms < SILENCE_RMS_THRESHOLD:
        return 0.0

    fft_vals = np.abs(np.fft.rfft(arr))
    psd = fft_vals**2
    psd_sum = np.sum(psd)
    if psd_sum == 0:
        return 0.0

    prob = psd / psd_sum
    prob = prob[prob > 0]
    shannon = -float(np.sum(prob * np.log2(prob)))
    max_entropy = float(np.log2(len(fft_vals)))
    if max_entropy == 0:
        return 0.0

    return min(1.0, max(0.0, shannon / max_entropy))


@dataclass
class SpeculativeMetrics:
    total_chunks: int = 0
    draft_accepted_chunks: int = 0
    verified_chunks: int = 0
    ambiguous_chunks: int = 0
    mean_entropy: float = 0.0
    compute_savings_pct: float = 0.0
    verification_triggered: bool = False


class SpeculativeStreamSession:
    def __init__(
        self,
        session_id: str,
        draft_adapter: MoonshineStreamingAdapter,
        verifier_adapter: MoonshineStreamingAdapter,
        *,
        activation_source: str = "push_to_talk",
        entropy_threshold: float = DEFAULT_ENTROPY_THRESHOLD,
        ambiguity_ratio_trigger: float = DEFAULT_AMBIGUITY_RATIO_TRIGGER,
        verification_policy: str = "adaptive",
        update_interval: float = DEFAULT_UPDATE_INTERVAL,
        on_partial: Optional[Callable[[str, int], None]] = None,
        on_final: Optional[Callable[[FinalTranscript], None]] = None,
    ):
        self.session_id = session_id
        self.draft_adapter = draft_adapter
        self.verifier_adapter = verifier_adapter
        self.activation_source = activation_source
        self.entropy_threshold = entropy_threshold
        self.ambiguity_ratio_trigger = ambiguity_ratio_trigger
        self.verification_policy = verification_policy
        self.update_interval = update_interval
        self._on_partial = on_partial
        self._on_final = on_final

        self.state = SessionState.READY
        self.revision = 0
        self.total_samples = 0
        self._lock = threading.Lock()

        self._audio_buffer: list[float] = []
        self._chunk_entropies: list[float] = []
        self._ambiguous_chunk_count = 0
        self._verification_triggered = False

        self._draft_session = self.draft_adapter.create_session(
            session_id=f"{session_id}-draft",
            activation_source=activation_source,
            on_partial=self._handle_draft_partial,
        )

    def _handle_draft_partial(self, text: str, revision: int) -> None:
        with self._lock:
            if self.state not in (SessionState.LISTENING, SessionState.FINALIZING):
                return
            self.revision = revision

        if self._on_partial:
            try:
                self._on_partial(text, revision)
            except Exception as e:
                LOGGER.warning("stt: error in speculative partial callback: %s", e)

    def start(self) -> None:
        with self._lock:
            if self.state != SessionState.READY:
                raise STTError(
                    "voice.invalid_state",
                    f"Cannot start session in state {self.state.value}",
                )
            self.state = SessionState.LISTENING

    def add_audio(
        self,
        audio: Union[bytes, bytearray, Sequence[float]],
        is_final: bool = False,
    ) -> None:
        samples = parse_and_validate_f32le_pcm(audio)
        num_new = len(samples)

        with self._lock:
            if self.state != SessionState.LISTENING:
                raise STTError(
                    "voice.invalid_state",
                    f"Cannot add audio in state {self.state.value}",
                )
            if self.total_samples + num_new > MAX_UTTERANCE_SAMPLES:
                self.state = SessionState.ERROR
                raise UtteranceLimitExceededError(MAX_UTTERANCE_SECONDS)
            self.total_samples += num_new

        self._audio_buffer.extend(samples)

        entropy = compute_spectral_entropy(samples)
        self._chunk_entropies.append(entropy)
        if entropy >= self.entropy_threshold:
            self._ambiguous_chunk_count += 1

        self._draft_session.add_audio(samples)

        if is_final:
            self.finalize()

    def finalize(self) -> FinalTranscript:
        with self._lock:
            if self.state == SessionState.CANCELLED:
                raise STTCancelledError()
            if self.state not in (SessionState.LISTENING, SessionState.FINALIZING):
                raise STTError(
                    "voice.invalid_state",
                    f"Cannot finalize session in state {self.state.value}",
                )
            self.state = SessionState.FINALIZING

        draft_result = self._draft_session.finalize()

        total_chunks = len(self._chunk_entropies)
        ambiguous_chunks = self._ambiguous_chunk_count

        trigger_verification = False
        if self.verification_policy == "always":
            trigger_verification = True
        elif self.verification_policy == "never":
            trigger_verification = False
        else:
            if total_chunks > 0:
                ratio = ambiguous_chunks / total_chunks
                trigger_verification = (ratio >= self.ambiguity_ratio_trigger) or (ambiguous_chunks >= 2)

        chosen_text = draft_result.text
        num_lines = draft_result.num_lines

        if trigger_verification and len(self._audio_buffer) > 0 and self.verifier_adapter.transcriber:
            try:
                verifier_res = self.verifier_adapter.transcriber.transcribe_without_streaming(
                    self._audio_buffer,
                    sample_rate=SAMPLE_RATE_HZ,
                )
                verified_text = " ".join(
                    line.text.strip() for line in verifier_res.lines if line.text.strip()
                )
                if verified_text.strip():
                    chosen_text = verified_text
                    num_lines = len(verifier_res.lines)
                self._verification_triggered = True
            except Exception as e:
                LOGGER.warning("stt: verifier pass failed, falling back to draft: %s", e)
                self._verification_triggered = False
        else:
            self._verification_triggered = False

        wake_prefix_removed = False
        normalized_text = chosen_text
        if self.activation_source == "wake_word":
            normalized_text, wake_prefix_removed = normalize_wake_prefix(chosen_text)

        with self._lock:
            self.revision += 1
            dur = self.total_samples / SAMPLE_RATE_HZ
            transcript = FinalTranscript(
                text=normalized_text,
                session_id=self.session_id,
                activation_source=self.activation_source,
                wake_prefix_removed=wake_prefix_removed,
                duration_seconds=dur,
                num_samples=self.total_samples,
                num_lines=num_lines,
                revision=self.revision,
            )
            self.state = SessionState.COMPLETED

        metrics = self.get_metrics()

        LOGGER.info(
            "stt: speculative session %s complete (dur=%.2fs, savings=%.1f%%, verified=%s, mean_entropy=%.2f)",
            self.session_id,
            dur,
            metrics.compute_savings_pct,
            self._verification_triggered,
            metrics.mean_entropy,
        )

        if self._on_final:
            try:
                self._on_final(transcript)
            except Exception as e:
                LOGGER.warning("stt: error in speculative on_final callback: %s", e)

        return transcript

    def get_metrics(self) -> SpeculativeMetrics:
        total = len(self._chunk_entropies)
        ambiguous = self._ambiguous_chunk_count
        if self._verification_triggered:
            verified = ambiguous
            accepted = total - ambiguous
            savings = (accepted / total * 100.0) if total > 0 else 0.0
        else:
            verified = 0
            accepted = total
            savings = 100.0 if total > 0 else 0.0

        mean_e = float(np.mean(self._chunk_entropies)) if total > 0 else 0.0
        return SpeculativeMetrics(
            total_chunks=total,
            draft_accepted_chunks=accepted,
            verified_chunks=verified,
            ambiguous_chunks=ambiguous,
            mean_entropy=mean_e,
            compute_savings_pct=savings,
            verification_triggered=self._verification_triggered,
        )

    def cancel(self, reason: str = "cancelled") -> None:
        with self._lock:
            if self.state in (SessionState.COMPLETED, SessionState.CANCELLED):
                return
            self.state = SessionState.CANCELLED
            LOGGER.info("stt: speculative session %s cancelled (reason=%s)", self.session_id, reason)
        self._draft_session.cancel(reason)

    def close(self) -> None:
        with self._lock:
            if self.state not in (SessionState.COMPLETED, SessionState.CANCELLED):
                self.state = SessionState.CANCELLED
        self._draft_session.close()


class SpeculativeStreamingEngine:
    def __init__(
        self,
        *,
        entropy_threshold: float = DEFAULT_ENTROPY_THRESHOLD,
        ambiguity_ratio_trigger: float = DEFAULT_AMBIGUITY_RATIO_TRIGGER,
        update_interval: float = DEFAULT_UPDATE_INTERVAL,
    ):
        self.entropy_threshold = entropy_threshold
        self.ambiguity_ratio_trigger = ambiguity_ratio_trigger
        self.update_interval = update_interval

        self.draft_adapter = MoonshineStreamingAdapter(
            variant=MoonshineVariant.TINY_STREAMING,
            update_interval=update_interval,
        )
        self.verifier_adapter = MoonshineStreamingAdapter(
            variant=MoonshineVariant.SMALL_STREAMING,
            update_interval=update_interval,
        )
        self._active_sessions: dict[str, SpeculativeStreamSession] = {}
        self._lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self.draft_adapter.is_loaded and self.verifier_adapter.is_loaded

    def load(self) -> None:
        with self._lock:
            if not self.draft_adapter.is_loaded:
                self.draft_adapter.load()
            if not self.verifier_adapter.is_loaded:
                self.verifier_adapter.load()
        LOGGER.info("stt: SpeculativeStreamingEngine dual-tier models loaded")

    def unload(self) -> None:
        with self._lock:
            for s in list(self._active_sessions.values()):
                s.close()
            self._active_sessions.clear()
            self.draft_adapter.unload()
            self.verifier_adapter.unload()

    def create_session(
        self,
        session_id: str,
        *,
        activation_source: str = "push_to_talk",
        verification_policy: str = "adaptive",
        on_partial: Optional[Callable[[str, int], None]] = None,
        on_final: Optional[Callable[[FinalTranscript], None]] = None,
    ) -> SpeculativeStreamSession:
        with self._lock:
            if not self.is_loaded:
                raise ModelNotLoadedError("Speculative engine models are not loaded")
            if session_id in self._active_sessions:
                existing = self._active_sessions[session_id]
                existing.close()
                del self._active_sessions[session_id]

            session = SpeculativeStreamSession(
                session_id=session_id,
                draft_adapter=self.draft_adapter,
                verifier_adapter=self.verifier_adapter,
                activation_source=activation_source,
                entropy_threshold=self.entropy_threshold,
                ambiguity_ratio_trigger=self.ambiguity_ratio_trigger,
                verification_policy=verification_policy,
                update_interval=self.update_interval,
                on_partial=on_partial,
                on_final=on_final,
            )
            self._active_sessions[session_id] = session

        session.start()
        return session

    def get_session(self, session_id: str) -> SpeculativeStreamSession:
        with self._lock:
            session = self._active_sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError(session_id)
            return session

    def release_session(self, session_id: str) -> None:
        with self._lock:
            session = self._active_sessions.pop(session_id, None)
        if session:
            session.close()
            self.draft_adapter.release_session(f"{session_id}-draft")
