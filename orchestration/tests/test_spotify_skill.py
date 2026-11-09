from __future__ import annotations

from types import SimpleNamespace

from src.skills import applescript_utils, spotify_skill


def test_run_applescript_passes_optional_argv(monkeypatch):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(applescript_utils.subprocess, "run", fake_run)
    out = applescript_utils.run_applescript("on run argv\nend run", "Starboy", timeout=10)
    assert out == "ok"
    assert captured["cmd"] == ["osascript", "-e", "on run argv\nend run", "Starboy"]
    assert captured["timeout"] == 10


def test_spotify_intents_use_applescript(monkeypatch):
    scripts = []

    def fake_applescript(script: str, *args: str, timeout: float = 15.0) -> str:
        scripts.append((script, args, timeout))
        if "name of current track" in script:
            return "Starboy|||The Weeknd"
        return ""

    monkeypatch.setattr(spotify_skill, "run_applescript", fake_applescript)
    monkeypatch.setattr(spotify_skill, "_activate_spotify", lambda: None)

    skill = spotify_skill._build_spotify_skill()

    assert skill.execute("play", {}) == "Playback resumed"
    assert skill.execute("pause", {}) == "Playback paused"
    assert skill.execute("playpause", {}) == "Toggled play/pause"
    assert skill.execute("next_track", {}) == "Skipped to next track"
    assert skill.execute("previous_track", {}) == "Skipped to previous track"
    assert skill.execute("now_playing", {}) == "Currently playing: Starboy by The Weeknd"
    assert skill.execute("set_volume", {"volume": 65}) == "Volume set to 65%"
    assert (
        skill.execute("play_track", {"direct_parameter": "spotify:track:7MXVkk9YMctZqd1Srtv4MB"})
        == "Now playing spotify:track:7MXVkk9YMctZqd1Srtv4MB on Spotify."
    )
    assert (
        skill.execute("search", {"query": "Starboy"})
        == "Searched for Starboy in Spotify. Choose a result to play it."
    )

    assert spotify_skill.extract_spotify_intent_from_goal("Play Starboy on Spotify") == ("search", {"query": "Starboy"})

    executed_scripts = [s for s, _, _ in scripts]
    assert 'tell application "Spotify" to play' in executed_scripts
    assert 'tell application "Spotify" to pause' in executed_scripts
    assert 'tell application "Spotify" to playpause' in executed_scripts
    assert 'tell application "Spotify" to next track' in executed_scripts
    assert 'tell application "Spotify" to previous track' in executed_scripts
    assert 'tell application "Spotify" to set sound volume to 65' in executed_scripts
    assert 'tell application "Spotify" to play track "spotify:track:7MXVkk9YMctZqd1Srtv4MB"' in executed_scripts
