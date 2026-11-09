from __future__ import annotations

import argparse

from moonshine_voice.download import get_model_for_language
from moonshine_voice.moonshine_api import ModelArch

from src.audio.stt.artifacts import (
    MoonshineVariant,
    resolve_model_path,
    verify_artifacts,
    verify_crc32c,
)

ARCHS = {
    MoonshineVariant.TINY_STREAMING: ModelArch.TINY_STREAMING,
    MoonshineVariant.SMALL_STREAMING: ModelArch.SMALL_STREAMING,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Download and verify Moonshine STT models")
    parser.parse_args()

    failures: list[str] = []
    for variant, arch in ARCHS.items():
        path, _ = get_model_for_language("en", arch)
        print(f"{variant.value} ready at: {path}")
        model_dir = resolve_model_path(variant)
        errors = verify_artifacts(variant, model_dir) + verify_crc32c(variant, model_dir)
        failures.extend(f"{variant.value}: {e}" for e in errors)

    if failures:
        raise SystemExit("verification failed:\n  " + "\n  ".join(failures))
    print("Moonshine artifacts verified (size + CRC32C)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
