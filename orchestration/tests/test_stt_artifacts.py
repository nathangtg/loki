from __future__ import annotations

import importlib
import json
import os
import struct
import tempfile
from pathlib import Path
from unittest import mock

import pytest

class TestDependencyImport:
    def test_import_moonshine_voice(self):
        importlib.import_module("moonshine_voice")

    def test_version_is_pinned(self):
        import moonshine_voice

        assert moonshine_voice.__version__ == "0.1.5", (
            f"Expected moonshine-voice 0.1.5, got {moonshine_voice.__version__}"
        )

    def test_native_library_loads(self):
        from moonshine_voice.moonshine_api import moonshine_get_stt_catalog_string

        catalog_json = moonshine_get_stt_catalog_string()
        catalog = json.loads(catalog_json)
        assert "languages" in catalog

    def test_model_arch_enum_values(self):
        from moonshine_voice.moonshine_api import ModelArch

        assert int(ModelArch.SMALL_STREAMING) == 4
        assert int(ModelArch.TINY_STREAMING) == 2


class TestArtifactContract:
    def test_small_streaming_contract_matches_native(self):
        from moonshine_voice.moonshine_api import ModelArch
        from src.audio.stt.artifacts import CONTRACTS, MoonshineVariant
        from moonshine_voice.download import _stt_dependency_manifest

        contract = CONTRACTS[MoonshineVariant.SMALL_STREAMING]
        native = _stt_dependency_manifest("en", ModelArch.SMALL_STREAMING)
        native_files = native["groups"][0]["files"]

        assert len(contract.files) == len(native_files)
        for cf, nf in zip(contract.files, native_files):
            assert cf.name == nf["name"], f"Name mismatch: {cf.name} vs {nf['name']}"
            assert cf.size_bytes == nf["size"], (
                f"{cf.name}: size {cf.size_bytes} vs {nf['size']}"
            )
            assert cf.crc32c_b64 == nf["checksum"], (
                f"{cf.name}: crc32c {cf.crc32c_b64} vs {nf['checksum']}"
            )

    def test_tiny_streaming_contract_matches_native(self):
        from moonshine_voice.moonshine_api import ModelArch
        from src.audio.stt.artifacts import CONTRACTS, MoonshineVariant
        from moonshine_voice.download import _stt_dependency_manifest

        contract = CONTRACTS[MoonshineVariant.TINY_STREAMING]
        native = _stt_dependency_manifest("en", ModelArch.TINY_STREAMING)
        native_files = native["groups"][0]["files"]

        assert len(contract.files) == len(native_files)
        for cf, nf in zip(contract.files, native_files):
            assert cf.name == nf["name"]
            assert cf.size_bytes == nf["size"]
            assert cf.crc32c_b64 == nf["checksum"]

    def test_total_bytes_correct(self):
        from src.audio.stt.artifacts import CONTRACTS

        for variant, contract in CONTRACTS.items():
            total = sum(f.size_bytes for f in contract.files)
            assert contract.total_bytes == total, (
                f"{variant.value}: declared total_bytes={contract.total_bytes} "
                f"vs computed={total}"
            )

    def test_all_files_have_crc32c(self):
        from src.audio.stt.artifacts import CONTRACTS

        for variant, contract in CONTRACTS.items():
            for f in contract.files:
                assert f.crc32c_b64, f"{variant.value}/{f.name} missing crc32c"

    def test_license_is_mit(self):
        from src.audio.stt.artifacts import CONTRACTS

        for contract in CONTRACTS.values():
            assert contract.license == "MIT"

    def test_moonshine_voice_version_pinned(self):
        from src.audio.stt.artifacts import CONTRACTS

        for contract in CONTRACTS.values():
            assert contract.moonshine_voice_version == "0.1.5"


class TestPathResolution:
    def test_env_var_takes_precedence(self):
        from src.audio.stt.artifacts import MoonshineVariant, resolve_model_path

        with mock.patch.dict(os.environ, {"LOKI_STT_MODEL_PATH": "/custom/stt/path"}):
            p = resolve_model_path(MoonshineVariant.SMALL_STREAMING)
            assert p == Path("/custom/stt/path")

    def test_override_root(self):
        from src.audio.stt.artifacts import (
            CONTRACTS,
            MoonshineVariant,
            resolve_model_path,
        )

        override = Path("/override/root")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOKI_STT_MODEL_PATH", None)
            p = resolve_model_path(
                MoonshineVariant.SMALL_STREAMING, override_root=override
            )
            contract = CONTRACTS[MoonshineVariant.SMALL_STREAMING]
            assert p == override / contract.relative_cache_path

    def test_platform_default_is_moonshine_cache(self):
        from src.audio.stt.artifacts import MoonshineVariant, resolve_model_path

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOKI_STT_MODEL_PATH", None)
            p = resolve_model_path(MoonshineVariant.SMALL_STREAMING)
            assert "moonshine_voice" in str(p) or "moonshine" in str(p).lower()
            assert p.name == "quantized_26_08_21"

    def test_both_variants_have_different_paths(self):
        from src.audio.stt.artifacts import MoonshineVariant, resolve_model_path

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOKI_STT_MODEL_PATH", None)
            small = resolve_model_path(MoonshineVariant.SMALL_STREAMING)
            tiny = resolve_model_path(MoonshineVariant.TINY_STREAMING)
            assert small != tiny
            assert "small-streaming" in str(small)
            assert "tiny-streaming" in str(tiny)


class TestArtifactVerification:
    def test_missing_dir_reports_error(self):
        from src.audio.stt.artifacts import MoonshineVariant, verify_artifacts

        errors = verify_artifacts(
            MoonshineVariant.SMALL_STREAMING,
            model_dir=Path("/nonexistent/dir"),
        )
        assert len(errors) == 1
        assert "does not exist" in errors[0]

    def test_empty_dir_reports_all_missing(self):
        from src.audio.stt.artifacts import (
            CONTRACTS,
            MoonshineVariant,
            verify_artifacts,
        )

        with tempfile.TemporaryDirectory() as td:
            errors = verify_artifacts(
                MoonshineVariant.SMALL_STREAMING,
                model_dir=Path(td),
            )
            expected_count = len(CONTRACTS[MoonshineVariant.SMALL_STREAMING].files)
            assert len(errors) == expected_count
            for e in errors:
                assert "Missing file" in e

    def test_correct_sizes_pass(self):
        from src.audio.stt.artifacts import CONTRACTS, MoonshineVariant, verify_artifacts

        contract = CONTRACTS[MoonshineVariant.TINY_STREAMING]
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            for af in contract.files:
                fp = td_path / af.name
                fp.write_bytes(b"\x00" * af.size_bytes)

            errors = verify_artifacts(
                MoonshineVariant.TINY_STREAMING,
                model_dir=td_path,
                check_sizes=True,
            )
            assert errors == []

    def test_wrong_size_detected(self):
        from src.audio.stt.artifacts import CONTRACTS, MoonshineVariant, verify_artifacts

        contract = CONTRACTS[MoonshineVariant.TINY_STREAMING]
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            for af in contract.files:
                fp = td_path / af.name
                if af.name == "adapter.ort":
                    fp.write_bytes(b"\x00" * (af.size_bytes + 1))
                else:
                    fp.write_bytes(b"\x00" * af.size_bytes)

            errors = verify_artifacts(
                MoonshineVariant.TINY_STREAMING,
                model_dir=td_path,
                check_sizes=True,
            )
            assert len(errors) == 1
            assert "adapter.ort" in errors[0]
            assert "Size mismatch" in errors[0]

    def test_skip_size_check(self):
        from src.audio.stt.artifacts import CONTRACTS, MoonshineVariant, verify_artifacts

        contract = CONTRACTS[MoonshineVariant.TINY_STREAMING]
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            for af in contract.files:
                (td_path / af.name).write_bytes(b"\x00")

            errors = verify_artifacts(
                MoonshineVariant.TINY_STREAMING,
                model_dir=td_path,
                check_sizes=False,
            )
            assert errors == []


class TestModelArchBridge:
    def test_small_streaming_arch(self):
        from src.audio.stt.artifacts import MoonshineVariant, get_moonshine_model_arch

        assert get_moonshine_model_arch(MoonshineVariant.SMALL_STREAMING) == 4

    def test_tiny_streaming_arch(self):
        from src.audio.stt.artifacts import MoonshineVariant, get_moonshine_model_arch

        assert get_moonshine_model_arch(MoonshineVariant.TINY_STREAMING) == 2


class TestSTTLoader:
    def test_missing_dir_raises_file_not_found(self):
        from src.audio.stt import load_transcriber
        from src.audio.stt.artifacts import MoonshineVariant

        with pytest.raises(FileNotFoundError, match="does not exist"):
            load_transcriber(
                MoonshineVariant.SMALL_STREAMING,
                model_dir=Path("/nonexistent/model/dir"),
            )

    def test_verify_false_skips_check(self):
        from src.audio import stt

        with mock.patch.object(stt, "verify_artifacts") as mock_verify:
            with pytest.raises(Exception):
                stt.load_transcriber(
                    stt.MoonshineVariant.SMALL_STREAMING,
                    model_dir=Path("/fake"),
                    verify=False,
                )
            mock_verify.assert_not_called()


class TestInputValidationAndNormalization:
    def test_f32_byte_parsing_and_clamping(self):
        from src.audio.stt import parse_and_validate_f32le_pcm

        raw_bytes = struct.pack("<3f", 0.5, 1.5, -2.0)
        parsed = parse_and_validate_f32le_pcm(raw_bytes)
        assert len(parsed) == 3
        assert abs(parsed[0] - 0.5) < 1e-6
        assert parsed[1] == 1.0
        assert parsed[2] == -1.0

    def test_unaligned_bytes_rejected(self):
        from src.audio.stt import parse_and_validate_f32le_pcm, InvalidAudioDataError

        with pytest.raises(InvalidAudioDataError, match="not aligned"):
            parse_and_validate_f32le_pcm(b"\x00\x00\x00")

    def test_nan_and_inf_rejected(self):
        from src.audio.stt import parse_and_validate_f32le_pcm, InvalidAudioDataError

        nan_bytes = struct.pack("<f", float("nan"))
        with pytest.raises(InvalidAudioDataError, match="NaN"):
            parse_and_validate_f32le_pcm(nan_bytes)

        inf_bytes = struct.pack("<f", float("inf"))
        with pytest.raises(InvalidAudioDataError, match="Infinite"):
            parse_and_validate_f32le_pcm(inf_bytes)

    def test_wake_prefix_normalization_exact_leading(self):
        from src.audio.stt import normalize_wake_prefix

        cleaned, removed = normalize_wake_prefix("Hey Loki, open Safari")
        assert removed is True
        assert cleaned == "open Safari"

        cleaned, removed = normalize_wake_prefix("hey loki open Calendar")
        assert removed is True
        assert cleaned == "open Calendar"

        cleaned, removed = normalize_wake_prefix("open Safari please")
        assert removed is False
        assert cleaned == "open Safari please"

        cleaned, removed = normalize_wake_prefix("tell hey loki to do this")
        assert removed is False
        assert cleaned == "tell hey loki to do this"


class TestStreamingAdapterIntegration:
    @pytest.fixture(scope="module")
    def loaded_adapter(self):
        from src.audio.stt import MoonshineStreamingAdapter, MoonshineVariant
        from src.audio.stt.artifacts import resolve_model_path

        path = resolve_model_path(MoonshineVariant.TINY_STREAMING)
        if not path.is_dir():
            pytest.skip("Tiny Streaming weights not downloaded yet")

        adapter = MoonshineStreamingAdapter(variant=MoonshineVariant.TINY_STREAMING)
        adapter.load()
        yield adapter
        adapter.unload()

    def test_adapter_reuse_across_sessions(self, loaded_adapter):
        assert loaded_adapter.is_loaded

        s1 = loaded_adapter.create_session("sess-1")
        silence = struct.pack("<1600f", *([0.0] * 1600))
        s1.add_audio(silence)
        t1 = s1.finalize()
        loaded_adapter.release_session("sess-1")
        assert t1.session_id == "sess-1"

        s2 = loaded_adapter.create_session("sess-2")
        s2.add_audio(silence)
        t2 = s2.finalize()
        loaded_adapter.release_session("sess-2")
        assert t2.session_id == "sess-2"

    def test_3_to_5_second_command_transcription_from_pcm(self, loaded_adapter):
        import moonshine_voice
        from moonshine_voice.utils import load_wav_file

        assets_dir = Path(moonshine_voice.__file__).parent / "assets"
        wav_path = assets_dir / "beckett.wav"
        if not wav_path.exists():
            pytest.skip("beckett.wav fixture not found")

        audio_floats, sr = load_wav_file(str(wav_path))
        pcm_bytes = struct.pack(f"<{len(audio_floats)}f", *audio_floats)

        partials: list[str] = []
        final_holder: list = []

        session = loaded_adapter.create_session(
            "test-beckett",
            on_partial=lambda text, rev: partials.append(text),
            on_final=lambda transcript: final_holder.append(transcript),
        )

        chunk_size = 1600 * 4
        for offset in range(0, len(pcm_bytes), chunk_size):
            chunk = pcm_bytes[offset : offset + chunk_size]
            session.add_audio(chunk)

        result = session.finalize()
        loaded_adapter.release_session("test-beckett")

        assert len(result.text) > 0
        assert "tried" in result.text.lower() or "fail" in result.text.lower()
        assert result.duration_seconds > 0.0
        assert len(final_holder) == 1
        assert final_holder[0].text == result.text

    def test_session_isolation(self, loaded_adapter):
        s1 = loaded_adapter.create_session("iso-1")
        s2 = loaded_adapter.create_session("iso-2")

        silence = struct.pack("<1600f", *([0.0] * 1600))
        s1.add_audio(silence)
        s2.add_audio(silence)

        t1 = s1.finalize()
        t2 = s2.finalize()

        assert t1.session_id == "iso-1"
        assert t2.session_id == "iso-2"

        loaded_adapter.release_session("iso-1")
        loaded_adapter.release_session("iso-2")

    def test_utterance_cap_enforced(self, loaded_adapter):
        from src.audio.stt import UtteranceLimitExceededError

        session = loaded_adapter.create_session("test-cap")
        excess_floats = [0.0] * 496_000
        with pytest.raises(UtteranceLimitExceededError):
            session.add_audio(excess_floats)

        loaded_adapter.release_session("test-cap")

class TestSpeculativeStreamingEngine:
    def test_compute_spectral_entropy_extremes(self):
        from src.audio.stt.speculative import compute_spectral_entropy
        import numpy as np

        silence = [0.0] * 1600
        assert compute_spectral_entropy(silence) == 0.0

        pure_tone = [float(np.sin(2 * np.pi * 440 * i / 16000)) for i in range(1600)]
        assert compute_spectral_entropy(pure_tone) < 0.05

        noise = np.random.uniform(-0.5, 0.5, 1600).tolist()
        assert compute_spectral_entropy(noise) > 0.70

    def test_speculative_engine_end_to_end(self):
        import moonshine_voice
        from moonshine_voice.utils import load_wav_file
        from src.audio.stt.speculative import SpeculativeStreamingEngine
        from src.audio.stt.artifacts import resolve_model_path, MoonshineVariant

        if not resolve_model_path(MoonshineVariant.TINY_STREAMING).is_dir():
            pytest.skip("Tiny Streaming weights missing")
        if not resolve_model_path(MoonshineVariant.SMALL_STREAMING).is_dir():
            pytest.skip("Small Streaming weights missing")

        engine = SpeculativeStreamingEngine(entropy_threshold=0.35)
        engine.load()
        assert engine.is_loaded

        assets_dir = Path(moonshine_voice.__file__).parent / "assets"
        audio, sr = load_wav_file(str(assets_dir / "beckett.wav"))

        partials = []
        session = engine.create_session(
            "test-spec-session",
            on_partial=lambda text, rev: partials.append(text),
        )

        chunk_size = 1600
        for i in range(0, len(audio), chunk_size):
            chunk = audio[i : i + chunk_size]
            session.add_audio(chunk)

        result = session.finalize()
        metrics = session.get_metrics()
        engine.release_session("test-spec-session")
        engine.unload()

        assert "tried" in result.text.lower()
        assert "failed" in result.text.lower()
        assert "better" in result.text.lower()

        assert metrics.total_chunks > 0
        assert metrics.draft_accepted_chunks > 0
        assert metrics.compute_savings_pct > 0.0

    def test_speculative_clean_speech_skips_verifier_100_percent(self):
        import numpy as np
        from src.audio.stt.speculative import SpeculativeStreamingEngine
        from src.audio.stt.artifacts import resolve_model_path, MoonshineVariant

        if not resolve_model_path(MoonshineVariant.TINY_STREAMING).is_dir():
            pytest.skip("Tiny Streaming weights missing")
        if not resolve_model_path(MoonshineVariant.SMALL_STREAMING).is_dir():
            pytest.skip("Small Streaming weights missing")

        engine = SpeculativeStreamingEngine(entropy_threshold=0.35)
        engine.load()

        session = engine.create_session("test-clean-spec")
        t = np.linspace(0, 1.0, 16000, endpoint=False)
        clean_tone = (0.5 * np.sin(2 * np.pi * 440 * t) + 0.25 * np.sin(2 * np.pi * 880 * t)).astype(np.float32).tolist()

        for i in range(0, len(clean_tone), 1600):
            session.add_audio(clean_tone[i : i + 1600])

        session.finalize()
        metrics = session.get_metrics()
        engine.release_session("test-clean-spec")
        engine.unload()

        assert metrics.verification_triggered is False
        assert metrics.verified_chunks == 0
        assert metrics.compute_savings_pct == 100.0
        assert metrics.draft_accepted_chunks == metrics.total_chunks

    def test_speculative_verification_policies(self):
        import numpy as np
        from src.audio.stt.speculative import SpeculativeStreamingEngine
        from src.audio.stt.artifacts import resolve_model_path, MoonshineVariant

        if not resolve_model_path(MoonshineVariant.TINY_STREAMING).is_dir():
            pytest.skip("Tiny Streaming weights missing")
        if not resolve_model_path(MoonshineVariant.SMALL_STREAMING).is_dir():
            pytest.skip("Small Streaming weights missing")

        engine = SpeculativeStreamingEngine()
        engine.load()

        s_always = engine.create_session("test-always", verification_policy="always")
        tone = [0.1 * np.sin(2 * np.pi * 440 * i / 16000) for i in range(3200)]
        s_always.add_audio(tone)
        s_always.finalize()
        assert s_always.get_metrics().verification_triggered is True
        engine.release_session("test-always")

        s_never = engine.create_session("test-never", verification_policy="never")
        noise = np.random.uniform(-0.5, 0.5, 3200).tolist()
        s_never.add_audio(noise)
        s_never.finalize()
        assert s_never.get_metrics().verification_triggered is False
        assert s_never.get_metrics().compute_savings_pct == 100.0
        engine.release_session("test-never")

        engine.unload()

    def test_speculative_session_cancellation(self):
        from src.audio.stt.session import SessionState
        from src.audio.stt.exceptions import STTCancelledError
        from src.audio.stt.speculative import SpeculativeStreamingEngine
        from src.audio.stt.artifacts import resolve_model_path, MoonshineVariant

        if not resolve_model_path(MoonshineVariant.TINY_STREAMING).is_dir():
            pytest.skip("Tiny Streaming weights missing")
        if not resolve_model_path(MoonshineVariant.SMALL_STREAMING).is_dir():
            pytest.skip("Small Streaming weights missing")

        engine = SpeculativeStreamingEngine()
        engine.load()

        session = engine.create_session("test-cancel-spec")
        session.add_audio([0.0] * 1600)
        session.cancel("user barge-in")

        assert session.state == SessionState.CANCELLED
        with pytest.raises(STTCancelledError):
            session.finalize()

        engine.release_session("test-cancel-spec")
        engine.unload()
