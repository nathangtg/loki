from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from src.audio.tts import (
    MacOSSystemSpeechAdapter,
    TTSBackendUnavailableError,
    TTSPlaybackError,
    TTSStaleGenerationError,
    TTSValidationError,
    format_for_speech,
)


class InputSink:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> int:
        self.data.extend(data)
        return len(data)

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    _next_pid = 1_000

    def __init__(self, *, terminate_timeout: bool = False, exit_code: int = 0):
        self.pid = FakeProcess._next_pid
        FakeProcess._next_pid += 1
        self.stdin = InputSink()
        self.returncode: int | None = None
        self.terminate_timeout = terminate_timeout
        self.exit_code = exit_code
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.terminate_calls and self.terminate_timeout and not self.kill_calls:
            raise subprocess.TimeoutExpired(["say"], timeout)
        if self.returncode is None:
            self.returncode = -9 if self.kill_calls else self.exit_code
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1
        if not self.terminate_timeout:
            self.returncode = -15

    def kill(self):
        self.kill_calls += 1
        self.returncode = -9


class ProcessFactory:
    def __init__(self, *processes: FakeProcess):
        self.processes = list(processes)
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return self.processes.pop(0)


def loaded_adapter(
    factory: ProcessFactory,
    *,
    voice: str | None = "Samantha",
    rate: int = 190,
) -> MacOSSystemSpeechAdapter:
    adapter = MacOSSystemSpeechAdapter(
        voice=voice,
        rate=rate,
        say_path=Path("/usr/bin/say"),
        process_factory=factory,
    )
    with (
        mock.patch("src.audio.tts.system.platform.system", return_value="Darwin"),
        mock.patch("src.audio.tts.system.os.access", return_value=True),
        mock.patch("src.audio.tts.system.Path.is_file", return_value=True),
    ):
        adapter.load()
    return adapter


def test_speaks_with_voice_and_rate_without_shell_or_kokoro_artifacts(caplog):
    process = FakeProcess()
    factory = ProcessFactory(process)
    adapter = loaded_adapter(factory)
    secret_text = "Private spoken response"

    with mock.patch(
        "src.audio.tts.artifacts.load_kokoro_runtime",
        side_effect=AssertionError("Kokoro artifacts must not be used"),
    ) as kokoro_loader:
        handle = adapter.synthesize(secret_text, "session-1")

    kokoro_loader.assert_not_called()
    argv, kwargs = factory.calls[0]
    assert argv == ["/usr/bin/say", "-v", "Samantha", "-r", "190"]
    assert secret_text not in argv
    assert kwargs["shell"] is False
    assert kwargs["stdin"] == subprocess.PIPE
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert bytes(process.stdin.data) == secret_text.encode("utf-8")
    assert process.stdin.closed
    assert secret_text not in caplog.text
    assert handle.wait() == 0


def test_cancellation_terminates_only_owned_child():
    owned = FakeProcess()
    unrelated = FakeProcess()
    adapter = loaded_adapter(ProcessFactory(owned))
    handle = adapter.synthesize("Cancel me.", "session-1")

    assert adapter.cancel(handle.generation_id)
    assert owned.terminate_calls == 1
    assert owned.kill_calls == 0
    assert unrelated.terminate_calls == 0
    assert unrelated.kill_calls == 0


def test_cancellation_kills_only_owned_child_after_timeout():
    owned = FakeProcess(terminate_timeout=True)
    adapter = loaded_adapter(ProcessFactory(owned))
    handle = adapter.synthesize("Cancel me.", "session-1")

    adapter.cancel(handle.generation_id)

    assert owned.terminate_calls == 1
    assert owned.kill_calls == 1


def test_new_request_rejects_stale_handle_and_stops_previous_child():
    first_process = FakeProcess()
    second_process = FakeProcess()
    adapter = loaded_adapter(ProcessFactory(first_process, second_process))
    stale = adapter.synthesize("First response.", "session-1")
    current = adapter.synthesize("Second response.", "session-2")

    assert first_process.terminate_calls == 1
    with pytest.raises(TTSStaleGenerationError):
        stale.wait()
    assert current.wait() == 0


def test_reset_stops_active_child_but_keeps_backend_loaded():
    process = FakeProcess()
    adapter = loaded_adapter(ProcessFactory(process))
    adapter.synthesize("Reset me.", "session-1")

    adapter.reset()

    assert process.terminate_calls == 1
    assert adapter.is_loaded


def test_nonzero_say_exit_is_reported_safely():
    process = FakeProcess(exit_code=1)
    adapter = loaded_adapter(ProcessFactory(process))
    handle = adapter.synthesize("Do not leak this text.", "session-1")

    with pytest.raises(TTSPlaybackError) as caught:
        handle.wait()
    assert "Do not leak" not in str(caught.value)


def test_voice_rate_text_and_session_are_validated():
    adapter = loaded_adapter(ProcessFactory(FakeProcess()))

    with pytest.raises(TTSValidationError):
        adapter.synthesize("", "session-1")
    with pytest.raises(TTSValidationError):
        adapter.synthesize("Valid.", "bad session")
    with pytest.raises(TTSValidationError):
        adapter.synthesize("Valid.", "session-1", voice="bad\nvoice")
    with pytest.raises(TTSValidationError):
        adapter.synthesize("Valid.", "session-1", rate=20)


def test_load_rejects_non_macos_platform():
    adapter = MacOSSystemSpeechAdapter(process_factory=ProcessFactory(FakeProcess()))

    with mock.patch("src.audio.tts.system.platform.system", return_value="Linux"):
        with pytest.raises(TTSBackendUnavailableError, match="requires macOS"):
            adapter.load()


def test_server_returns_configured_resident_system_adapter(monkeypatch: pytest.MonkeyPatch):
    from src.server import config

    monkeypatch.setattr(config, "_system_tts_adapter", None)
    monkeypatch.setenv("LOKI_SYSTEM_TTS_VOICE", "Alex")
    monkeypatch.setenv("LOKI_SYSTEM_TTS_RATE", "205")

    adapter = config.get_system_tts_adapter()

    assert adapter is config.get_system_tts_adapter()
    assert adapter.voice == "Alex"
    assert adapter.rate == 205


def test_server_rejects_invalid_system_rate(monkeypatch: pytest.MonkeyPatch):
    from src.server import config

    monkeypatch.setattr(config, "_system_tts_adapter", None)
    monkeypatch.setenv("LOKI_SYSTEM_TTS_RATE", "fast")

    with pytest.raises(TTSValidationError, match="must be an integer"):
        config.get_system_tts_adapter()


def test_system_speech_warmup_loads_resident_adapter(monkeypatch: pytest.MonkeyPatch):
    import importlib

    from src.server import config

    warmup = importlib.import_module("src.server.warmup")
    adapter = MacOSSystemSpeechAdapter(process_factory=ProcessFactory(FakeProcess()))
    loaded: list[bool] = []
    monkeypatch.setattr(adapter, "load", lambda: loaded.append(True))
    monkeypatch.setattr(config, "get_system_tts_adapter", lambda: adapter)

    warmup._warmup_system_tts()

    assert loaded == [True]


def test_format_for_speech_sanitizes_raw_action_chains_and_indices():
    segments = format_for_speech(
        "DONE: open_app: Calculator → click: [3]",
        goal="Can you open the calculator app for me?",
    )
    assert segments == ["Sure thing!", "I've opened Calculator for you."]


def test_format_for_speech_splits_sentences_and_strips_markdown():
    segments = format_for_speech(
        "Clicked [4] **Submit** on https://example.com/login/page. Your form is submitted!"
    )
    assert segments == [
        "Clicked Submit on example.com.",
        "Your form is submitted!",
    ]

