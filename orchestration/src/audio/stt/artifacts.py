from __future__ import annotations

import enum
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

LOGGER = logging.getLogger("loki.stt_artifacts")

class MoonshineVariant(enum.Enum):
    SMALL_STREAMING = "small_streaming"
    TINY_STREAMING = "tiny_streaming"


@dataclass(frozen=True)
class ArtifactFile:
    name: str
    size_bytes: int
    crc32c_b64: str


@dataclass(frozen=True)
class ModelArtifactContract:
    variant: MoonshineVariant
    moonshine_model_arch: int
    base_url: str
    relative_cache_path: str
    files: List[ArtifactFile]
    total_bytes: int
    license: str = "MIT"
    language: str = "en"

    moonshine_voice_version: str = "0.1.5"

_SMALL_STREAMING_FILES: List[ArtifactFile] = [
    ArtifactFile(name="adapter.ort",            size_bytes=2_870_368,   crc32c_b64="XlWjTg=="),
    ArtifactFile(name="cross_kv.ort",           size_bytes=5_356_536,   crc32c_b64="5ySK8g=="),
    ArtifactFile(name="decoder_kv.ort",         size_bytes=81_878_600,  crc32c_b64="Rn/VUA=="),
    ArtifactFile(name="encoder.ort",            size_bytes=44_148_576,  crc32c_b64="41D2jQ=="),
    ArtifactFile(name="frontend.model.ort",     size_bytes=26_944,      crc32c_b64="WW2/2g=="),
    ArtifactFile(name="frontend.weights.ort",   size_bytes=7_769_464,   crc32c_b64="9pROuQ=="),
    ArtifactFile(name="streaming_config.json",  size_bytes=512,         crc32c_b64="dPbFiw=="),
    ArtifactFile(name="tokenizer.bin",          size_bytes=249_974,     crc32c_b64="B7s10Q=="),
]

SMALL_STREAMING = ModelArtifactContract(
    variant=MoonshineVariant.SMALL_STREAMING,
    moonshine_model_arch=4,
    base_url="https://download.moonshine.ai/model/small-streaming-en/quantized_26_08_21",
    relative_cache_path="download.moonshine.ai/model/small-streaming-en/quantized_26_08_21",
    files=_SMALL_STREAMING_FILES,
    total_bytes=142_300_974,
)

_TINY_STREAMING_FILES: List[ArtifactFile] = [
    ArtifactFile(name="adapter.ort",            size_bytes=1_319_664,   crc32c_b64="kwQ+Bw=="),
    ArtifactFile(name="cross_kv.ort",           size_bytes=1_287_544,   crc32c_b64="76wzFQ=="),
    ArtifactFile(name="decoder_kv.ort",         size_bytes=32_583_720,  crc32c_b64="KJjeNw=="),
    ArtifactFile(name="encoder.ort",            size_bytes=7_675_440,   crc32c_b64="UjAIpQ=="),
    ArtifactFile(name="frontend.model.ort",     size_bytes=23_344,      crc32c_b64="aM/+wQ=="),
    ArtifactFile(name="frontend.weights.ort",   size_bytes=2_093_464,   crc32c_b64="WYZ7EQ=="),
    ArtifactFile(name="streaming_config.json",  size_bytes=509,         crc32c_b64="HGL0Ug=="),
    ArtifactFile(name="tokenizer.bin",          size_bytes=249_974,     crc32c_b64="B7s10Q=="),
]

TINY_STREAMING = ModelArtifactContract(
    variant=MoonshineVariant.TINY_STREAMING,
    moonshine_model_arch=2,
    base_url="https://download.moonshine.ai/model/tiny-streaming-en/quantized_26_08_21",
    relative_cache_path="download.moonshine.ai/model/tiny-streaming-en/quantized_26_08_21",
    files=_TINY_STREAMING_FILES,
    total_bytes=45_233_659,
)

CONTRACTS: Dict[MoonshineVariant, ModelArtifactContract] = {
    MoonshineVariant.SMALL_STREAMING: SMALL_STREAMING,
    MoonshineVariant.TINY_STREAMING: TINY_STREAMING,
}

def _default_cache_root() -> Path:
    try:
        from moonshine_voice.download_file import get_cache_dir
        return Path(get_cache_dir())
    except ImportError:
        import platform as _plat
        if _plat.system() == "Darwin":
            return Path.home() / "Library" / "Caches" / "moonshine_voice"
        return Path.home() / ".cache" / "moonshine_voice"


def resolve_model_path(
    variant: MoonshineVariant,
    *,
    override_root: Optional[Path] = None,
) -> Path:
    contract = CONTRACTS[variant]

    env_path = os.environ.get("LOKI_STT_MODEL_PATH")
    if env_path:
        return Path(env_path)

    if override_root is not None:
        return override_root / contract.relative_cache_path

    return _default_cache_root() / contract.relative_cache_path


def verify_artifacts(
    variant: MoonshineVariant,
    model_dir: Optional[Path] = None,
    *,
    check_sizes: bool = True,
) -> List[str]:
    contract = CONTRACTS[variant]
    if model_dir is None:
        model_dir = resolve_model_path(variant)

    errors: List[str] = []

    if not model_dir.is_dir():
        errors.append(f"Model directory does not exist: {model_dir}")
        return errors

    for af in contract.files:
        fp = model_dir / af.name
        if not fp.exists():
            errors.append(f"Missing file: {af.name}")
            continue
        if check_sizes:
            actual = fp.stat().st_size
            if actual != af.size_bytes:
                errors.append(
                    f"Size mismatch for {af.name}: "
                    f"expected {af.size_bytes}, got {actual}"
                )

    return errors


def verify_crc32c(
    variant: MoonshineVariant,
    model_dir: Optional[Path] = None,
) -> List[str]:
    try:
        import google_crc32c
    except ImportError:
        return ["google-crc32c is not installed; cannot verify CRC32C checksums"]

    import base64
    import struct

    contract = CONTRACTS[variant]
    if model_dir is None:
        model_dir = resolve_model_path(variant)

    errors: List[str] = []

    for af in contract.files:
        fp = model_dir / af.name
        if not fp.exists():
            errors.append(f"Missing file: {af.name}")
            continue

        crc = 0
        with open(fp, "rb") as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                crc = google_crc32c.extend(crc, chunk)

        actual_b64 = base64.b64encode(struct.pack(">I", crc)).decode("ascii")
        if actual_b64 != af.crc32c_b64:
            errors.append(
                f"CRC32C mismatch for {af.name}: "
                f"expected {af.crc32c_b64}, got {actual_b64}"
            )

    return errors


def get_moonshine_model_arch(variant: MoonshineVariant) -> int:
    return CONTRACTS[variant].moonshine_model_arch
