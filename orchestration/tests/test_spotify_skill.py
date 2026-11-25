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
    assert spotify_skill.extract_spotify_intent_from_goal(
        "Can you play me Neutron Star Collision by Muse on Spotify?"
    ) == ("search", {"query": "Neutron Star Collision by Muse"})

    assert spotify_skill.extract_spotify_intent_from_goal("Play Starboy on Spotify") == ("search", {"query": "Starboy"})

    executed_scripts = [s for s, _, _ in scripts]
    assert 'tell application "Spotify" to play' in executed_scripts
    assert 'tell application "Spotify" to pause' in executed_scripts
    assert 'tell application "Spotify" to playpause' in executed_scripts
    assert 'tell application "Spotify" to next track' in executed_scripts
    assert 'tell application "Spotify" to previous track' in executed_scripts
    assert 'tell application "Spotify" to set sound volume to 65' in executed_scripts
    assert 'tell application "Spotify" to play track "spotify:track:7MXVkk9YMctZqd1Srtv4MB"' in executed_scripts


def _patch_search(monkeypatch, state="playing|||Starlight|||Muse"):
    calls = []

    def fake_applescript(script: str, *args: str, timeout: float = 15.0) -> str:
        calls.append(script)
        if "player state" in script:
            if isinstance(state, Exception):
                raise state
            return state
        return ""

    monkeypatch.setattr(spotify_skill, "run_applescript", fake_applescript)
    monkeypatch.setattr(spotify_skill, "_activate_spotify", lambda: None)
    monkeypatch.setattr(spotify_skill.time, "sleep", lambda s: None)
    return calls


def test_search_sends_keyboard_flow_and_reports_playing(monkeypatch):
    calls = _patch_search(monkeypatch)
    result = spotify_skill._search_and_play_in_spotify_ui("starlight by muse")
    assert result == "Playing Starlight by Muse on Spotify."
    joined = "\n".join(calls)
    cmd_k = joined.index('keystroke "k" using {command down}')
    query = joined.index('keystroke "starlight by muse"')
    enter = joined.index("key code 36 using {shift down}")
    assert cmd_k < query < enter


def test_search_not_playing_is_reported_honestly(monkeypatch):
    _patch_search(monkeypatch, state="paused|||Starlight|||Muse")
    result = spotify_skill._search_and_play_in_spotify_ui("starlight by muse")
    assert "Playing" not in result
    assert "could not start playback" in result


def test_search_verification_error_is_reported_honestly(monkeypatch):
    _patch_search(monkeypatch, state=RuntimeError("boom"))
    assert "could not start playback" in spotify_skill._search_and_play_in_spotify_ui("x")


def test_extract_spotify_query_from_noisy_transcripts():
    f = spotify_skill.extract_spotify_intent_from_goal
    assert f("Can you play me a neutron star collision by a Vsons Spotify?") == (
        "search",
        {"query": "a neutron star collision by a Vsons"},
    )
    assert f("Play Neutron Star Collision by Muse on Spotify") == (
        "search",
        {"query": "Neutron Star Collision by Muse"},
    )
    assert f("play me starlight by Muse on Spotify please") == ("search", {"query": "starlight by Muse"})
    # Fused STT form: artist unrecoverable, so no match (falls back to generic handling).
    assert f("play starlight by musonsportify") is None
    assert f("play Spotify") is None
    assert f("play on Spotify") is None
    assert f("open Spotify") is None
