from __future__ import annotations

import sys

from src.audio.stt.artifacts import MoonshineVariant
from src.audio.stt.artifacts import verify_artifacts as verify_stt
from src.audio.stt.artifacts import verify_crc32c
from src.audio.tts.artifacts import verify_artifacts as verify_tts


def main() -> int:
    errors = [f"tts: {e}" for e in verify_tts()]
    for variant in MoonshineVariant:
        errors += [f"stt {variant.value}: {e}" for e in verify_stt(variant) + verify_crc32c(variant)]

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("all model artifacts verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
