from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "CONTRACTS": ("src.audio.stt.artifacts", "CONTRACTS"),
    "DEFAULT_UPDATE_INTERVAL": ("src.audio.stt.session", "DEFAULT_UPDATE_INTERVAL"),
    "FinalTranscript": ("src.audio.stt.session", "FinalTranscript"),
    "InvalidAudioDataError": ("src.audio.stt.exceptions", "InvalidAudioDataError"),
    "MAX_UTTERANCE_SECONDS": ("src.audio.stt.session", "MAX_UTTERANCE_SECONDS"),
    "ModelNotLoadedError": ("src.audio.stt.exceptions", "ModelNotLoadedError"),
    "MoonshineStreamingAdapter": ("src.audio.stt.adapter", "MoonshineStreamingAdapter"),
    "MoonshineStreamSession": ("src.audio.stt.session", "MoonshineStreamSession"),
    "MoonshineVariant": ("src.audio.stt.artifacts", "MoonshineVariant"),
    "SAMPLE_RATE_HZ": ("src.audio.stt.session", "SAMPLE_RATE_HZ"),
    "STTCancelledError": ("src.audio.stt.exceptions", "STTCancelledError"),
    "STTError": ("src.audio.stt.exceptions", "STTError"),
    "STTTimeoutError": ("src.audio.stt.exceptions", "STTTimeoutError"),
    "SessionConflictError": ("src.audio.stt.exceptions", "SessionConflictError"),
    "SessionNotFoundError": ("src.audio.stt.exceptions", "SessionNotFoundError"),
    "SessionState": ("src.audio.stt.session", "SessionState"),
    "SpeculativeMetrics": ("src.audio.stt.speculative", "SpeculativeMetrics"),
    "SpeculativeStreamSession": ("src.audio.stt.speculative", "SpeculativeStreamSession"),
    "SpeculativeStreamingEngine": ("src.audio.stt.speculative", "SpeculativeStreamingEngine"),
    "UtteranceLimitExceededError": ("src.audio.stt.exceptions", "UtteranceLimitExceededError"),
    "compute_spectral_entropy": ("src.audio.stt.speculative", "compute_spectral_entropy"),
    "get_moonshine_model_arch": ("src.audio.stt.artifacts", "get_moonshine_model_arch"),
    "load_transcriber": ("src.audio.stt.adapter", "load_transcriber"),
    "normalize_wake_prefix": ("src.audio.stt.normalization", "normalize_wake_prefix"),
    "parse_and_validate_f32le_pcm": ("src.audio.stt.normalization", "parse_and_validate_f32le_pcm"),
    "resolve_model_path": ("src.audio.stt.artifacts", "resolve_model_path"),
    "verify_artifacts": ("src.audio.stt.artifacts", "verify_artifacts"),
    "verify_crc32c": ("src.audio.stt.artifacts", "verify_crc32c"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
