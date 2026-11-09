from __future__ import annotations

import math
import re
import struct
from typing import Sequence, Union

from src.audio.stt.exceptions import InvalidAudioDataError

BYTES_PER_SAMPLE_F32 = 4

_WAKE_PREFIX_RE = re.compile(r"^\s*hey\s+loki\b[,\.?!;\s]*", re.IGNORECASE)


def normalize_wake_prefix(text: str) -> tuple[str, bool]:
    match = _WAKE_PREFIX_RE.match(text)
    if match:
        cleaned = text[match.end():].lstrip()
        return cleaned, True
    return text.strip(), False


def parse_and_validate_f32le_pcm(
    data: Union[bytes, bytearray, Sequence[float]]
) -> list[float]:
    if isinstance(data, (bytes, bytearray)):
        if len(data) % BYTES_PER_SAMPLE_F32 != 0:
            raise InvalidAudioDataError(
                f"Audio payload size ({len(data)} bytes) is not aligned to 4-byte f32 samples"
            )
        num_samples = len(data) // BYTES_PER_SAMPLE_F32
        floats = struct.unpack(f"<{num_samples}f", data)
    elif isinstance(data, (list, tuple)):
        floats = data
    else:
        raise InvalidAudioDataError(f"Unsupported audio type: {type(data)}")

    clamped_samples: list[float] = []
    for s in floats:
        f = float(s)
        if math.isnan(f) or math.isinf(f):
            raise InvalidAudioDataError(
                "Audio buffer contains NaN or Infinite sample values"
            )
        if f > 1.0:
            clamped_samples.append(1.0)
        elif f < -1.0:
            clamped_samples.append(-1.0)
        else:
            clamped_samples.append(f)

    return clamped_samples
