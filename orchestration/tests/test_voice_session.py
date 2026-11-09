from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import pytest

from src.voice.audio_queue import VoicePCMQueue
from src.voice.events import VoiceEventLog
from src.voice.exceptions import (
    VoiceBodyTooLargeError,
    VoiceInvalidStateError,
    VoiceSequenceGapError,
    VoiceSessionLimitError,
    VoiceSessionNotFoundError,
)
from src.voice.manager import VoiceSessionManager
from src.voice.session import (
    MAX_AUDIO_CHUNK_BYTES,
    VoiceSession,
    VoiceSessionHandles,
    VoiceSessionState,
)

SAMPLE_RATE_HZ = 16_000


def pcm_bytes(num_samples: int, value: float = 0.1) -> bytes:
    import struct

    return struct.pack(f"<{num_samples}f", *([value] * num_samples))


@dataclass
class FakeTranscript:
    text: str
    duration_seconds: float = 1.0


class FakeSTTSession:
    def __init__(self, session_id: str, on_partial=None, text: str = "turn on the lights"):
        self.session_id = session_id
        self._on_partial = on_partial
        self._text = text
        self.cancelled = False
        self.closed = False
        self.added_chunks: list[bytes] = []

    def add_audio(self, audio, is_final: bool = False):
        self.added_chunks.append(bytes(audio) if isinstance(audio, (bytes, bytearray)) else audio)
        if self._on_partial:
            self._on_partial("partial text", len(self.added_chunks))

    def finalize(self):
        return FakeTranscript(self._text)

    def cancel(self, reason: str = "cancelled"):
        self.cancelled = True

    def close(self):
        self.closed = True


class FakeSTTEngine:
    def __init__(self, text: str = "turn on the lights"):
        self.text = text
        self.loaded = True
        self.sessions: dict[str, FakeSTTSession] = {}

    @property
    def is_loaded(self):
        return self.loaded

    def load(self):
        self.loaded = True

    def create_session(self, session_id, *, activation_source="push_to_talk", on_partial=None, **_):
        session = FakeSTTSession(session_id, on_partial=on_partial, text=self.text)
        self.sessions[session_id] = session
        return session


@dataclass
class FakePCMChunk:
    pcm_f32le: bytes
    valid_samples: int
    is_final: bool
    sample_rate: int = 24_000
    channels: int = 1


class FakeTTSAdapter:
    def __init__(self, num_chunks: int = 2, fail: bool = False):
        self.num_chunks = num_chunks
        self.fail = fail
        self.loaded = True
        self.calls: list[str] = []
        self.cancel_calls = 0

    @property
    def is_loaded(self):
        return self.loaded

    def load(self):
        self.loaded = True

    def synthesize(self, text: str, session_id: str):
        self.calls.append(text)
        if self.fail:
            from src.audio.tts.exceptions import TTSSynthesisError

            raise TTSSynthesisError()
        return iter(
            FakePCMChunk(b"\x00\x00\x00\x00" * 10, 10, i == self.num_chunks - 1)
            for i in range(self.num_chunks)
        )

    def cancel(self, generation_id=None):
        self.cancel_calls += 1
        return True


def fake_run_pipeline_factory(message: str = "done", ask: str | None = None):
    def run_pipeline(command, on_step=None, on_message=None, ask_user=None):
        if on_message:
            on_message("info", f"handling: {command}")
        if ask and ask_user:
            answer = ask_user(ask)
            return {"status": "ok", "message": f"{message} (you said: {answer})"}
        return {"status": "ok", "message": message}

    return run_pipeline


def make_handles(stt_engine=None, tts_adapter=None, run_pipeline=None) -> VoiceSessionHandles:
    return VoiceSessionHandles(
        stt_engine=stt_engine or FakeSTTEngine(),
        tts_adapter=tts_adapter or FakeTTSAdapter(),
        pipeline_lock=threading.Lock(),
        run_pipeline=run_pipeline or fake_run_pipeline_factory(),
    )


def wait_for_state(session: VoiceSession, state: VoiceSessionState, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session.state == state:
            return
        time.sleep(0.01)
    pytest.fail(f"session never reached {state}, stuck at {session.state}")


def test_event_log_bounds_memory_and_reports_dropped():
    log = VoiceEventLog(max_buffered=3)
    for i in range(5):
        log.emit("tick", n=i)

    events, latest, dropped = log.poll(after=0)
    assert [e.data["n"] for e in events] == [2, 3, 4]
    assert latest == 5
    assert dropped == 2


def test_event_log_poll_cursor_only_returns_new_events():
    log = VoiceEventLog()
    log.emit("a")
    log.emit("b")
    events, _, _ = log.poll(after=1)
    assert [e.type for e in events] == ["b"]


def test_pcm_queue_trims_acknowledged_chunks_on_poll():
    queue = VoicePCMQueue(max_buffered_bytes=1_000_000)
    for i in range(3):
        assert queue.put(FakePCMChunk(b"\x00" * 10, 10, False))

    chunks, start, latest, closed, cancelled = queue.poll(after=0, limit=10)
    assert len(chunks) == 3
    assert start == 0 and latest == 3

    chunks, start, latest, closed, cancelled = queue.poll(after=2, limit=10)
    assert len(chunks) == 1
    assert start == 2 and latest == 3


def test_pcm_queue_backpressure_blocks_until_drained():
    queue = VoicePCMQueue(max_buffered_bytes=20)
    chunk = FakePCMChunk(b"\x00" * 20, 20, False)
    assert queue.put(chunk, timeout=1.0)

    blocked = threading.Event()
    result = {}

    def producer():
        blocked.set()
        result["ok"] = queue.put(FakePCMChunk(b"\x00" * 20, 20, False), timeout=2.0)

    t = threading.Thread(target=producer)
    t.start()
    blocked.wait()
    time.sleep(0.05)
    assert t.is_alive()

    queue.poll(after=1, limit=10)
    t.join(timeout=2.0)
    assert result["ok"] is True


def test_pcm_queue_put_returns_false_after_cancel():
    queue = VoicePCMQueue(max_buffered_bytes=10)
    queue.cancel()
    assert queue.put(FakePCMChunk(b"\x00" * 10, 10, False), timeout=0.1) is False


def test_push_audio_rejects_oversized_chunk():
    session = VoiceSession("s1", make_handles())
    with pytest.raises(VoiceBodyTooLargeError):
        session.push_audio(0, b"\x00" * (MAX_AUDIO_CHUNK_BYTES + 4), False)


def test_push_audio_sequence_gap_cancels_utterance():
    session = VoiceSession("s1", make_handles())
    session.push_audio(0, pcm_bytes(10), False)
    with pytest.raises(VoiceSequenceGapError):
        session.push_audio(5, pcm_bytes(10), False)
    assert session.state == VoiceSessionState.ERROR


def test_push_audio_duplicate_sequence_is_idempotent():
    session = VoiceSession("s1", make_handles())
    ack1 = session.push_audio(0, pcm_bytes(10), False)
    ack2 = session.push_audio(0, pcm_bytes(10), False)
    assert ack1["duplicate"] is False
    assert ack2["duplicate"] is True
    assert session.state == VoiceSessionState.LISTENING


def test_push_audio_after_listening_rejected():
    session = VoiceSession("s1", make_handles())
    session.push_audio(0, pcm_bytes(10), True)
    wait_for_state(session, VoiceSessionState.DONE)
    with pytest.raises(VoiceInvalidStateError):
        session.push_audio(1, pcm_bytes(10), False)


def test_full_turn_produces_transcript_assistant_text_and_audio():
    stt = FakeSTTEngine(text="turn on the lights")
    tts = FakeTTSAdapter(num_chunks=3)
    handles = make_handles(stt_engine=stt, tts_adapter=tts, run_pipeline=fake_run_pipeline_factory("lights on"))
    session = VoiceSession("s1", handles)

    session.push_audio(0, pcm_bytes(10), True)
    wait_for_state(session, VoiceSessionState.DONE)

    events, _, _ = session.poll_events(0)
    types = [e.type for e in events]
    assert "transcript.final" in types
    assert "assistant.text" in types
    assert "speaking.done" in types

    final_text_event = next(e for e in events if e.type == "assistant.text")
    assert final_text_event.data["text"] == "lights on"

    chunks, start, latest, closed, cancelled = session.pull_audio(0, limit=10)
    assert len(chunks) == 3
    assert closed is True
    assert cancelled is False


def test_empty_transcript_skips_pipeline_and_tts():
    stt = FakeSTTEngine(text="   ")
    tts = FakeTTSAdapter()
    handles = make_handles(stt_engine=stt, tts_adapter=tts)
    session = VoiceSession("s1", handles)

    session.push_audio(0, pcm_bytes(10), True)
    wait_for_state(session, VoiceSessionState.DONE)

    assert tts.calls == []
    events, _, _ = session.poll_events(0)
    assert any(e.type == "voice.no_speech" for e in events)


def test_question_and_typed_reply_round_trip():
    tts = FakeTTSAdapter(num_chunks=1)
    handles = make_handles(
        tts_adapter=tts,
        run_pipeline=fake_run_pipeline_factory("ok", ask="which room?"),
    )
    session = VoiceSession("s1", handles)

    session.push_audio(0, pcm_bytes(10), True)

    deadline = time.monotonic() + 2.0
    while session.state != VoiceSessionState.AWAITING_REPLY and time.monotonic() < deadline:
        time.sleep(0.01)
    assert session.state == VoiceSessionState.AWAITING_REPLY

    session.submit_reply("the kitchen")
    wait_for_state(session, VoiceSessionState.DONE)

    events, _, _ = session.poll_events(0)
    assert any(e.type == "question" and e.data["text"] == "which room?" for e in events)
    assert any(e.type == "answer" and e.data["text"] == "the kitchen" for e in events)
    final_text_event = next(e for e in events if e.type == "assistant.text")
    assert "the kitchen" in final_text_event.data["text"]


def test_finalize_with_transcript_bypasses_stt():
    stt = FakeSTTEngine()
    tts = FakeTTSAdapter(num_chunks=1)
    handles = make_handles(stt_engine=stt, tts_adapter=tts, run_pipeline=fake_run_pipeline_factory("ack"))
    session = VoiceSession("s1", handles)

    session.finalize_with_transcript("what time is it")
    wait_for_state(session, VoiceSessionState.DONE)

    assert stt.sessions["voice-s1"].cancelled is True
    assert stt.sessions["voice-s1"].added_chunks == []
    events, _, _ = session.poll_events(0)
    assert any(e.type == "transcript.final" and e.data["text"] == "what time is it" for e in events)


def test_cancel_stops_in_flight_speech_and_releases_stt():
    stt = FakeSTTEngine()
    session = VoiceSession("s1", make_handles(stt_engine=stt))
    session.cancel("user_requested")
    assert session.state == VoiceSessionState.CANCELLED
    assert stt.sessions["voice-s1"].cancelled is True


def test_tts_failure_marks_session_error_without_crashing_worker():
    tts = FakeTTSAdapter(fail=True)
    handles = make_handles(tts_adapter=tts)
    session = VoiceSession("s1", handles)

    session.push_audio(0, pcm_bytes(10), True)
    wait_for_state(session, VoiceSessionState.ERROR)

    events, _, _ = session.poll_events(0)
    assert any(e.type == "error" for e in events)


def test_stalled_audio_consumer_fails_session_instead_of_hanging():
    session = VoiceSession("s1", make_handles(tts_adapter=FakeTTSAdapter(num_chunks=5)))
    session.output_audio = VoicePCMQueue(max_buffered_bytes=1)
    original_put = session.output_audio.put
    session.output_audio.put = lambda chunk: original_put(chunk, timeout=0.05)

    session.push_audio(0, pcm_bytes(10), True)
    wait_for_state(session, VoiceSessionState.ERROR)

    events, _, _ = session.poll_events(0)
    assert any(e.type == "error" and e.data["code"] == "voice.audio_backpressure" for e in events)


def test_full_turn_with_system_speech_handle():
    class FakeSystemHandle:
        def __init__(self):
            self.waited = False

        def wait(self, timeout=None):
            self.waited = True
            return 0

    class FakeSystemTTSAdapter:
        def __init__(self):
            self.loaded = True
            self.calls: list[str] = []
            self.handles: list[FakeSystemHandle] = []

        @property
        def is_loaded(self):
            return self.loaded

        def load(self):
            self.loaded = True

        def synthesize(self, text: str, session_id: str):
            self.calls.append(text)
            h = FakeSystemHandle()
            self.handles.append(h)
            return h

        def cancel(self, generation_id=None):
            return True

    FakeSystemTTSAdapter.__name__ = "MacOSSystemSpeechAdapter"
    stt = FakeSTTEngine(text="open calculator")
    tts = FakeSystemTTSAdapter()
    handles = make_handles(
        stt_engine=stt,
        tts_adapter=tts,
        run_pipeline=fake_run_pipeline_factory("DONE: open_app: Calculator → click: [3]"),
    )
    session = VoiceSession("s-sys", handles)

    session.push_audio(0, pcm_bytes(10), True)
    wait_for_state(session, VoiceSessionState.DONE)

    assert tts.calls == ["Sure thing!", "I've opened Calculator for you."]
    assert len(tts.handles) == 2 and all(h.waited for h in tts.handles)
    events, _, _ = session.poll_events(0)
    assert any(e.type == "speaking.done" for e in events)
    _, _, _, closed, cancelled = session.pull_audio(0, limit=10)
    assert closed is True
    assert cancelled is False


def test_manager_enforces_max_concurrent_sessions():
    manager = VoiceSessionManager(handles_factory=make_handles, max_sessions=1)
    first = manager.create_session()
    with pytest.raises(VoiceSessionLimitError):
        manager.create_session()
    manager.release_session(first.session_id)
    second = manager.create_session()
    assert second.session_id != first.session_id


def test_manager_get_session_missing_raises():
    manager = VoiceSessionManager(handles_factory=make_handles)
    with pytest.raises(VoiceSessionNotFoundError):
        manager.get_session("does-not-exist")


def test_manager_sweeps_expired_sessions():
    manager = VoiceSessionManager(handles_factory=make_handles, session_ttl_seconds=0.01)
    session = manager.create_session()
    time.sleep(0.05)
    manager._sweep_expired()
    assert manager.active_session_count == 0
    with pytest.raises(VoiceSessionNotFoundError):
        manager.get_session(session.session_id)
