from __future__ import annotations

import logging
import math
import re
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.signal import resample_poly

from src.audio.tts.artifacts import KokoroRuntime, load_kokoro_runtime
from src.audio.tts.exceptions import (
    TTSCancelledError,
    TTSError,
    TTSModelNotLoadedError,
    TTSStaleGenerationError,
    TTSSynthesisError,
    TTSValidationError,
)

LOGGER = logging.getLogger("loki.tts")

PCM_SAMPLE_RATE_HZ = 24_000
PCM_FRAME_DURATION_MS = 20
PCM_FRAME_SAMPLES = PCM_SAMPLE_RATE_HZ * PCM_FRAME_DURATION_MS // 1_000
PCM_FRAME_BYTES = PCM_FRAME_SAMPLES * 4
MAX_TEXT_CHARS = 4_000
MAX_SEGMENT_CHARS = 240
MAX_AUDIO_SEGMENT_SAMPLES = PCM_SAMPLE_RATE_HZ * 60
_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_SENTENCE_BOUNDARY_PATTERN = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True)
class PCMChunk:
    generation_id: int
    session_id: str
    sequence: int
    pcm_f32le: bytes
    valid_samples: int
    is_final: bool
    sample_rate: int = PCM_SAMPLE_RATE_HZ
    channels: int = 1


@dataclass
class _GenerationState:
    generation_id: int
    session_id: str
    cancelled: bool = False
    stale: bool = False


class SynthesisStream(Iterator[PCMChunk]):
    def __init__(
        self,
        adapter: KokoroStreamingAdapter,
        state: _GenerationState,
        iterator: Iterator[PCMChunk],
    ):
        self._adapter = adapter
        self._iterator = iterator
        self.generation_id = state.generation_id
        self.session_id = state.session_id

    def __iter__(self) -> SynthesisStream:
        return self

    def __next__(self) -> PCMChunk:
        return next(self._iterator)

    def close(self) -> None:
        try:
            self._adapter.cancel(self.generation_id)
        except TTSStaleGenerationError:
            pass
        close = getattr(self._iterator, "close", None)
        if close is not None:
            close()


class KokoroStreamingAdapter:
    def __init__(
        self,
        *,
        model_dir: Path | None = None,
        runtime_loader: Callable[[Path | None], KokoroRuntime] = load_kokoro_runtime,
    ):
        self.model_dir = model_dir
        self._runtime_loader = runtime_loader
        self._runtime: KokoroRuntime | None = None
        self._active: _GenerationState | None = None
        self._generation_id = 0
        self._state_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._runtime is not None

    @property
    def model(self) -> Any | None:
        runtime = self._runtime
        return None if runtime is None else runtime.model

    def load(self) -> None:
        with self._state_lock:
            if self._runtime is not None:
                return
            self._runtime = self._runtime_loader(self.model_dir)

    def synthesize(
        self,
        text: str,
        session_id: str,
        *,
        speed: float = 1.0,
    ) -> SynthesisStream:
        normalized_text = _validate_and_segment_text(text)
        _validate_session_id(session_id)
        if not isinstance(speed, (int, float)) or not math.isfinite(speed):
            raise TTSValidationError("Speech speed must be a finite number")
        if not 0.5 <= speed <= 2.0:
            raise TTSValidationError("Speech speed must be between 0.5 and 2.0")

        with self._state_lock:
            if self._runtime is None:
                raise TTSModelNotLoadedError()
            if self._active is not None:
                self._active.stale = True
            self._generation_id += 1
            state = _GenerationState(self._generation_id, session_id)
            self._active = state

        iterator = self._generate_chunks(state, normalized_text, float(speed))
        return SynthesisStream(self, state, iterator)

    def cancel(self, generation_id: int | None = None) -> bool:
        with self._state_lock:
            state = self._active
            if state is None:
                return False
            if generation_id is not None and generation_id != state.generation_id:
                raise TTSStaleGenerationError()
            state.cancelled = True
            self._active = None
            return True

    def reset(self) -> None:
        with self._state_lock:
            if self._active is not None:
                self._active.stale = True
                self._active = None
            self._generation_id += 1
            runtime = self._runtime

        if runtime is None:
            return
        with self._inference_lock:
            pipelines = getattr(runtime.model, "_pipelines", None)
            if isinstance(pipelines, dict):
                pipelines.clear()

    def _generate_chunks(
        self,
        state: _GenerationState,
        text: str,
        speed: float,
    ) -> Iterator[PCMChunk]:
        runtime = self._runtime
        if runtime is None:
            raise TTSModelNotLoadedError()

        pending = np.empty(0, dtype=np.float32)
        sequence = 0
        try:
            results = iter(
                runtime.model.generate(
                    text,
                    voice=str(runtime.voice_path),
                    lang_code=runtime.language_code,
                    speed=speed,
                    split_pattern=r"\n+",
                )
            )
            while True:
                self._assert_active(state)
                try:
                    with self._inference_lock:
                        self._assert_active(state)
                        result = next(results)
                except StopIteration:
                    break
                self._assert_active(state)
                audio = _canonicalize_audio(result.audio, result.sample_rate)
                pending = np.concatenate((pending, audio))
                while pending.size > PCM_FRAME_SAMPLES:
                    self._assert_active(state)
                    frame = pending[:PCM_FRAME_SAMPLES]
                    pending = pending[PCM_FRAME_SAMPLES:]
                    yield _build_chunk(state, sequence, frame, PCM_FRAME_SAMPLES, False)
                    sequence += 1

            self._assert_active(state)
            if pending.size == 0:
                raise TTSSynthesisError()
            valid_samples = int(pending.size)
            if valid_samples < PCM_FRAME_SAMPLES:
                pending = np.pad(pending, (0, PCM_FRAME_SAMPLES - valid_samples))
            yield _build_chunk(state, sequence, pending, valid_samples, True)
        except (TTSError, GeneratorExit):
            raise
        except Exception as exc:
            LOGGER.warning("tts: synthesis failed (%s)", type(exc).__name__)
            raise TTSSynthesisError() from None
        finally:
            self._finish(state)

    def _assert_active(self, state: _GenerationState) -> None:
        with self._state_lock:
            if state.cancelled:
                raise TTSCancelledError()
            if state.stale or self._active is not state:
                raise TTSStaleGenerationError()

    def _finish(self, state: _GenerationState) -> None:
        with self._state_lock:
            if self._active is state:
                self._active = None


def _validate_and_segment_text(text: str) -> str:
    if not isinstance(text, str):
        raise TTSValidationError("Speech text must be a string")
    text = " ".join(text.split())
    if not text:
        raise TTSValidationError("Speech text must not be empty")
    if len(text) > MAX_TEXT_CHARS:
        raise TTSValidationError(f"Speech text exceeds {MAX_TEXT_CHARS} characters")

    segments: list[str] = []
    for sentence in _SENTENCE_BOUNDARY_PATTERN.split(text):
        words = sentence.split()
        current: list[str] = []
        current_length = 0
        for word in words:
            added = len(word) + (1 if current else 0)
            if current and current_length + added > MAX_SEGMENT_CHARS:
                segments.append(" ".join(current))
                current = [word]
                current_length = len(word)
            else:
                current.append(word)
                current_length += added
        if current:
            segments.append(" ".join(current))
    return "\n".join(segments)


def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise TTSValidationError("Session ID has an invalid format")


def _canonicalize_audio(audio: Any, sample_rate: Any) -> np.ndarray:
    try:
        sample_rate = int(sample_rate)
    except (TypeError, ValueError, OverflowError):
        raise TTSSynthesisError() from None
    if not 8_000 <= sample_rate <= 192_000:
        raise TTSSynthesisError()

    array = np.asarray(audio, dtype=np.float32)
    if array.ndim == 2:
        if array.shape[0] == 1:
            array = array[0]
        elif array.shape[1] == 1:
            array = array[:, 0]
        else:
            raise TTSSynthesisError()
    if array.ndim != 1 or array.size == 0 or array.size > MAX_AUDIO_SEGMENT_SAMPLES:
        raise TTSSynthesisError()
    if not np.isfinite(array).all():
        raise TTSSynthesisError()

    if sample_rate != PCM_SAMPLE_RATE_HZ:
        divisor = math.gcd(sample_rate, PCM_SAMPLE_RATE_HZ)
        array = resample_poly(
            array,
            PCM_SAMPLE_RATE_HZ // divisor,
            sample_rate // divisor,
        ).astype(np.float32, copy=False)
    return np.clip(array, -1.0, 1.0).astype(np.float32, copy=False)


def _build_chunk(
    state: _GenerationState,
    sequence: int,
    frame: np.ndarray,
    valid_samples: int,
    is_final: bool,
) -> PCMChunk:
    pcm = np.ascontiguousarray(frame, dtype="<f4").tobytes()
    if len(pcm) != PCM_FRAME_BYTES:
        raise TTSSynthesisError()
    return PCMChunk(
        generation_id=state.generation_id,
        session_id=state.session_id,
        sequence=sequence,
        pcm_f32le=pcm,
        valid_samples=valid_samples,
        is_final=is_final,
    )
