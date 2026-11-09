from __future__ import annotations

import logging
import re
import subprocess
from typing import Any
from urllib.parse import urlparse

from src.skills.applescript_utils import escape_as_string, run_applescript
from src.skills.base import Skill, SkillIntent
from src.skills.registry import register_skill

LOGGER = logging.getLogger(__name__)

_EMAIL_OR_PHONE_RE = re.compile(r"^(\+?\d[\d\s\-()]{5,}|[^@\s]+@[^@\s]+\.[^@\s]+)$")
_ALLOWED_MEETING_SCHEMES = {"msteams", "zoommtg", "zoomus", "https"}
_ALLOWED_MEETING_HOSTS = {
    "teams.microsoft.com",
    "gov.teams.microsoft.us",
    "zoom.us",
}


def resolve_contact_info(name_query: str) -> list[dict[str, str]]:
    clean = escape_as_string((name_query or "").strip())
    if not clean:
        return []
    script = f'''
    tell application "Contacts"
        set matchedPeople to (every person whose name contains "{clean}")
        set fetchCount to count of matchedPeople
        if fetchCount is 0 then return "EMPTY"
        if fetchCount > 5 then set fetchCount to 5
        set out to ""
        repeat with i from 1 to fetchCount
            set p to item i of matchedPeople
            set pName to name of p
            set pEmail to ""
            set pPhone to ""
            try
                if (count of emails of p) > 0 then
                    set pEmail to value of item 1 of emails of p
                end if
            end try
            try
                if (count of phones of p) > 0 then
                    set pPhone to value of item 1 of phones of p
                end if
            end try
            set out to out & pName & "|||" & pEmail & "|||" & pPhone & "~~~"
        end repeat
        return out
    end tell
    '''
    try:
        raw = run_applescript(script, timeout=10.0).strip()
    except Exception as exc:
        LOGGER.debug("contacts: AppleScript lookup failed: %s", exc)
        return []

    if not raw or raw == "EMPTY":
        return []

    results: list[dict[str, str]] = []
    for item in raw.split("~~~"):
        item = item.strip()
        if not item:
            continue
        parts = [p.strip() for p in item.split("|||")]
        results.append({
            "name": parts[0] if len(parts) > 0 else "",
            "email": parts[1] if len(parts) > 1 else "",
            "phone": parts[2] if len(parts) > 2 else "",
        })
    return results


def _search_contact(params: dict[str, Any]) -> str:
    name = (params.get("name") or params.get("query") or "").strip()
    if not name:
        raise ValueError("Missing 'name' parameter")
    matches = resolve_contact_info(name)
    if not matches:
        return f"No contact found matching '{name}'."
    lines = []
    for m in matches:
        details = []
        if m.get("email"):
            details.append(f"email: {m['email']}")
        if m.get("phone"):
            details.append(f"phone: {m['phone']}")
        suffix = f" ({', '.join(details)})" if details else " (no email/phone listed)"
        lines.append(f"- {m['name']}{suffix}")
    return f"Contacts matching '{name}':\n" + "\n".join(lines)


def _get_contact_email(params: dict[str, Any]) -> str:
    name = (params.get("name") or "").strip()
    if not name:
        raise ValueError("Missing 'name' parameter")
    matches = resolve_contact_info(name)
    for m in matches:
        if m.get("email"):
            return f"{m['name']}: {m['email']}"
    return f"No email address found in Contacts for '{name}'."


def _get_contact_phone(params: dict[str, Any]) -> str:
    name = (params.get("name") or "").strip()
    if not name:
        raise ValueError("Missing 'name' parameter")
    matches = resolve_contact_info(name)
    for m in matches:
        if m.get("phone"):
            return f"{m['name']}: {m['phone']}"
    return f"No phone number found in Contacts for '{name}'."


def _send_message(params: dict[str, Any]) -> str:
    recipient = (params.get("recipient") or params.get("to") or "").strip()
    body = (params.get("body") or params.get("text") or "").strip()
    if not recipient or not body:
        raise ValueError("Both 'recipient' and 'body' parameters are required")

    handle = recipient
    if not _EMAIL_OR_PHONE_RE.match(recipient):
        matches = resolve_contact_info(recipient)
        if matches:
            handle = matches[0].get("phone") or matches[0].get("email") or recipient

    safe_handle = escape_as_string(handle)
    safe_body = escape_as_string(body)
    script = f'''
    tell application "Messages"
        set targetService to 1st account whose service type = iMessage
        set targetBuddy to participant "{safe_handle}" of targetService
        send "{safe_body}" to targetBuddy
    end tell
    '''
    run_applescript(script, timeout=15.0)
    return f"Sent iMessage to {recipient} ({handle})."


def _music_play_pause(params: dict[str, Any]) -> str:
    run_applescript('tell application "Music" to playpause')
    return "Toggled Apple Music play/pause."


def _music_next_track(params: dict[str, Any]) -> str:
    run_applescript('tell application "Music" to next track')
    return "Skipped to next track in Apple Music."


def _music_previous_track(params: dict[str, Any]) -> str:
    run_applescript('tell application "Music" to previous track')
    return "Skipped to previous track in Apple Music."


def _music_now_playing(params: dict[str, Any]) -> str:
    script = '''
    tell application "Music"
        if player state is stopped then
            return "STOPPED"
        end if
        return (name of current track) & " by " & (artist of current track)
    end tell
    '''
    raw = run_applescript(script).strip()
    if not raw or raw == "STOPPED":
        return "Nothing is currently playing in Apple Music."
    return f"Currently playing in Apple Music: {raw}"


def _music_play_playlist(params: dict[str, Any]) -> str:
    name = (params.get("name") or "").strip()
    if not name:
        raise ValueError("Missing playlist 'name' parameter")
    safe_name = escape_as_string(name)
    script = f'''
    tell application "Music"
        activate
        play playlist "{safe_name}"
    end tell
    '''
    run_applescript(script)
    return f"Playing playlist '{name}' in Apple Music."


def _music_search_and_play(params: dict[str, Any]) -> str:
    query = (params.get("query") or "").strip()
    if not query:
        raise ValueError("Missing 'query' parameter")
    safe_q = escape_as_string(query)
    script = f'''
    tell application "Music"
        activate
        set foundTracks to (search library playlist 1 for "{safe_q}")
        if (count of foundTracks) is 0 then
            return "NOT_FOUND"
        end if
        set chosenTrack to item 1 of foundTracks
        play chosenTrack
        return (name of chosenTrack) & " by " & (artist of chosenTrack)
    end tell
    '''
    raw = run_applescript(script).strip()
    if raw == "NOT_FOUND":
        return f"No tracks matching '{query}' found in local Apple Music library."
    return f"Now playing {raw} in Apple Music."


def _join_meeting_url(params: dict[str, Any]) -> str:
    url = (params.get("url") or "").strip()
    if not url:
        raise ValueError("Missing meeting 'url' parameter")
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()

    if scheme not in _ALLOWED_MEETING_SCHEMES:
        raise ValueError(f"Unsupported meeting URL scheme: {scheme!r}")
    if scheme == "https" and not (
        host in _ALLOWED_MEETING_HOSTS or host.endswith(".zoom.us") or host.endswith(".teams.microsoft.com")
    ):
        raise ValueError(f"Host '{host}' is not a recognized Zoom or Microsoft Teams meeting domain")

    res = subprocess.run(
        ["/usr/bin/open", url],
        capture_output=True,
        text=True,
        timeout=5.0,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"Failed to launch meeting URL: {(res.stderr or '').strip()}")
    return f"Launched meeting client for {host or scheme}."


_contacts_skill = Skill(
    name="contacts",
    description="Look up people, email addresses, and phone numbers in Apple Contacts",
    app_names={"contacts", "apple contacts", "contacts.app", "address book"},
)
_contacts_skill.add_intent(
    SkillIntent(
        name="search_contact",
        description="Search Apple Contacts by person name and return matching names, emails, and phone numbers",
        params=["name"],
        handler=_search_contact,
    )
)
_contacts_skill.add_intent(
    SkillIntent(
        name="get_contact_email",
        description="Look up a person's email address by name in Apple Contacts",
        params=["name"],
        handler=_get_contact_email,
    )
)
_contacts_skill.add_intent(
    SkillIntent(
        name="get_contact_phone",
        description="Look up a person's phone number by name in Apple Contacts",
        params=["name"],
        handler=_get_contact_phone,
    )
)

_messages_skill = Skill(
    name="messages",
    description="Send iMessages via Apple Messages (automatically resolves contact names via Apple Contacts)",
    app_names={"messages", "apple messages", "messages.app", "imessage"},
)
_messages_skill.add_intent(
    SkillIntent(
        name="send_message",
        description="Send an iMessage to a recipient (phone number, Apple ID email, or contact name) with body text",
        params=["recipient", "body"],
        handler=_send_message,
    )
)

_music_skill = Skill(
    name="music",
    description="Control Apple Music playback, playlists, and local library search via AppleScript",
    app_names={"music", "apple music", "music.app", "itunes"},
)
_music_skill.add_intent(
    SkillIntent(
        name="play_pause",
        description="Toggle play/pause in Apple Music",
        params=[],
        handler=_music_play_pause,
    )
)
_music_skill.add_intent(
    SkillIntent(
        name="next_track",
        description="Skip to the next track in Apple Music",
        params=[],
        handler=_music_next_track,
    )
)
_music_skill.add_intent(
    SkillIntent(
        name="previous_track",
        description="Return to the previous track in Apple Music",
        params=[],
        handler=_music_previous_track,
    )
)
_music_skill.add_intent(
    SkillIntent(
        name="now_playing",
        description="Get the title and artist of the currently playing track in Apple Music",
        params=[],
        handler=_music_now_playing,
    )
)
_music_skill.add_intent(
    SkillIntent(
        name="play_playlist",
        description="Play a named playlist in Apple Music",
        params=["name"],
        handler=_music_play_playlist,
    )
)
_music_skill.add_intent(
    SkillIntent(
        name="search_and_play_library",
        description="Search the local Apple Music library for a track/artist and play the top match",
        params=["query"],
        handler=_music_search_and_play,
    )
)

_meetings_skill = Skill(
    name="meetings",
    description="Join Microsoft Teams or Zoom meetings directly via validated deep-link URLs",
    app_names={"teams", "microsoft teams", "zoom", "zoom.us"},
)
_meetings_skill.add_intent(
    SkillIntent(
        name="join_meeting_url",
        description="Open a Microsoft Teams or Zoom meeting URL directly in the native desktop client",
        params=["url"],
        handler=_join_meeting_url,
    )
)

register_skill(_contacts_skill)
register_skill(_messages_skill)
register_skill(_music_skill)
register_skill(_meetings_skill)
