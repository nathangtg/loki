from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.audio.stt import SpeculativeStreamingEngine
    from src.audio.tts import KokoroStreamingAdapter, MacOSSystemSpeechAdapter
    from src.voice import VoiceSessionManager

TTS_BACKEND = os.getenv("LOKI_TTS_BACKEND", "system").strip().lower()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
LOGGER = logging.getLogger("loki.server")


def _env_int(name: str, default: int) -> int:
    """Read a positive int from the environment, falling back with a warning."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        LOGGER.warning("%s=%r is not an integer, using default %d", name, raw, default)
        return default
    if value <= 0:
        LOGGER.warning("%s=%r must be positive, using default %d", name, raw, default)
        return default
    return value


JOB_TTL_SECONDS = _env_int("LOKI_JOB_TTL_SECONDS", 3600)
MAX_RETAINED_JOBS = _env_int("LOKI_MAX_RETAINED_JOBS", 200)
MAX_STEPS_PER_JOB = _env_int("LOKI_MAX_STEPS_PER_JOB", 200)
MAX_MESSAGES_PER_JOB = _env_int("LOKI_MAX_MESSAGES_PER_JOB", 200)
JOB_SWEEP_INTERVAL_SECONDS = _env_int("LOKI_JOB_SWEEP_INTERVAL_SECONDS", 60)
ASK_USER_TIMEOUT_SECONDS = _env_int("LOKI_ASK_USER_TIMEOUT_SECONDS", 120)

pipeline_lock = threading.Lock()

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
job_events: dict[str, threading.Event] = {}
job_done_events: dict[str, threading.Event] = {}

_stt_engine: Optional[SpeculativeStreamingEngine] = None
_stt_lock = threading.Lock()
_tts_adapter: Optional[KokoroStreamingAdapter] = None
_tts_lock = threading.Lock()
_system_tts_adapter: Optional[MacOSSystemSpeechAdapter] = None
_system_tts_lock = threading.Lock()
_voice_manager: Optional[VoiceSessionManager] = None
_voice_manager_lock = threading.Lock()


def get_stt_engine() -> SpeculativeStreamingEngine:
    global _stt_engine
    if _stt_engine is None:
        with _stt_lock:
            if _stt_engine is None:
                from src.audio.stt import SpeculativeStreamingEngine

                _stt_engine = SpeculativeStreamingEngine()
    return _stt_engine


def get_tts_adapter() -> KokoroStreamingAdapter:
    global _tts_adapter
    if _tts_adapter is None:
        with _tts_lock:
            if _tts_adapter is None:
                from src.audio.tts import KokoroStreamingAdapter

                _tts_adapter = KokoroStreamingAdapter()
    return _tts_adapter


def get_system_tts_adapter() -> MacOSSystemSpeechAdapter:
    global _system_tts_adapter
    if _system_tts_adapter is None:
        with _system_tts_lock:
            if _system_tts_adapter is None:
                from src.audio.tts import (
                    MacOSSystemSpeechAdapter,
                    TTSValidationError,
                    detect_preferred_macos_voice,
                )

                voice = os.getenv("LOKI_SYSTEM_TTS_VOICE") or detect_preferred_macos_voice()
                try:
                    rate = int(os.getenv("LOKI_SYSTEM_TTS_RATE", "185"))
                except ValueError:
                    raise TTSValidationError("LOKI_SYSTEM_TTS_RATE must be an integer") from None
                _system_tts_adapter = MacOSSystemSpeechAdapter(voice=voice, rate=rate)
    return _system_tts_adapter


def get_active_tts_adapter() -> KokoroStreamingAdapter | MacOSSystemSpeechAdapter:
    backend = os.getenv("LOKI_TTS_BACKEND", TTS_BACKEND).strip().lower()
    if backend == "kokoro":
        try:
            adapter = get_tts_adapter()
            if not adapter.is_loaded:
                adapter.load()
            return adapter
        except Exception as exc:
            LOGGER.warning("voice: Kokoro TTS load failed (%s), falling back to macOS system speech", exc)
    sys_adapter = get_system_tts_adapter()
    if not sys_adapter.is_loaded:
        sys_adapter.load()
    return sys_adapter


def get_voice_manager() -> VoiceSessionManager:
    global _voice_manager
    if _voice_manager is None:
        with _voice_manager_lock:
            if _voice_manager is None:
                from src.server.pipeline import run_pipeline
                from src.voice import VoiceSessionHandles, VoiceSessionManager

                def _handles_factory() -> VoiceSessionHandles:
                    stt_engine = get_stt_engine()
                    if not stt_engine.is_loaded:
                        try:
                            stt_engine.load()
                        except Exception as exc:
                            LOGGER.warning("voice: STT engine load failed — %s", exc)
                    tts_adapter = get_active_tts_adapter()
                    return VoiceSessionHandles(
                        stt_engine=stt_engine,
                        tts_adapter=tts_adapter,
                        pipeline_lock=pipeline_lock,
                        run_pipeline=run_pipeline,
                    )

                _voice_manager = VoiceSessionManager(handles_factory=_handles_factory)
                _voice_manager.start_sweeper()
    return _voice_manager
