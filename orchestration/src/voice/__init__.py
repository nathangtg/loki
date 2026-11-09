from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "VoiceBackpressureError": ("src.voice.exceptions", "VoiceBackpressureError"),
    "VoiceBodyTooLargeError": ("src.voice.exceptions", "VoiceBodyTooLargeError"),
    "VoiceError": ("src.voice.exceptions", "VoiceError"),
    "VoiceEvent": ("src.voice.events", "VoiceEvent"),
    "VoiceEventLog": ("src.voice.events", "VoiceEventLog"),
    "VoiceInvalidStateError": ("src.voice.exceptions", "VoiceInvalidStateError"),
    "VoicePCMQueue": ("src.voice.audio_queue", "VoicePCMQueue"),
    "VoiceSequenceGapError": ("src.voice.exceptions", "VoiceSequenceGapError"),
    "VoiceSession": ("src.voice.session", "VoiceSession"),
    "VoiceSessionExpiredError": ("src.voice.exceptions", "VoiceSessionExpiredError"),
    "VoiceSessionHandles": ("src.voice.session", "VoiceSessionHandles"),
    "VoiceSessionLimitError": ("src.voice.exceptions", "VoiceSessionLimitError"),
    "VoiceSessionManager": ("src.voice.manager", "VoiceSessionManager"),
    "VoiceSessionNotFoundError": ("src.voice.exceptions", "VoiceSessionNotFoundError"),
    "VoiceSessionState": ("src.voice.session", "VoiceSessionState"),
    "new_session_id": ("src.voice.session", "new_session_id"),
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
