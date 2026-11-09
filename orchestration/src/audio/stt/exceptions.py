from __future__ import annotations


class STTError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class ModelNotLoadedError(STTError):
    def __init__(self, message: str = "Moonshine model is not loaded"):
        super().__init__("voice.stt_model_not_loaded", message, retryable=False)


class SessionConflictError(STTError):
    def __init__(self, session_id: str):
        super().__init__(
            "voice.session_conflict",
            f"An active session is already using session ID: {session_id}",
            retryable=False,
        )


class SessionNotFoundError(STTError):
    def __init__(self, session_id: str):
        super().__init__(
            "voice.session_not_found",
            f"Session not found or already terminated: {session_id}",
            retryable=False,
        )


class UtteranceLimitExceededError(STTError):
    def __init__(self, seconds: float = 30.0):
        super().__init__(
            "voice.utterance_limit_exceeded",
            f"Utterance duration exceeded maximum limit of {seconds:.1f}s",
            retryable=False,
        )


class InvalidAudioDataError(STTError):
    def __init__(self, details: str):
        super().__init__(
            "voice.invalid_audio_data",
            f"Invalid audio data: {details}",
            retryable=False,
        )


class STTTimeoutError(STTError):
    def __init__(self, message: str = "STT finalization timed out"):
        super().__init__("voice.stt_timeout", message, retryable=True)


class STTCancelledError(STTError):
    def __init__(self, message: str = "Voice session was cancelled"):
        super().__init__("voice.session_cancelled", message, retryable=False)
