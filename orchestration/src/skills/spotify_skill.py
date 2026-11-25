from __future__ import annotations

import logging
import re
import subprocess
import time

from src.skills.applescript_utils import escape_as_string, run_applescript
from src.skills.base import Skill, SkillIntent
from src.skills.registry import register_skill

LOGGER = logging.getLogger(__name__)

_PLAY_PREFIX_RE = re.compile(
    r"^(?:(?:hey\s+)?loki[,\s]*|please\s+|can\s+you\s+(?:please\s+)?|could\s+you\s+(?:please\s+)?)*"
    r"play\s+(?:me\s+)?(?P<rest>.+)$",
    re.IGNORECASE,
)
_TRAILING_POLITE_RE = re.compile(r"(?:\s+for\s+me)?(?:\s+please)?$", re.IGNORECASE)
_TRAILING_SPOTIFY_RE = re.compile(r"(?:\s+(?:on|in|with|using))?\s+spotify$", re.IGNORECASE)
_GENERIC_QUERIES = {"music", "some music", "a song", "something", "spotify", "on spotify", "in spotify"}


def extract_spotify_intent_from_goal(goal: str) -> tuple[str, dict] | None:
    clean = " ".join((goal or "").split()).strip().rstrip(".!?")
    lower = clean.lower()
    if "spotify" not in lower:
        return None
    if re.match(r"^(?:open|launch|start)\s+spotify$", lower):
        return None
    if any(p in lower for p in ("pause spotify", "pause playback", "pause the music", "stop the music")):
        return "pause", {}
    if any(p in lower for p in ("resume spotify", "resume playback", "unpause spotify")):
        return "play", {}
    if any(p in lower for p in ("next track", "next song", "skip track", "skip song")):
        return "next_track", {}
    if any(p in lower for p in ("previous track", "previous song", "last song")):
        return "previous_track", {}
    if any(p in lower for p in ("what's playing", "what is playing", "currently playing", "now playing")):
        return "now_playing", {}
    m_play = _PLAY_PREFIX_RE.match(clean)
    if m_play:
        rest = _TRAILING_POLITE_RE.sub("", m_play.group("rest").strip()).strip()
        # Fused STT forms like "musonsportify" never match "\s+spotify$", so the
        # "spotify" guard above or this strip leaves them unmatched (artist is unrecoverable).
        q = _TRAILING_SPOTIFY_RE.sub("", rest).strip(" ,")
        if q and q.lower() not in _GENERIC_QUERIES and "spotify" not in q.lower():
            LOGGER.info("spotify_skill: extracted query %r from goal %r", q, goal)
            return "search", {"query": q}
    return None


def _activate_spotify() -> None:
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Spotify" to activate'],
            capture_output=True,
            timeout=5,
        )
        time.sleep(1)
    except Exception as exc:
        LOGGER.debug("spotify_skill: activate failed (non-fatal): %s", exc)


def _play_track_applescript(uri: str, context_uri: str | None = None) -> None:
    escaped_uri = escape_as_string(uri)
    if context_uri:
        escaped_ctx = escape_as_string(context_uri)
        script = f'tell application "Spotify" to play track "{escaped_uri}" in context "{escaped_ctx}"'
    else:
        script = f'tell application "Spotify" to play track "{escaped_uri}"'
    run_applescript(script)


# Seconds to wait for Spotify's quick-search results before pressing Shift+Enter.
# Tune between 3 and 5 depending on how quickly results return.
_SEARCH_RESULTS_DELAY = 3.0

_PLAYBACK_STATE_SCRIPT = """
tell application "Spotify"
    return (player state as text) & "|||" & (name of current track) & "|||" & (artist of current track)
end tell
"""

_HONEST_FAILURE = "I searched Spotify for {query} but could not start playback automatically."


def _search_and_play_in_spotify_ui(query: str) -> str:
    """Drive Spotify's quick search by keyboard (Cmd+K, type, Shift+Enter) and verify playback.

    Keystroke automation requires Accessibility permission for the process running the backend.
    """
    failure = _HONEST_FAILURE.format(query=query)
    _activate_spotify()
    time.sleep(1.0)
    try:
        run_applescript(
            'tell application "System Events"\n'
            '    keystroke "k" using {command down}\n'
            "    delay 0.5\n"
            f'    keystroke "{escape_as_string(query)}"\n'
            "end tell"
        )
        time.sleep(_SEARCH_RESULTS_DELAY)
        run_applescript('tell application "System Events" to key code 36 using {shift down}')
        time.sleep(2.0)
        state, _, rest = run_applescript(_PLAYBACK_STATE_SCRIPT).partition("|||")
        name, _, artist = rest.partition("|||")
    except RuntimeError as exc:
        LOGGER.warning("spotify_skill: keyboard search/verify failed: %s", exc)
        return failure
    if state.strip().lower() == "playing":
        return f"Playing {name.strip()} by {artist.strip()} on Spotify."
    LOGGER.warning("spotify_skill: playback not confirmed (state=%r, track=%r)", state, name)
    return failure


def _play_track(params: dict) -> str:
    uri = (params.get("direct_parameter") or params.get("uri") or params.get("query") or "").strip()
    if not uri:
        raise ValueError("Missing track URI or query")
    if not uri.startswith("spotify:"):
        return _search_and_play_in_spotify_ui(uri)
    _activate_spotify()
    _play_track_applescript(uri, params.get("in_context") or params.get("in context"))
    return f"Now playing {uri} on Spotify."


def _play(params: dict) -> str:
    run_applescript('tell application "Spotify" to play')
    return "Playback resumed"


def _pause(params: dict) -> str:
    run_applescript('tell application "Spotify" to pause')
    return "Playback paused"


def _playpause(params: dict) -> str:
    run_applescript('tell application "Spotify" to playpause')
    return "Toggled play/pause"


def _next_track(params: dict) -> str:
    run_applescript('tell application "Spotify" to next track')
    return "Skipped to next track"


def _previous_track(params: dict) -> str:
    run_applescript('tell application "Spotify" to previous track')
    return "Skipped to previous track"


def _now_playing(params: dict) -> str:
    script = """
    tell application "Spotify"
        if player state is stopped then
            return "STOPPED"
        end if
        set trackName to name of current track
        set trackArtist to artist of current track
        return trackName & "|||" & trackArtist
    end tell
    """
    raw = run_applescript(script).strip()
    if not raw or raw == "STOPPED":
        return "Nothing is currently playing"
    name, _, artist = raw.partition("|||")
    return f"Currently playing: {name.strip() or 'Unknown'} by {artist.strip() or 'Unknown'}"


def _set_volume(params: dict) -> str:
    vol = params.get("volume")
    if vol is None:
        raise ValueError("Missing 'volume' parameter")
    try:
        vol_int = max(0, min(100, int(vol)))
    except (ValueError, TypeError):
        raise ValueError(f"Invalid volume: {vol}")
    run_applescript(f'tell application "Spotify" to set sound volume to {vol_int}')
    return f"Volume set to {vol_int}%"


def _build_spotify_skill() -> Skill:
    skill = Skill(
        name="spotify",
        description="Spotify — search, play a track URI, pause, skip tracks, check the current track, and adjust volume via local AppleScript",
        app_names={"spotify", "spotify.app"},
    )

    skill.add_intent(
        SkillIntent(
            name="play_track",
            description="Start playback of a Spotify track URI (spotify:track:...). A plain query is searched instead.",
            params=["direct_parameter"],
            handler=_play_track,
        )
    )
    skill.add_intent(
        SkillIntent(
            name="search",
            description="Search Spotify for a song, artist, or topic and start playing the top result automatically. Use this for 'play <song> on Spotify'.",
            params=["query"],
            handler=lambda params: _search_and_play_in_spotify_ui(
                params.get("query") or params.get("direct_parameter") or ""
            ),
        )
    )
    skill.add_intent(
        SkillIntent(
            name="play",
            description="Resume paused playback (does NOT search or start new music).",
            params=[],
            handler=_play,
        )
    )
    skill.add_intent(
        SkillIntent(
            name="pause",
            description="Pause the current playback.",
            params=[],
            handler=_pause,
        )
    )
    skill.add_intent(
        SkillIntent(
            name="playpause",
            description="Toggle play/pause.",
            params=[],
            handler=_playpause,
        )
    )
    skill.add_intent(
        SkillIntent(
            name="next_track",
            description="Skip to the next track.",
            params=[],
            handler=_next_track,
        )
    )
    skill.add_intent(
        SkillIntent(
            name="previous_track",
            description="Go back to the previous track.",
            params=[],
            handler=_previous_track,
        )
    )
    skill.add_intent(
        SkillIntent(
            name="now_playing",
            description="Show what is currently playing.",
            params=[],
            handler=_now_playing,
        )
    )
    skill.add_intent(
        SkillIntent(
            name="set_volume",
            description="Set playback volume (0-100).",
            params=["volume"],
            handler=_set_volume,
        )
    )

    return skill


register_skill(_build_spotify_skill())
