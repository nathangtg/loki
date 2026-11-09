from __future__ import annotations


class VoiceError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class VoiceSessionNotFoundError(VoiceError):
    def __init__(self, session_id: str):
        super().__init__(
            "voice.session_not_found",
            f"Voice session not found: {session_id}",
        )


class VoiceSessionExpiredError(VoiceError):
    def __init__(self, session_id: str):
        super().__init__(
            "voice.session_expired",
            f"Voice session expired from inactivity: {session_id}",
        )


class VoiceInvalidStateError(VoiceError):
    def __init__(self, state: str, operation: str = "operation"):
        super().__init__(
            "voice.invalid_state",
            f"Cannot perform {operation} in session state: {state}",
        )


class VoiceSequenceGapError(VoiceError):
    def __init__(self, expected: int, received: int):
        super().__init__(
            "voice.audio_gap",
            f"Expected sequence {expected}, received {received} — utterance cancelled",
        )


class VoiceBodyTooLargeError(VoiceError):
    def __init__(self, limit_bytes: int):
        super().__init__(
            "voice.body_too_large",
            f"Request body exceeds the {limit_bytes}-byte limit",
        )


class VoiceBackpressureError(VoiceError):
    def __init__(self, queue: str):
        super().__init__(
            "voice.backpressure",
            f"{queue} queue is full",
            retryable=True,
        )


class VoiceSessionLimitError(VoiceError):
    def __init__(self, max_sessions: int):
        super().__init__(
            "voice.session_limit",
            f"At most {max_sessions} concurrent voice session(s) are supported",
            retryable=True,
        )
