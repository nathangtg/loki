from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "KokoroArtifactContract": ("src.audio.tts.artifacts", "KokoroArtifactContract"),
    "KokoroRuntime": ("src.audio.tts.artifacts", "KokoroRuntime"),
    "KokoroStreamingAdapter": ("src.audio.tts.adapter", "KokoroStreamingAdapter"),
    "MacOSSystemSpeechAdapter": ("src.audio.tts.system", "MacOSSystemSpeechAdapter"),
    "PCMChunk": ("src.audio.tts.adapter", "PCMChunk"),
    "PCM_FRAME_BYTES": ("src.audio.tts.adapter", "PCM_FRAME_BYTES"),
    "PCM_FRAME_SAMPLES": ("src.audio.tts.adapter", "PCM_FRAME_SAMPLES"),
    "PCM_SAMPLE_RATE_HZ": ("src.audio.tts.adapter", "PCM_SAMPLE_RATE_HZ"),
    "SynthesisStream": ("src.audio.tts.adapter", "SynthesisStream"),
    "SystemSpeechHandle": ("src.audio.tts.system", "SystemSpeechHandle"),
    "detect_preferred_macos_voice": ("src.audio.tts.system", "detect_preferred_macos_voice"),
    "format_for_speech": ("src.audio.tts.system", "format_for_speech"),
    "humanize_goal_completion": ("src.audio.tts.system", "humanize_goal_completion"),
    "TTSArtifactFile": ("src.audio.tts.artifacts", "TTSArtifactFile"),
    "TTSBackendUnavailableError": ("src.audio.tts.exceptions", "TTSBackendUnavailableError"),
    "TTSCancelledError": ("src.audio.tts.exceptions", "TTSCancelledError"),
    "TTSError": ("src.audio.tts.exceptions", "TTSError"),
    "TTSModelNotLoadedError": ("src.audio.tts.exceptions", "TTSModelNotLoadedError"),
    "TTSPlaybackError": ("src.audio.tts.exceptions", "TTSPlaybackError"),
    "TTSStaleGenerationError": ("src.audio.tts.exceptions", "TTSStaleGenerationError"),
    "TTSSynthesisError": ("src.audio.tts.exceptions", "TTSSynthesisError"),
    "TTSValidationError": ("src.audio.tts.exceptions", "TTSValidationError"),
    "load_artifact_contract": ("src.audio.tts.artifacts", "load_artifact_contract"),
    "load_kokoro_runtime": ("src.audio.tts.artifacts", "load_kokoro_runtime"),
    "resolve_model_path": ("src.audio.tts.artifacts", "resolve_model_path"),
    "verify_artifacts": ("src.audio.tts.artifacts", "verify_artifacts"),
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
