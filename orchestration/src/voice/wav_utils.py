from __future__ import annotations

import io
import math
import struct
import wave
from pathlib import Path
from typing import BinaryIO, Union

VOICE_SAMPLE_RATE_HZ = 16_000


def read_wav_as_16k_mono_f32(source: Union[str, Path, BinaryIO, bytes, bytearray]) -> list[float]:
    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)

    with wave.open(source, "rb") as wav:
        n_channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        frame_rate = wav.getframerate()
        n_frames = wav.getnframes()
        raw = wav.readframes(n_frames)

    if sample_width == 2:
        samples = struct.unpack(f"<{len(raw) // 2}h", raw)
        floats = [s / 32768.0 for s in samples]
    elif sample_width == 4:
        samples = struct.unpack(f"<{len(raw) // 4}i", raw)
        floats = [s / 2147483648.0 for s in samples]
    elif sample_width == 1:
        samples = struct.unpack(f"<{len(raw)}B", raw)
        floats = [(s - 128) / 128.0 for s in samples]
    else:
        raise ValueError(f"Unsupported WAV sample width: {sample_width * 8}-bit")

    if n_channels > 1:
        floats = [
            sum(floats[i : i + n_channels]) / n_channels
            for i in range(0, len(floats), n_channels)
        ]

    if frame_rate != VOICE_SAMPLE_RATE_HZ and floats:
        from scipy.signal import resample_poly

        divisor = math.gcd(frame_rate, VOICE_SAMPLE_RATE_HZ)
        floats = resample_poly(
            floats, VOICE_SAMPLE_RATE_HZ // divisor, frame_rate // divisor
        ).tolist()

    return floats


def write_pcm16_wav_bytes(samples: list[float], sample_rate: int) -> bytes:
    clamped = [max(-1.0, min(1.0, s)) for s in samples]
    ints = [int(s * 32767) for s in clamped]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(struct.pack(f"<{len(ints)}h", *ints))
    return buf.getvalue()
