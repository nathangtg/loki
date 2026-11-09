from __future__ import annotations

import hashlib
import json
import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = PROJECT_ROOT / "model-manifests" / "kokoro-82m-4bit.json"
DEFAULT_MODEL_DIR = PROJECT_ROOT / "weights" / "tts" / "kokoro-82m-4bit"
MODEL_PATH_ENV = "LOKI_TTS_MODEL_PATH"
_REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class TTSArtifactFile:
    path: str
    size_bytes: int
    sha256: str
    role: str


@dataclass(frozen=True)
class KokoroArtifactContract:
    model_id: str
    revision: str
    variant: str
    quantization_bits: int
    quantization_group_size: int
    license: str
    source: str
    default_voice: str
    language_code: str
    voice_path: str
    total_bytes: int
    files: tuple[TTSArtifactFile, ...]


@dataclass(frozen=True)
class KokoroRuntime:
    model: Any
    voice_path: Path
    language_code: str


def load_artifact_contract(manifest_path: Path = MANIFEST_PATH) -> KokoroArtifactContract:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported Kokoro artifact manifest schema")

    model = data["model"]
    voice = data["default_voice"]
    files = tuple(
        TTSArtifactFile(
            path=item["path"],
            size_bytes=item["size_bytes"],
            sha256=item["sha256"],
            role=item["role"],
        )
        for item in data["files"]
    )
    contract = KokoroArtifactContract(
        model_id=model["id"],
        revision=model["revision"],
        variant=model["variant"],
        quantization_bits=model["quantization"]["bits"],
        quantization_group_size=model["quantization"]["group_size"],
        license=model["license"],
        source=model["source"],
        default_voice=voice["id"],
        language_code=voice["language_code"],
        voice_path=voice["path"],
        total_bytes=data["total_bytes"],
        files=files,
    )
    _validate_contract(contract)
    return contract


def _validate_contract(contract: KokoroArtifactContract) -> None:
    if not _REVISION_PATTERN.fullmatch(contract.revision):
        raise ValueError("Model revision must be a full hexadecimal commit hash")
    if contract.total_bytes != sum(item.size_bytes for item in contract.files):
        raise ValueError("Artifact total does not match file sizes")
    paths = {item.path for item in contract.files}
    if contract.voice_path not in paths:
        raise ValueError("Default voice is absent from the artifact file list")
    for item in contract.files:
        path = Path(item.path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Unsafe artifact path: {item.path}")
        if item.size_bytes < 0 or not _SHA256_PATTERN.fullmatch(item.sha256):
            raise ValueError(f"Invalid artifact metadata: {item.path}")


def resolve_model_path(override: Path | None = None) -> Path:
    env_path = os.environ.get(MODEL_PATH_ENV)
    if env_path:
        return Path(env_path).expanduser()
    if override is not None:
        return override.expanduser()
    return DEFAULT_MODEL_DIR


def verify_artifacts(
    model_dir: Path | None = None,
    contract: KokoroArtifactContract | None = None,
) -> list[str]:
    contract = contract or load_artifact_contract()
    model_dir = resolve_model_path(model_dir)
    if not model_dir.is_dir():
        return [f"Model directory does not exist: {model_dir}"]

    errors: list[str] = []
    for artifact in contract.files:
        path = model_dir / artifact.path
        if not path.is_file():
            errors.append(f"Missing file: {artifact.path}")
            continue
        actual_size = path.stat().st_size
        if actual_size != artifact.size_bytes:
            errors.append(
                f"Size mismatch for {artifact.path}: expected {artifact.size_bytes}, got {actual_size}"
            )
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
        actual_hash = digest.hexdigest()
        if actual_hash != artifact.sha256:
            errors.append(
                f"SHA-256 mismatch for {artifact.path}: expected {artifact.sha256}, got {actual_hash}"
            )
    return errors


def load_kokoro_runtime(model_dir: Path | None = None) -> KokoroRuntime:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("Kokoro MLX requires arm64 macOS")

    contract = load_artifact_contract()
    model_dir = resolve_model_path(model_dir)
    errors = verify_artifacts(model_dir, contract)
    if errors:
        raise RuntimeError("Invalid Kokoro artifact set: " + "; ".join(errors))

    from mlx_audio.tts.utils import load_model

    model = load_model(model_dir)
    return KokoroRuntime(
        model=model,
        voice_path=model_dir / contract.voice_path,
        language_code=contract.language_code,
    )
