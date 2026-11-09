from pathlib import Path

from src.audio.stt import STTError
from src.audio.tts import TTSError


def test_audio_implementations_are_grouped_by_subsystem():
    audio_root = Path(__file__).parents[1] / "src" / "audio"
    superseded = {
        "errors.py",
        "normalization.py",
        "session.py",
        "speculative.py",
        "stt.py",
        "stt_artifacts.py",
        "tts.py",
        "tts_artifacts.py",
    }

    assert not superseded.intersection(path.name for path in audio_root.glob("*.py"))
    assert (audio_root / "stt" / "exceptions.py").is_file()
    assert (audio_root / "tts" / "exceptions.py").is_file()


def test_exception_types_are_owned_by_their_subsystems():
    assert STTError.__module__ == "src.audio.stt.exceptions"
    assert TTSError.__module__ == "src.audio.tts.exceptions"
