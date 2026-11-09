from __future__ import annotations

import logging
import re

from src.graph.llm import call_llm
from src.skills.applescript_utils import run_applescript, escape_as_string
from src.skills.base import Skill, SkillIntent
from src.skills.registry import register_skill

LOGGER = logging.getLogger(__name__)

_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_MAIL_READ_RE = re.compile(
    r"\b(?:fetch|read|get|show|check|summarize|summarise|list|what(?:'s|\s+are))\b.*\b(?:emails?|inbox|messages?|mail)\b"
    r"|\b(?:latest|recent|last|unread|new)\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)?\s*(?:emails?|messages?|mail)\b",
    re.IGNORECASE,
)


def extract_mail_intent_from_goal(goal: str) -> tuple[str, dict] | None:
    clean = " ".join((goal or "").split()).strip()
    if not clean or not _MAIL_READ_RE.search(clean):
        return None
    lower = clean.lower()
    if any(w in lower for w in ("send ", "compose ", "write an email", "reply ")):
        return None

    if "how many unread" in lower or ("unread count" in lower):
        return "list_unread", {}

    count = 5
    m_digit = re.search(r"\b(\d{1,2})\s+(?:latest|recent|last|unread|new)?\s*(?:emails?|messages?)\b", lower)
    if not m_digit:
        m_digit = re.search(r"\b(?:latest|recent|last|top)\s+(\d{1,2})\b", lower)
    if m_digit:
        count = max(1, min(20, int(m_digit.group(1))))
    else:
        for word, num in _WORD_NUMBERS.items():
            if re.search(rf"\b{word}\b", lower):
                count = num
                break

    if "unread" in lower and "latest" not in lower and "recent" not in lower:
        return "summarize_inbox", {"count": str(count)}

    return "read_recent_emails", {
        "count": str(count),
        "summarize": "true" if ("summarize" in lower or "summarise" in lower or "summary" in lower) else "false",
    }


def _send_email(params: dict) -> str:
    to = escape_as_string(params["to"])
    subject = escape_as_string(params.get("subject", ""))
    body = escape_as_string(params.get("body", ""))

    script = f'''
    tell application "Mail"
        set newMessage to make new outgoing message with properties {{subject:"{subject}", content:"{body}", visible:true}}
        tell newMessage
            make new to recipient at end of to recipients with properties {{address:"{to}"}}
        end tell
        send newMessage
    end tell
    '''
    run_applescript(script)
    return f"Email sent to {params['to']}"


def _draft_email(params: dict) -> str:
    subject = escape_as_string(params.get("subject", ""))
    body = escape_as_string(params.get("body", ""))

    script = f'''
    tell application "Mail"
        make new outgoing message with properties {{subject:"{subject}", content:"{body}", visible:true}}
    end tell
    '''
    run_applescript(script)
    return f"Draft email '{params.get('subject', '')}' opened in Mail (not sent)."


def _list_unread(params: dict) -> str:
    script = '''
    tell application "Mail"
        set unreadCount to unread count of inbox
        return unreadCount as string
    end tell
    '''
    count = run_applescript(script)
    return f"You have {count} unread message(s) in your inbox."


def _parse_delimited_emails(raw: str) -> list[dict[str, str]]:
    if not raw or raw.strip() == "EMPTY":
        return []
    entries: list[dict[str, str]] = []
    for chunk in raw.split("~~~"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split("|||")]
        if len(parts) >= 2:
            entries.append(
                {
                    "subject": parts[0] or "(no subject)",
                    "sender": parts[1] or "Unknown sender",
                    "snippet": parts[2][:200] if len(parts) >= 3 else "",
                }
            )
        else:
            entries.append({"subject": chunk, "sender": "", "snippet": ""})
    return entries


def _maybe_summarize_emails(entries: list[dict[str, str]], count: int, want_summary: bool) -> str:
    lines = []
    for e in entries:
        sender_part = f" from {e['sender']}" if e.get("sender") else ""
        snippet_part = f" — {e['snippet']}" if e.get("snippet") else ""
        lines.append(f"- {e['subject']}{sender_part}{snippet_part}")
    formatted = "\n".join(lines)

    if want_summary and entries:
        try:
            summary = call_llm(
                system=(
                    "You are Loki, a warm, concise voice assistant on macOS. "
                    "Summarize these recent emails for the user in 2-4 natural spoken sentences "
                    "(no markdown, no bullet points, no email addresses in angle brackets)."
                ),
                user=f"Recent {len(entries)} emails:\n{formatted}",
                num_predict=200,
                temperature=0.2,
            ).strip()
            if summary:
                return summary
        except Exception as exc:
            LOGGER.debug("mail_skill: LLM email summary failed, returning formatted list: %s", exc)

    return f"Here are your {len(entries)} latest email(s):\n{formatted}"


def _read_recent_emails(params: dict) -> str:
    try:
        count = max(1, min(20, int(params.get("count") or 5)))
    except (ValueError, TypeError):
        count = 5
    want_summary = str(params.get("summarize", "true")).lower() in ("true", "1", "yes")

    script = f'''
    tell application "Mail"
        activate
        set msgCount to (count of messages of inbox)
        if msgCount is 0 then return "EMPTY"
        set fetchCount to {count}
        if fetchCount > msgCount then set fetchCount to msgCount
        set output to ""
        repeat with i from 1 to fetchCount
            set msg to message i of inbox
            set senderName to sender of msg
            set subjectLine to subject of msg
            set bodyPreview to ""
            try
                set fullBody to content of msg
                if (length of fullBody) > 160 then
                    set bodyPreview to text 1 thru 160 of fullBody
                else
                    set bodyPreview to fullBody
                end if
            end try
            set output to output & subjectLine & "|||" & senderName & "|||" & bodyPreview & "~~~"
        end repeat
        return output
    end tell
    '''
    raw = run_applescript(script, timeout=20.0).strip()
    entries = _parse_delimited_emails(raw)
    if not entries:
        return "No messages in inbox."
    return _maybe_summarize_emails(entries, count, want_summary)


def _summarize_inbox(params: dict) -> str:
    try:
        max_count = max(1, min(15, int(params.get("count") or 10)))
    except (ValueError, TypeError):
        max_count = 10

    script = f'''
    tell application "Mail"
        activate
        set unreadMsgs to (messages of inbox whose read status is false)
        set fetchCount to count of unreadMsgs
        if fetchCount is 0 then return "EMPTY"
        if fetchCount > {max_count} then set fetchCount to {max_count}
        set output to ""
        repeat with i from 1 to fetchCount
            set msg to item i of unreadMsgs
            set senderName to sender of msg
            set subjectLine to subject of msg
            set output to output & subjectLine & "|||" & senderName & "~~~"
        end repeat
        return output
    end tell
    '''
    raw = run_applescript(script, timeout=20.0).strip()
    entries = _parse_delimited_emails(raw)
    if not entries:
        return "No unread messages in inbox."
    return _maybe_summarize_emails(entries, len(entries), True)


def _search_emails(params: dict) -> str:
    query = escape_as_string(params["query"])
    script = f'''
    tell application "Mail"
        activate
        set matchedMsgs to (messages of inbox whose subject contains "{query}")
        set fetchCount to count of matchedMsgs
        if fetchCount is 0 then return "EMPTY"
        if fetchCount > 10 then set fetchCount to 10
        set output to ""
        repeat with i from 1 to fetchCount
            set msg to item i of matchedMsgs
            set senderName to sender of msg
            set subjectLine to subject of msg
            set output to output & subjectLine & "|||" & senderName & "~~~"
        end repeat
        return output
    end tell
    '''
    raw = run_applescript(script, timeout=20.0).strip()
    entries = _parse_delimited_emails(raw)
    if not entries:
        return f"No emails matching '{params['query']}' found."
    formatted = "\n".join(f"- {e['subject']} | from: {e['sender']}" for e in entries)
    return f"Emails matching '{params['query']}':\n{formatted}"


def _reply_to_last(params: dict) -> str:
    body = escape_as_string(params["body"])
    script = f'''
    tell application "Mail"
        if (count of messages of inbox) is 0 then
            return "EMPTY"
        end if
        set lastMsg to message 1 of inbox
        set replyMsg to reply lastMsg
        tell replyMsg
            set content to "{body}"
        end tell
        send replyMsg
        return "OK"
    end tell
    '''
    result = run_applescript(script)
    if result == "EMPTY":
        raise RuntimeError("Inbox is empty — no message to reply to.")
    return "Reply sent."


_mail_skill = Skill(
    name="mail",
    description="Send, read, search, and summarize email via Apple Mail's AppleScript dictionary",
    app_names={"mail", "apple mail", "mail.app", "email", "emails", "inbox"},
)
_mail_skill.add_intent(
    SkillIntent(
        name="send_email",
        description="Compose and send an email",
        params=["to", "subject", "body"],
        handler=_send_email,
    )
)
_mail_skill.add_intent(
    SkillIntent(
        name="draft_email",
        description="Open a visible, unsent draft email with a subject and body for the user to review",
        params=["subject", "body"],
        handler=_draft_email,
    )
)
_mail_skill.add_intent(
    SkillIntent(
        name="list_unread",
        description="Get the count of unread messages in the inbox",
        params=[],
        handler=_list_unread,
    )
)
_mail_skill.add_intent(
    SkillIntent(
        name="read_recent_emails",
        description="Read the subject and sender of the N most recent inbox messages (default 5)",
        params=[],
        handler=_read_recent_emails,
    )
)
_mail_skill.add_intent(
    SkillIntent(
        name="summarize_inbox",
        description="Return subject and sender of the top 10 unread messages for LLM summarization",
        params=[],
        handler=_summarize_inbox,
    )
)
_mail_skill.add_intent(
    SkillIntent(
        name="search_emails",
        description="Search inbox messages whose subject contains the given query string",
        params=["query"],
        handler=_search_emails,
    )
)
_mail_skill.add_intent(
    SkillIntent(
        name="reply_to_last",
        description="Reply to the most recent message in the inbox with the given body text",
        params=["body"],
        handler=_reply_to_last,
    )
)

register_skill(_mail_skill)
