from __future__ import annotations


class TTSError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class TTSModelNotLoadedError(TTSError):
    def __init__(self):
        super().__init__("voice.tts_model_not_loaded", "Speech model is not loaded")


class TTSValidationError(TTSError):
    def __init__(self, message: str):
        super().__init__("voice.tts_invalid_request", message)


class TTSCancelledError(TTSError):
    def __init__(self):
        super().__init__("voice.tts_cancelled", "Speech synthesis was cancelled")


class TTSStaleGenerationError(TTSError):
    def __init__(self):
        super().__init__("voice.tts_stale_generation", "Speech generation is no longer active")


class TTSSynthesisError(TTSError):
    def __init__(self):
        super().__init__("voice.tts_synthesis_failed", "Speech synthesis failed")


class TTSBackendUnavailableError(TTSError):
    def __init__(self, message: str = "Speech backend is unavailable"):
        super().__init__("voice.tts_backend_unavailable", message)


class TTSPlaybackError(TTSError):
    def __init__(self):
        super().__init__("voice.tts_playback_failed", "System speech playback failed")
