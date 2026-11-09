from __future__ import annotations

import os
import platform
import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.audio.tts.exceptions import (
    TTSBackendUnavailableError,
    TTSCancelledError,
    TTSPlaybackError,
    TTSStaleGenerationError,
    TTSValidationError,
)

DEFAULT_SAY_PATH = Path("/usr/bin/say")
DEFAULT_RATE = 180
MIN_RATE = 80
MAX_RATE = 500
MAX_TEXT_CHARS = 4_000
MAX_SPOKEN_SEGMENT_CHARS = 220
TERMINATE_TIMEOUT_SECONDS = 0.25
_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_PREFERRED_MACOS_VOICES = (
    "Samantha (Enhanced)",
    "Ava (Enhanced)",
    "Allison (Enhanced)",
    "Zoe (Enhanced)",
    "Tom (Enhanced)",
    "Evan (Enhanced)",
    "Siri",
    "Samantha",
    "Ava",
)
_cached_default_voice: str | None = None
_voice_detect_lock = threading.Lock()


def detect_preferred_macos_voice(say_path: Path = DEFAULT_SAY_PATH) -> str | None:
    global _cached_default_voice
    if _cached_default_voice is not None:
        return _cached_default_voice
    with _voice_detect_lock:
        if _cached_default_voice is not None:
            return _cached_default_voice
        if platform.system() != "Darwin" or not say_path.is_file():
            return None
        try:
            proc = subprocess.run(
                [str(say_path), "-v", "?"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            available: list[str] = []
            for line in proc.stdout.splitlines():
                parts = re.split(r"\s{2,}[a-z]{2}_[A-Z]{2}\s+", line.strip(), maxsplit=1)
                if parts and parts[0]:
                    available.append(parts[0].strip())
            avail_set = {v.lower(): v for v in available}
            for candidate in _PREFERRED_MACOS_VOICES:
                if candidate.lower() in avail_set:
                    _cached_default_voice = avail_set[candidate.lower()]
                    return _cached_default_voice
        except Exception:
            pass
        return None

_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?…])\s+|\n+")
_INDEX_TOKEN_PATTERN = re.compile(r"\[\d+\]")
_MARKDOWN_FENCE_PATTERN = re.compile(r"```.*?```", re.DOTALL)
_MARKDOWN_INLINE_PATTERN = re.compile(r"[`*_~#]+")
_URL_PATTERN = re.compile(r"https?://([^\s/]+?)(?:/[^\s]*?)?([.,!?;:]?)(?=\s|$)")
_RAW_ACTION_CHAIN_PATTERN = re.compile(
    r"^(?:DONE\s*:?\s*|Completed\s*:?\s*|Done\.\s*Completed\s+)?(?:(?:open_app|navigate|click|type_text|type_content|key_combo|read_page|scroll|wait)\s*:.*|"
    r"(?:Opened|Clicked|Navigated to|Typed|Pressed|Read page|Scrolled|Waited)\b.*→.*)$",
    re.IGNORECASE,
)
_POLITE_PREFIX_PATTERN = re.compile(
    r"^(?:hey\s+loki[,\s]*|loki[,\s]*|please\s+|can\s+you\s+(?:please\s+)?|could\s+you\s+(?:please\s+)?|"
    r"would\s+you\s+(?:please\s+)?|will\s+you\s+(?:please\s+)?|i\s+want\s+you\s+to\s+|"
    r"i'd\s+like\s+you\s+to\s+|go\s+ahead\s+and\s+)+",
    re.IGNORECASE,
)
_POLITE_SUFFIX_PATTERN = re.compile(
    r"\s+(?:for\s+me(?:\s+please)?|please|right\s+now|now)\s*$",
    re.IGNORECASE,
)
_OPEN_GOAL_PATTERN = re.compile(
    r"^(?:open|launch|start)(?:\s+up)?\s+(?:the\s+)?(.+?)(?:\s+app(?:lication)?)?$",
    re.IGNORECASE,
)
_SEARCH_GOAL_PATTERN = re.compile(
    r"^(?:search(?:\s+google)?\s+for|look\s+up|find)\s+(.+)$",
    re.IGNORECASE,
)
_NAVIGATE_GOAL_PATTERN = re.compile(
    r"^(?:go\s+to|navigate\s+to|open\s+up)\s+(.+)$",
    re.IGNORECASE,
)


def humanize_goal_completion(goal: str, history: list[dict[str, Any]] | None = None) -> str:
    if history:
        opened_apps = [
            str(h.get("target", "")).strip()
            for h in history
            if h.get("action") == "open_app" and str(h.get("target", "")).strip()
        ]
        if len(history) == 1 and opened_apps:
            return f"Sure thing! I've opened {opened_apps[-1]} for you."

    clean = " ".join((goal or "").split()).strip().rstrip(".!?")
    if not clean:
        return "All done! Let me know if you need anything else."

    core = _POLITE_PREFIX_PATTERN.sub("", clean).strip()
    core = _POLITE_SUFFIX_PATTERN.sub("", core).strip().rstrip(".!?")

    open_match = _OPEN_GOAL_PATTERN.match(core)
    if open_match:
        app_name = open_match.group(1).strip()
        if app_name.islower():
            app_name = app_name.title()
        return f"Sure thing! I've opened {app_name} for you."

    search_match = _SEARCH_GOAL_PATTERN.match(core)
    if search_match:
        topic = search_match.group(1).strip()
        return f"All set! I've pulled up the search results for {topic}."

    nav_match = _NAVIGATE_GOAL_PATTERN.match(core)
    if nav_match:
        dest = nav_match.group(1).strip()
        return f"Here you go! I've opened {dest} for you."

    if core:
        lowered = core[0].lower() + core[1:] if len(core) > 1 else core.lower()
        lowered = re.sub(r"\bmy\b", "your", lowered, flags=re.IGNORECASE)
        return f"All done! I've finished {lowered} for you." if lowered.endswith("ing") else "All set! I've taken care of that for you."

    return "All done! Let me know if you need anything else."


def format_for_speech(text: str, goal: str = "") -> list[str]:
    if not isinstance(text, str):
        return []
    cleaned = _MARKDOWN_FENCE_PATTERN.sub(" ", text).strip()
    if not cleaned:
        return []

    if (
        cleaned.upper() == "DONE"
        or cleaned.lower().startswith("done. completed ")
        or cleaned.lower().startswith("completed:")
        or _RAW_ACTION_CHAIN_PATTERN.match(cleaned)
    ):
        cleaned = humanize_goal_completion(goal)

    if cleaned.upper().startswith("DONE:"):
        cleaned = cleaned[5:].strip()

    cleaned = _URL_PATTERN.sub(r"\1\2", cleaned)
    cleaned = _INDEX_TOKEN_PATTERN.sub("", cleaned)
    cleaned = cleaned.replace("→", ", then ")
    cleaned = _MARKDOWN_INLINE_PATTERN.sub("", cleaned)
    cleaned = " ".join(cleaned.split()).strip()
    if not cleaned:
        return []

    segments: list[str] = []
    for raw_sentence in _SENTENCE_SPLIT_PATTERN.split(cleaned):
        sentence = raw_sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= MAX_SPOKEN_SEGMENT_CHARS:
            segments.append(sentence)
            continue
        words = sentence.split()
        current: list[str] = []
        current_len = 0
        for word in words:
            added = len(word) + (1 if current else 0)
            if current and current_len + added > MAX_SPOKEN_SEGMENT_CHARS:
                segments.append(" ".join(current))
                current = [word]
                current_len = len(word)
            else:
                current.append(word)
                current_len += added
        if current:
            segments.append(" ".join(current))
    return segments


@dataclass
class _SystemSpeechState:
    generation_id: int
    session_id: str
    process: Any
    cancelled: bool = False
    stale: bool = False


class SystemSpeechHandle:
    def __init__(
        self,
        adapter: MacOSSystemSpeechAdapter,
        state: _SystemSpeechState,
    ):
        self._adapter = adapter
        self._state = state
        self.generation_id = state.generation_id
        self.session_id = state.session_id

    @property
    def is_running(self) -> bool:
        return self._adapter._is_running(self._state)

    def wait(self, timeout: float | None = None) -> int:
        return self._adapter._wait(self._state, timeout)

    def cancel(self) -> bool:
        return self._adapter.cancel(self.generation_id)


class MacOSSystemSpeechAdapter:
    def __init__(
        self,
        *,
        voice: str | None = None,
        rate: int = DEFAULT_RATE,
        say_path: Path = DEFAULT_SAY_PATH,
        process_factory: Callable[..., Any] = subprocess.Popen,
    ):
        self.voice = _validate_voice(voice)
        self.rate = _validate_rate(rate)
        self.say_path = say_path
        self._process_factory = process_factory
        self._loaded = False
        self._generation_id = 0
        self._active: _SystemSpeechState | None = None
        self._state_lock = threading.Lock()
        self._operation_lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def load(self) -> None:
        if platform.system() != "Darwin":
            raise TTSBackendUnavailableError("System speech requires macOS")
        if not self.say_path.is_absolute() or not self.say_path.is_file():
            raise TTSBackendUnavailableError()
        if not os.access(self.say_path, os.X_OK):
            raise TTSBackendUnavailableError()
        self._loaded = True

    def synthesize(
        self,
        text: str,
        session_id: str,
        *,
        voice: str | None = None,
        rate: int | None = None,
    ) -> SystemSpeechHandle:
        text = _validate_text(text)
        _validate_session_id(session_id)
        selected_voice = self.voice if voice is None else _validate_voice(voice)
        selected_rate = self.rate if rate is None else _validate_rate(rate)

        with self._operation_lock:
            if not self._loaded:
                raise TTSBackendUnavailableError("System speech backend is not loaded")
            previous = self._detach_active(stale=True)
            if previous is not None:
                _terminate_process(previous.process)

            argv = [str(self.say_path)]
            if selected_voice is not None:
                argv.extend(("-v", selected_voice))
            argv.extend(("-r", str(selected_rate)))

            try:
                process = self._process_factory(
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    close_fds=True,
                )
            except OSError:
                raise TTSBackendUnavailableError() from None

            with self._state_lock:
                self._generation_id += 1
                state = _SystemSpeechState(self._generation_id, session_id, process)
                self._active = state

            try:
                if process.stdin is None:
                    raise OSError
                process.stdin.write(text.encode("utf-8"))
                process.stdin.close()
            except (BrokenPipeError, OSError):
                self._detach_state(state)
                _terminate_process(process)
                raise TTSPlaybackError() from None

            return SystemSpeechHandle(self, state)

    def cancel(self, generation_id: int | None = None) -> bool:
        with self._operation_lock:
            with self._state_lock:
                state = self._active
                if state is None:
                    return False
                if generation_id is not None and generation_id != state.generation_id:
                    raise TTSStaleGenerationError()
                state.cancelled = True
                self._active = None
            _terminate_process(state.process)
            return True

    def reset(self) -> None:
        with self._operation_lock:
            state = self._detach_active(stale=True)
            if state is not None:
                _terminate_process(state.process)

    def _detach_active(self, *, stale: bool) -> _SystemSpeechState | None:
        with self._state_lock:
            state = self._active
            if state is not None:
                state.stale = stale
                self._active = None
            return state

    def _detach_state(self, state: _SystemSpeechState) -> None:
        with self._state_lock:
            if self._active is state:
                self._active = None

    def _wait(self, state: _SystemSpeechState, timeout: float | None) -> int:
        return_code = state.process.wait(timeout=timeout)
        self._detach_state(state)
        if state.stale:
            raise TTSStaleGenerationError()
        if state.cancelled:
            raise TTSCancelledError()
        if return_code != 0:
            raise TTSPlaybackError()
        return return_code

    def _is_running(self, state: _SystemSpeechState) -> bool:
        if state.stale or state.cancelled:
            return False
        running = state.process.poll() is None
        if not running:
            self._detach_state(state)
        return running


def _validate_text(text: str) -> str:
    if not isinstance(text, str):
        raise TTSValidationError("Speech text must be a string")
    text = text.strip()
    if not text:
        raise TTSValidationError("Speech text must not be empty")
    if len(text) > MAX_TEXT_CHARS:
        raise TTSValidationError(f"Speech text exceeds {MAX_TEXT_CHARS} characters")
    if "\x00" in text:
        raise TTSValidationError("Speech text contains an invalid character")
    return text


def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise TTSValidationError("Session ID has an invalid format")


def _validate_voice(voice: str | None) -> str | None:
    if voice is None:
        return None
    if not isinstance(voice, str):
        raise TTSValidationError("System voice must be a string")
    voice = voice.strip()
    if not voice or len(voice) > 128 or any(ord(char) < 32 for char in voice):
        raise TTSValidationError("System voice has an invalid format")
    return voice


def _validate_rate(rate: int) -> int:
    if isinstance(rate, bool) or not isinstance(rate, int):
        raise TTSValidationError("Speaking rate must be an integer")
    if not MIN_RATE <= rate <= MAX_RATE:
        raise TTSValidationError(
            f"Speaking rate must be between {MIN_RATE} and {MAX_RATE} words per minute"
        )
    return rate


def _terminate_process(process: Any) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=TERMINATE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=TERMINATE_TIMEOUT_SECONDS)
    except ProcessLookupError:
        return
