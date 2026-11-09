from __future__ import annotations

import hashlib
from importlib.metadata import version
from pathlib import Path
from unittest import mock

import pytest

from src.audio.tts.artifacts import (
    KokoroArtifactContract,
    TTSArtifactFile,
    load_artifact_contract,
    load_kokoro_runtime,
    resolve_model_path,
    verify_artifacts,
)


def _contract(content: bytes) -> KokoroArtifactContract:
    return KokoroArtifactContract(
        model_id="mlx-community/Kokoro-82M-4bit",
        revision="e4468a460f6f70b9125a003e0adb1ab7d4904bbd",
        variant="4-bit",
        quantization_bits=4,
        quantization_group_size=64,
        license="Apache-2.0",
        source="https://huggingface.co/mlx-community/Kokoro-82M-4bit",
        default_voice="af_heart",
        language_code="a",
        voice_path="voices/af_heart.safetensors",
        total_bytes=len(content),
        files=(
            TTSArtifactFile(
                path="voices/af_heart.safetensors",
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                role="voice",
            ),
        ),
    )


def test_dependencies_are_pinned_and_english_model_is_local():
    import spacy
    from misaki.en import G2P

    assert version("mlx-audio") == "0.5.4"
    assert version("misaki") == "0.9.4"
    assert version("en-core-web-sm") == "3.8.0"
    assert spacy.util.is_package("en_core_web_sm")
    with mock.patch("spacy.cli.download", side_effect=AssertionError("network download")):
        G2P(trf=False, british=False, fallback=None)


def test_manifest_records_pinned_model_contract():
    contract = load_artifact_contract()

    assert contract.model_id == "mlx-community/Kokoro-82M-4bit"
    assert contract.revision == "e4468a460f6f70b9125a003e0adb1ab7d4904bbd"
    assert contract.quantization_bits == 4
    assert contract.license == "Apache-2.0"
    assert contract.default_voice == "af_heart"
    assert contract.total_bytes == sum(item.size_bytes for item in contract.files)


def test_environment_path_has_precedence(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOKI_TTS_MODEL_PATH", "~/kokoro-test")

    assert resolve_model_path(Path("/ignored")) == Path.home() / "kokoro-test"


def test_verifier_accepts_exact_artifact(tmp_path: Path):
    content = b"voice"
    path = tmp_path / "voices" / "af_heart.safetensors"
    path.parent.mkdir()
    path.write_bytes(content)

    assert verify_artifacts(tmp_path, _contract(content)) == []


def test_verifier_rejects_modified_artifact(tmp_path: Path):
    expected = b"voice"
    path = tmp_path / "voices" / "af_heart.safetensors"
    path.parent.mkdir()
    path.write_bytes(b"other")

    errors = verify_artifacts(tmp_path, _contract(expected))

    assert len(errors) == 1
    assert "SHA-256 mismatch" in errors[0]


def test_runtime_loads_only_verified_local_path(tmp_path: Path):
    runtime_model = object()
    with (
        mock.patch("src.audio.tts.artifacts.platform.system", return_value="Darwin"),
        mock.patch("src.audio.tts.artifacts.platform.machine", return_value="arm64"),
        mock.patch("src.audio.tts.artifacts.verify_artifacts", return_value=[]),
        mock.patch("mlx_audio.tts.utils.load_model", return_value=runtime_model) as loader,
    ):
        runtime = load_kokoro_runtime(tmp_path)

    loader.assert_called_once_with(tmp_path)
    assert runtime.model is runtime_model
    assert runtime.voice_path == tmp_path / "voices" / "af_heart.safetensors"
    assert runtime.language_code == "a"


def test_runtime_rejects_unsupported_platform():
    with (
        mock.patch("src.audio.tts.artifacts.platform.system", return_value="Darwin"),
        mock.patch("src.audio.tts.artifacts.platform.machine", return_value="x86_64"),
    ):
        with pytest.raises(RuntimeError, match="arm64 macOS"):
            load_kokoro_runtime()
