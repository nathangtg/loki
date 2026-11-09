from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Dict, Optional

from moonshine_voice.moonshine_api import ModelArch
from moonshine_voice.transcriber import Transcriber

from src.audio.stt.exceptions import (
    InvalidAudioDataError,
    ModelNotLoadedError,
    SessionConflictError,
    SessionNotFoundError,
    STTCancelledError,
    STTError,
    STTTimeoutError,
    UtteranceLimitExceededError,
)
from src.audio.stt.normalization import (
    normalize_wake_prefix,
    parse_and_validate_f32le_pcm,
)
from src.audio.stt.session import (
    DEFAULT_UPDATE_INTERVAL,
    MAX_UTTERANCE_SECONDS,
    SAMPLE_RATE_HZ,
    FinalTranscript,
    MoonshineStreamSession,
    SessionState,
)
from src.audio.stt.artifacts import (
    MoonshineVariant,
    resolve_model_path,
    verify_artifacts,
)

LOGGER = logging.getLogger("loki.stt")

_VARIANT_TO_ARCH = {
    MoonshineVariant.SMALL_STREAMING: ModelArch.SMALL_STREAMING,
    MoonshineVariant.TINY_STREAMING: ModelArch.TINY_STREAMING,
}

__all__ = [
    "MoonshineStreamingAdapter",
    "MoonshineStreamSession",
    "MoonshineVariant",
    "FinalTranscript",
    "SessionState",
    "load_transcriber",
    "normalize_wake_prefix",
    "parse_and_validate_f32le_pcm",
    "STTError",
    "ModelNotLoadedError",
    "SessionConflictError",
    "SessionNotFoundError",
    "UtteranceLimitExceededError",
    "InvalidAudioDataError",
    "STTTimeoutError",
    "STTCancelledError",
    "SAMPLE_RATE_HZ",
    "MAX_UTTERANCE_SECONDS",
    "DEFAULT_UPDATE_INTERVAL",
]


class MoonshineStreamingAdapter:
    def __init__(
        self,
        variant: MoonshineVariant = MoonshineVariant.SMALL_STREAMING,
        *,
        model_dir: Optional[Path] = None,
        update_interval: float = DEFAULT_UPDATE_INTERVAL,
        verify: bool = True,
    ):
        self.variant = variant
        self.model_dir = model_dir
        self.update_interval = update_interval
        self.verify = verify

        self._transcriber: Optional[Transcriber] = None
        self._active_sessions: Dict[str, MoonshineStreamSession] = {}
        self._lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._transcriber is not None

    @property
    def transcriber(self) -> Optional[Transcriber]:
        return self._transcriber

    def load(self) -> None:
        with self._lock:
            if self._transcriber is not None:
                return
            self._transcriber = load_transcriber(
                self.variant,
                model_dir=self.model_dir,
                update_interval=self.update_interval,
                verify=self.verify,
            )

    def unload(self) -> None:
        with self._lock:
            for s in list(self._active_sessions.values()):
                s.close()
            self._active_sessions.clear()
            self._transcriber = None

    def create_session(
        self,
        session_id: str,
        *,
        activation_source: str = "push_to_talk",
        on_partial: Optional[Callable[[str, int], None]] = None,
        on_final: Optional[Callable[[FinalTranscript], None]] = None,
    ) -> MoonshineStreamSession:
        with self._lock:
            if self._transcriber is None:
                raise ModelNotLoadedError()
            if session_id in self._active_sessions:
                existing = self._active_sessions[session_id]
                if existing.state not in (
                    SessionState.COMPLETED,
                    SessionState.CANCELLED,
                    SessionState.ERROR,
                ):
                    raise SessionConflictError(session_id)
                existing.close()
                del self._active_sessions[session_id]

            session = MoonshineStreamSession(
                session_id=session_id,
                transcriber=self._transcriber,
                activation_source=activation_source,
                update_interval=self.update_interval,
                on_partial=on_partial,
                on_final=on_final,
            )
            self._active_sessions[session_id] = session

        session.start()
        return session

    def get_session(self, session_id: str) -> MoonshineStreamSession:
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


def load_transcriber(
    variant: MoonshineVariant = MoonshineVariant.SMALL_STREAMING,
    *,
    model_dir: Optional[Path] = None,
    update_interval: float = DEFAULT_UPDATE_INTERVAL,
    verify: bool = True,
) -> Transcriber:
    if model_dir is None:
        model_dir = resolve_model_path(variant)

    LOGGER.info("stt: loading %s from %s", variant.value, model_dir)

    if verify:
        errors = verify_artifacts(variant, model_dir)
        if errors:
            msg = (
                f"Moonshine {variant.value} artifacts failed verification "
                f"at {model_dir}:\n  " + "\n  ".join(errors)
            )
            LOGGER.error(msg)
            if any("does not exist" in e for e in errors):
                raise FileNotFoundError(msg)
            raise ValueError(msg)

    arch = _VARIANT_TO_ARCH[variant]
    transcriber = Transcriber(
        model_path=str(model_dir),
        model_arch=arch,
        update_interval=update_interval,
    )

    LOGGER.info(
        "stt: %s loaded successfully (arch=%s, update_interval=%.2fs)",
        variant.value,
        arch.name,
        update_interval,
    )
    return transcriber
