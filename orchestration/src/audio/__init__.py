from __future__ import annotations

from importlib import import_module
from typing import Any

_STT_EXPORTS = {
    "CONTRACTS",
    "DEFAULT_UPDATE_INTERVAL",
    "FinalTranscript",
    "InvalidAudioDataError",
    "MAX_UTTERANCE_SECONDS",
    "ModelNotLoadedError",
    "MoonshineStreamingAdapter",
    "MoonshineStreamSession",
    "MoonshineVariant",
    "SAMPLE_RATE_HZ",
    "STTCancelledError",
    "STTError",
    "STTTimeoutError",
    "SessionConflictError",
    "SessionNotFoundError",
    "SessionState",
    "SpeculativeMetrics",
    "SpeculativeStreamSession",
    "SpeculativeStreamingEngine",
    "UtteranceLimitExceededError",
    "compute_spectral_entropy",
    "load_transcriber",
    "normalize_wake_prefix",
    "parse_and_validate_f32le_pcm",
    "resolve_model_path",
    "verify_artifacts",
}
_TTS_EXPORTS = {
    "KokoroStreamingAdapter",
    "MacOSSystemSpeechAdapter",
    "PCMChunk",
    "PCM_FRAME_BYTES",
    "PCM_FRAME_SAMPLES",
    "PCM_SAMPLE_RATE_HZ",
    "SynthesisStream",
    "SystemSpeechHandle",
    "TTSBackendUnavailableError",
    "TTSCancelledError",
    "TTSError",
    "TTSModelNotLoadedError",
    "TTSPlaybackError",
    "TTSStaleGenerationError",
    "TTSSynthesisError",
    "TTSValidationError",
}

__all__ = sorted(_STT_EXPORTS | _TTS_EXPORTS)


def __getattr__(name: str) -> Any:
    if name in _STT_EXPORTS:
        module_name = "src.audio.stt"
    elif name in _TTS_EXPORTS:
        module_name = "src.audio.tts"
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
