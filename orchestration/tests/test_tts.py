from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from src.audio.tts import (
    PCM_FRAME_SAMPLES,
    PCM_SAMPLE_RATE_HZ,
    KokoroStreamingAdapter,
    TTSCancelledError,
    TTSSynthesisError,
    TTSStaleGenerationError,
    TTSValidationError,
)
from src.audio.tts import KokoroRuntime


@dataclass
class FakeResult:
    audio: np.ndarray
    sample_rate: int = PCM_SAMPLE_RATE_HZ


class FakeModel:
    def __init__(self, results: list[FakeResult]):
        self.results = results
        self.calls: list[dict] = []
        self._pipelines = {"a": object()}

    def generate(self, text: str, **kwargs):
        self.calls.append({"text": text, **kwargs})
        yield from self.results


def adapter_for(model: object) -> KokoroStreamingAdapter:
    runtime = KokoroRuntime(model, Path("/local/af_heart.safetensors"), "a")
    adapter = KokoroStreamingAdapter(runtime_loader=lambda _: runtime)
    adapter.load()
    return adapter


def decode(chunk) -> np.ndarray:
    return np.frombuffer(chunk.pcm_f32le, dtype="<f4")


def test_yields_bounded_playable_chunk_before_model_generator_finishes():
    events: list[str] = []

    class IncrementalModel(FakeModel):
        def generate(self, text: str, **kwargs):
            self.calls.append({"text": text, **kwargs})
            yield FakeResult(np.full(PCM_FRAME_SAMPLES * 2, 0.25, dtype=np.float32))
            events.append("resumed")
            yield FakeResult(np.full(PCM_FRAME_SAMPLES, 0.5, dtype=np.float32))

    model = IncrementalModel([])
    stream = adapter_for(model).synthesize("First sentence. Second sentence.", "session-1")

    first = next(stream)

    assert events == []
    assert model.calls[0]["text"] == "First sentence.\nSecond sentence."
    assert first.sequence == 0
    assert first.sample_rate == PCM_SAMPLE_RATE_HZ
    assert first.channels == 1
    assert first.valid_samples == PCM_FRAME_SAMPLES
    assert len(first.pcm_f32le) == PCM_FRAME_SAMPLES * 4
    assert np.allclose(decode(first), 0.25)
    assert all(len(chunk.pcm_f32le) == PCM_FRAME_SAMPLES * 4 for chunk in stream)
    assert events == ["resumed"]


def test_cancellation_stops_further_chunks():
    adapter = adapter_for(
        FakeModel([FakeResult(np.ones(PCM_FRAME_SAMPLES * 3, dtype=np.float32))])
    )
    stream = adapter.synthesize("Cancel this response.", "session-1")
    next(stream)

    assert adapter.cancel(stream.generation_id)
    with pytest.raises(TTSCancelledError):
        next(stream)


def test_new_generation_rejects_stale_stream():
    adapter = adapter_for(
        FakeModel([FakeResult(np.ones(PCM_FRAME_SAMPLES * 2, dtype=np.float32))])
    )
    stale = adapter.synthesize("Old response.", "session-old")
    current = adapter.synthesize("Current response.", "session-current")

    with pytest.raises(TTSStaleGenerationError):
        next(stale)
    assert list(current)[-1].is_final


def test_model_is_reused_without_cross_session_audio_leakage():
    model = FakeModel([])

    def generate(text: str, **kwargs):
        model.calls.append({"text": text, **kwargs})
        value = 0.2 if len(model.calls) == 1 else 0.4
        count = PCM_FRAME_SAMPLES + 20 if len(model.calls) == 1 else PCM_FRAME_SAMPLES
        yield FakeResult(np.full(count, value, dtype=np.float32))

    model.generate = generate
    adapter = adapter_for(model)

    first = list(adapter.synthesize("First.", "session-1"))
    second = list(adapter.synthesize("Second.", "session-2"))

    assert len(model.calls) == 2
    assert [chunk.valid_samples for chunk in first] == [PCM_FRAME_SAMPLES, 20]
    assert [chunk.valid_samples for chunk in second] == [PCM_FRAME_SAMPLES]
    assert second[0].sequence == 0
    assert np.allclose(decode(second[0]), 0.4)


def test_resamples_to_canonical_24khz_mono_f32le():
    source = np.linspace(-0.5, 0.5, PCM_FRAME_SAMPLES * 2, dtype=np.float32)
    adapter = adapter_for(FakeModel([FakeResult(source, sample_rate=48_000)]))

    chunks = list(adapter.synthesize("Resample this.", "session-1"))

    assert len(chunks) == 1
    assert chunks[0].sample_rate == PCM_SAMPLE_RATE_HZ
    assert chunks[0].valid_samples == PCM_FRAME_SAMPLES
    assert decode(chunks[0]).dtype == np.dtype("float32")


def test_reset_invalidates_stream_and_preserves_resident_model():
    model = FakeModel([FakeResult(np.ones(PCM_FRAME_SAMPLES * 2, dtype=np.float32))])
    adapter = adapter_for(model)
    stream = adapter.synthesize("Reset this.", "session-1")

    adapter.reset()

    assert adapter.model is model
    assert model._pipelines == {}
    with pytest.raises(TTSStaleGenerationError):
        next(stream)


def test_text_and_session_validation_fail_fast():
    adapter = adapter_for(FakeModel([]))

    with pytest.raises(TTSValidationError):
        adapter.synthesize("", "session-1")
    with pytest.raises(TTSValidationError):
        adapter.synthesize("x" * 4_001, "session-1")
    with pytest.raises(TTSValidationError):
        adapter.synthesize("Valid text.", "bad session")


def test_language_processing_failure_returns_safe_error():
    secret_text = "private response text"

    class BrokenModel(FakeModel):
        def generate(self, text: str, **kwargs):
            raise ValueError(f"failed while processing {text}")

    stream = adapter_for(BrokenModel([])).synthesize(secret_text, "session-1")

    with pytest.raises(TTSSynthesisError) as caught:
        next(stream)
    assert secret_text not in str(caught.value)


def test_server_returns_one_resident_adapter(monkeypatch: pytest.MonkeyPatch):
    from src.server import config

    monkeypatch.setattr(config, "_tts_adapter", None)

    assert config.get_tts_adapter() is config.get_tts_adapter()


def test_tts_warmup_loads_resident_adapter(monkeypatch: pytest.MonkeyPatch):
    import importlib

    from src.server import config

    warmup = importlib.import_module("src.server.warmup")
    adapter = KokoroStreamingAdapter(runtime_loader=lambda _: None)
    loaded: list[bool] = []
    monkeypatch.setattr(adapter, "load", lambda: loaded.append(True))
    monkeypatch.setattr(config, "get_tts_adapter", lambda: adapter)

    warmup._warmup_tts()

    assert loaded == [True]
