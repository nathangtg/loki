from __future__ import annotations

import argparse
import os
import resource
import struct
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

_env_file = Path(__file__).resolve().parent.parent.parent / ".env"
if not _env_file.exists():
    _env_file = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_file, override=False)
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from src.server.config import LOGGER, get_voice_manager
from src.voice.session import VoiceSessionState
from src.voice.wav_utils import (
    VOICE_SAMPLE_RATE_HZ,
    read_wav_as_16k_mono_f32,
    write_pcm16_wav_bytes,
)

CHUNK_SAMPLES = int(VOICE_SAMPLE_RATE_HZ * 0.1)


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return peak / divisor


def run_demo(audio_path: Path | None, text: str | None, out_path: Path) -> None:
    LOGGER.info("voice_demo: warming up resident models (STT + TTS + LLM)...")
    from src.server.config import TTS_BACKEND
    from src.server.warmup import _warmup_llm, _warmup_stt, _warmup_system_tts, _warmup_tts

    _warmup_stt()
    _warmup_system_tts()
    if os.getenv("LOKI_TTS_BACKEND", TTS_BACKEND).strip().lower() == "kokoro":
        _warmup_tts()
    _warmup_llm()
    baseline_rss_mb = _peak_rss_mb()

    manager = get_voice_manager()
    session = manager.create_session(text_only=text is not None)
    LOGGER.info("voice_demo: session %s created", session.session_id)

    t0 = time.perf_counter()
    marks: dict[str, float] = {}

    if text is not None:
        session.finalize_with_transcript(text)
    else:
        assert audio_path is not None
        samples = read_wav_as_16k_mono_f32(audio_path)
        LOGGER.info(
            "voice_demo: streaming %.2fs of audio in %dms chunks",
            len(samples) / VOICE_SAMPLE_RATE_HZ, 100,
        )
        seq = 0
        for i in range(0, len(samples), CHUNK_SAMPLES):
            chunk = samples[i : i + CHUNK_SAMPLES]
            is_final = i + CHUNK_SAMPLES >= len(samples)
            pcm_bytes = struct.pack(f"<{len(chunk)}f", *chunk)
            session.push_audio(seq, pcm_bytes, is_final)
            seq += 1

    events_after = 0
    pcm_after = 0
    all_samples: list[float] = []
    sample_rate = 24_000
    audio_done = False

    while True:
        events, events_after, dropped = session.poll_events(events_after)
        for event in events:
            elapsed = time.perf_counter() - t0
            if event.type == "transcript.partial" and "first_partial_transcript" not in marks:
                marks["first_partial_transcript"] = elapsed
            if event.type == "transcript.final":
                marks["final_transcript"] = elapsed
                LOGGER.info("voice_demo: transcript: %r", event.data["text"])
            if event.type == "assistant.text":
                marks["assistant_text_ready"] = elapsed
                LOGGER.info("voice_demo: assistant: %r", event.data["text"])
            if event.type == "speaking.started" and "first_audio_frame" not in marks:
                marks["first_audio_frame"] = elapsed
            if event.type == "error":
                LOGGER.error("voice_demo: engine error: %s", event.data)

        if not audio_done:
            frames, start_seq, _latest, closed, cancelled = session.pull_audio(pcm_after, limit=20)
            pcm_after = start_seq + len(frames)
            for frame in frames:
                if "first_audio_frame" not in marks:
                    marks["first_audio_frame"] = time.perf_counter() - t0
                sample_rate = frame.sample_rate
                raw = struct.unpack(f"<{len(frame.pcm_f32le) // 4}f", frame.pcm_f32le)
                all_samples.extend(raw[: frame.valid_samples])
            audio_done = closed or cancelled

        if audio_done and session.state in (
            VoiceSessionState.DONE,
            VoiceSessionState.CANCELLED,
            VoiceSessionState.ERROR,
        ):
            break
        time.sleep(0.01)

    total_elapsed = time.perf_counter() - t0
    peak_rss_mb = _peak_rss_mb()

    if all_samples:
        out_path.write_bytes(write_pcm16_wav_bytes(all_samples, sample_rate))
        LOGGER.info("voice_demo: wrote %.2fs of speech to %s", len(all_samples) / sample_rate, out_path)

    manager.release_session(session.session_id)

    print("\n=== Loki Voice Engine Demo — latency & memory report ===")
    print(f"final session state        : {session.state.value}")
    if "first_partial_transcript" in marks:
        print(f"time to first partial STT  : {marks['first_partial_transcript'] * 1000:.0f} ms")
    if "final_transcript" in marks:
        print(f"time to final transcript   : {marks['final_transcript'] * 1000:.0f} ms")
    if "assistant_text_ready" in marks:
        print(f"time to assistant response : {marks['assistant_text_ready'] * 1000:.0f} ms")
    if "first_audio_frame" in marks:
        print(f"time to first audio frame  : {marks['first_audio_frame'] * 1000:.0f} ms  (TTFA)")
    print(f"total wall-clock turn time : {total_elapsed * 1000:.0f} ms")
    print(f"RSS after warmup (models)  : {baseline_rss_mb:.1f} MB")
    print(f"RSS after one full turn    : {peak_rss_mb:.1f} MB")
    print(f"marginal turn overhead     : {peak_rss_mb - baseline_rss_mb:.1f} MB")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--audio", type=Path, help="16-bit PCM WAV file to use as the spoken command")
    source.add_argument("--text", type=str, help="Skip STT; use this text as the transcribed command directly")
    parser.add_argument("--out", type=Path, default=Path("voice_demo_output.wav"), help="Where to write the synthesized reply")
    args = parser.parse_args()

    if args.audio is not None and not args.audio.exists():
        parser.error(f"audio file not found: {args.audio}")

    run_demo(args.audio, args.text, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
