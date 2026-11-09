from __future__ import annotations

import re
from datetime import datetime, date, timedelta

from src.skills.applescript_utils import run_applescript, escape_as_string
from src.skills.base import Skill, SkillIntent
from src.skills.registry import register_skill


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?$")
_TIME_TOKEN_RE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*(am|pm|a\.m\.|p\.m\.)?\b",
    re.IGNORECASE,
)
_TIME_RANGE_SPLIT_RE = re.compile(r"\b(?:to|until|till)\b|\s*[-–—]\s*", re.IGNORECASE)
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_MONTH_NAMES_PAT = "|".join(sorted(_MONTHS.keys(), key=len, reverse=True))
_MONTH_DAY_RE = re.compile(
    rf"\b({_MONTH_NAMES_PAT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:[,\s]+(\d{{4}}))?\b",
    re.IGNORECASE,
)
_DAY_MONTH_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_NAMES_PAT})\.?(?:[,\s]+(\d{{4}}))?\b",
    re.IGNORECASE,
)
_IN_DAYS_RE = re.compile(r"\bin\s+(\d{1,2})\s+days?\b", re.IGNORECASE)


def _parse_date(value: str, default_date: date | None = None) -> date:
    s = (value or "").strip().lower()
    today = date.today()
    if not s:
        if default_date is not None:
            return default_date
        raise ValueError("Empty date")
    if s == "today" or "today" in s or "tonight" in s:
        return today
    if "day after tomorrow" in s:
        return today + timedelta(days=2)
    if s == "tomorrow" or "tomorrow" in s or "tmr" in s:
        return today + timedelta(days=1)

    m_in_days = _IN_DAYS_RE.search(s)
    if m_in_days:
        return today + timedelta(days=int(m_in_days.group(1)))

    iso_m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if iso_m:
        return datetime.strptime(iso_m.group(1), "%Y-%m-%d").date()

    m_md = _MONTH_DAY_RE.search(s)
    if m_md:
        month_num = _MONTHS[m_md.group(1).lower()]
        day_num = int(m_md.group(2))
        year_num = int(m_md.group(3)) if m_md.group(3) else today.year
        candidate = date(year_num, month_num, day_num)
        if not m_md.group(3) and candidate < today - timedelta(days=1):
            candidate = date(year_num + 1, month_num, day_num)
        return candidate

    m_dm = _DAY_MONTH_RE.search(s)
    if m_dm:
        day_num = int(m_dm.group(1))
        month_num = _MONTHS[m_dm.group(2).lower()]
        year_num = int(m_dm.group(3)) if m_dm.group(3) else today.year
        candidate = date(year_num, month_num, day_num)
        if not m_dm.group(3) and candidate < today - timedelta(days=1):
            candidate = date(year_num + 1, month_num, day_num)
        return candidate

    for wd_name, wd_idx in _WEEKDAYS.items():
        if wd_name in s:
            days_ahead = (wd_idx - today.weekday()) % 7
            if days_ahead == 0 or f"next {wd_name}" in s:
                days_ahead = days_ahead or 7
            return today + timedelta(days=days_ahead)

    if default_date is not None:
        return default_date
    raise ValueError(f"Cannot parse date: {value}")


def _extract_time_hm(text: str) -> tuple[int, int] | None:
    s = (text or "").strip().lower()
    if not s:
        return None
    if "noon" in s:
        return 12, 0
    if "midnight" in s:
        return 0, 0

    cleaned = re.sub(r"\d{4}-\d{2}-\d{2}", " ", s)
    cleaned = _MONTH_DAY_RE.sub(" ", cleaned)
    cleaned = _DAY_MONTH_RE.sub(" ", cleaned)
    for m in _TIME_TOKEN_RE.finditer(cleaned):
        raw_h = int(m.group(1))
        raw_m = int(m.group(2)) if m.group(2) is not None else 0
        meridiem = (m.group(3) or "").replace(".", "").lower()
        has_colon = m.group(2) is not None

        if not meridiem and not has_colon:
            continue
        if raw_m > 59:
            continue
        if meridiem in ("am", "pm"):
            if not (1 <= raw_h <= 12):
                continue
            hour = raw_h % 12
            if meridiem == "pm":
                hour += 12
            return hour, raw_m
        if 0 <= raw_h <= 23:
            return raw_h, raw_m
    return None


def _parse_datetime(value: str, default_date: date | None = None) -> datetime:
    s = (value or "").strip()
    if not s:
        raise ValueError("Empty datetime")
    s_lower = s.lower()
    if s_lower == "today":
        return datetime.combine(date.today(), datetime.min.time())
    if s_lower == "tomorrow":
        return datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
    if _ISO_DATE_RE.match(s_lower):
        return datetime.strptime(s, "%Y-%m-%d")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue

    target_date = _parse_date(s, default_date=default_date or date.today())
    hm = _extract_time_hm(s)
    if hm is not None:
        return datetime.combine(target_date, datetime.min.time()).replace(hour=hm[0], minute=hm[1])

    if any(k in s_lower for k in ("today", "tomorrow", "tmr", "tonight", *_WEEKDAYS.keys(), *_MONTHS.keys())):
        return datetime.combine(target_date, datetime.min.time())

    raise ValueError(f"Cannot parse date: {value}")


_AS_MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _as_date_block(var_name: str, dt: datetime | date) -> str:
    year = dt.year
    month_const = _AS_MONTH_NAMES[dt.month]
    day = dt.day
    hour = getattr(dt, "hour", 0)
    minute = getattr(dt, "minute", 0)
    second = getattr(dt, "second", 0)
    return (
        f"set {var_name} to current date\n"
        f"set day of {var_name} to 1\n"
        f"set year of {var_name} to {year}\n"
        f"set month of {var_name} to {month_const}\n"
        f"set day of {var_name} to {day}\n"
        f"set hours of {var_name} to {hour}\n"
        f"set minutes of {var_name} to {minute}\n"
        f"set seconds of {var_name} to {second}"
    )


_ek_store = None


def _get_eventkit_store():
    global _ek_store
    if _ek_store is not None:
        return _ek_store
    try:
        import threading
        import objc

        ns: dict = {}
        objc.loadBundle(
            "EventKit",
            ns,
            bundle_path="/System/Library/Frameworks/EventKit.framework",
        )
        EKEventStore = ns.get("EKEventStore")
        if EKEventStore is None:
            return None

        auth_status = EKEventStore.authorizationStatusForEntityType_(0)
        store = EKEventStore.alloc().init()
        if auth_status == 0:
            done_evt = threading.Event()
            if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
                store.requestFullAccessToEventsWithCompletion_(lambda granted, err: done_evt.set())
            else:
                store.requestAccessToEntityType_completion_(0, lambda granted, err: done_evt.set())
            done_evt.wait(timeout=5.0)
            auth_status = EKEventStore.authorizationStatusForEntityType_(0)

        if auth_status not in (3, 4):
            return None

        _ek_store = (store, ns)
        return _ek_store
    except Exception:
        return None


def _try_eventkit_create_event(title: str, start_dt: datetime, end_dt: datetime) -> bool:
    pair = _get_eventkit_store()
    if pair is None:
        return False
    try:
        from Foundation import NSDate

        store, ns = pair
        EKEvent = ns["EKEvent"]
        cal = store.defaultCalendarForNewEvents()
        if cal is None:
            return False
        ev = EKEvent.eventWithEventStore_(store)
        ev.setTitle_(title)
        ev.setStartDate_(NSDate.dateWithTimeIntervalSince1970_(start_dt.timestamp()))
        ev.setEndDate_(NSDate.dateWithTimeIntervalSince1970_(end_dt.timestamp()))
        ev.setCalendar_(cal)
        res = store.saveEvent_span_commit_error_(ev, 0, True, None)
        ok = res[0] if isinstance(res, tuple) else bool(res)
        return bool(ok)
    except Exception:
        return False


def _try_eventkit_list_events(start_date: date, end_date: date) -> list[str] | None:
    pair = _get_eventkit_store()
    if pair is None:
        return None
    try:
        from Foundation import NSDate

        store, _ = pair
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
        ns_start = NSDate.dateWithTimeIntervalSince1970_(start_dt.timestamp())
        ns_end = NSDate.dateWithTimeIntervalSince1970_(end_dt.timestamp())
        pred = store.predicateForEventsWithStartDate_endDate_calendars_(ns_start, ns_end, None)
        raw_events = store.eventsMatchingPredicate_(pred) or []
        lines: list[str] = []
        for ev in raw_events:
            summary = str(ev.title() or "Untitled")
            s_ts = float(ev.startDate().timeIntervalSince1970())
            e_ts = float(ev.endDate().timeIntervalSince1970())
            s_local = datetime.fromtimestamp(s_ts)
            e_local = datetime.fromtimestamp(e_ts)
            lines.append(f"{summary} ({s_local.hour}:{s_local.minute:02d} \u2013 {e_local.hour}:{e_local.minute:02d})")
        return lines
    except Exception:
        return None


_LIST_EVENTS_TODAY_SCRIPT = '''\
tell application "Calendar"
    set todayStart to current date
    set hours of todayStart to 0
    set minutes of todayStart to 0
    set seconds of todayStart to 0
    set todayEnd to todayStart + (86400)
    set resultList to {}
    repeat with cal in every calendar
        set calEvents to (every event of cal whose start date >= todayStart and start date < todayEnd)
        repeat with ev in calEvents
            set evStart to start date of ev
            set evEnd to end date of ev
            set h1 to hours of evStart as string
            set m1 to text -2 thru -1 of ("00" & (minutes of evStart as string))
            set h2 to hours of evEnd as string
            set m2 to text -2 thru -1 of ("00" & (minutes of evEnd as string))
            set end of resultList to (summary of ev & " (" & h1 & ":" & m1 & " \xe2\x80\x93 " & h2 & ":" & m2 & ")")
        end repeat
    end repeat
    return resultList
end tell\
'''


def _build_list_events_range_script(start_date: date, end_date: date) -> str:
    start_block = _as_date_block("rangeStart", start_date)
    end_block = _as_date_block("rangeEnd", end_date + timedelta(days=1))
    return f'''\
{start_block}
{end_block}
tell application "Calendar"
    set resultList to {{}}
    repeat with cal in every calendar
        set calEvents to (every event of cal whose start date >= rangeStart and start date < rangeEnd)
        repeat with ev in calEvents
            set evStart to start date of ev
            set evEnd to end date of ev
            set h1 to hours of evStart as string
            set m1 to text -2 thru -1 of ("00" & (minutes of evStart as string))
            set h2 to hours of evEnd as string
            set m2 to text -2 thru -1 of ("00" & (minutes of evEnd as string))
            set end of resultList to (summary of ev & " (" & h1 & ":" & m1 & " \xe2\x80\x93 " & h2 & ":" & m2 & ")")
        end repeat
    end repeat
    return resultList
end tell\
'''


def _parse_event_lines(raw: str) -> list[str]:
    if not raw:
        return []
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) == 1 and " – " not in lines[0] and ", " in lines[0]:
        lines = [part.strip() for part in lines[0].split(", ") if part.strip()]
    return lines


def _list_events_today(params: dict) -> str:
    today = date.today()
    ek_lines = _try_eventkit_list_events(today, today)
    if ek_lines is not None:
        return "\n".join(ek_lines) if ek_lines else "No events today."
    raw = run_applescript(_LIST_EVENTS_TODAY_SCRIPT)
    lines = _parse_event_lines(raw)
    if not lines:
        return "No events today."
    return "\n".join(lines)


def _list_events_range(params: dict) -> str:
    start_date = _parse_date(params["start_date"])
    end_date = _parse_date(params["end_date"])
    if end_date < start_date:
        raise ValueError("end_date must not be before start_date")
    ek_lines = _try_eventkit_list_events(start_date, end_date)
    if ek_lines is not None:
        return "\n".join(ek_lines) if ek_lines else "No events in range."
    script = _build_list_events_range_script(start_date, end_date)
    raw = run_applescript(script)
    lines = _parse_event_lines(raw)
    if not lines:
        return "No events in range."
    return "\n".join(lines)


_CALENDAR_CREATE_RE = re.compile(
    r"\b(?:create|add|schedule|book|put|set(?:\s+up)?|make|block)\b.*\b(?:event|meeting|appointment|calendar|schedule|session|call|class|reminder|agenda)\b"
    r"|\b(?:in|on|to)\s+(?:my\s+|the\s+)?calendar\b"
    r"|\b(?:schedule|book)\s+[A-Za-z0-9].*\b(?:on|for|at|from|tomorrow|today|tonight|"
    + _MONTH_NAMES_PAT
    + r")\b",
    re.IGNORECASE,
)
_TITLE_CALLED_RE = re.compile(
    r"\b(?:called|named|titled|title(?:\s+to|\s+as)?|about)\s+['\"]?([^'\",.!?]+?)(?:\s+(?:on|for|at|from|in|tomorrow|today|tonight|this|next|"
    + _MONTH_NAMES_PAT
    + r"|\d{1,2}\s*(?:am|pm|:))\b|$)",
    re.IGNORECASE,
)
_TITLE_FOR_RE = re.compile(
    r"\bfor\s+['\"]?([^'\",.!?]+?)(?:\s+(?:on|at|from|in|tomorrow|today|tonight|this|next|"
    + _MONTH_NAMES_PAT
    + r"|\d{1,2}\s*(?:am|pm|:))\b|$)",
    re.IGNORECASE,
)
_TITLE_EVENT_VERB_RE = re.compile(
    r"\b(?:schedule|book|add|create|set\s+up|make|put)\s+(?:a|an|my|the)?\s*([A-Za-z0-9][A-Za-z0-9\s_-]{0,35}?)\s+(?:in|on|to|for|at|from|tomorrow|today|tonight|this|next|"
    + _MONTH_NAMES_PAT
    + r")\b",
    re.IGNORECASE,
)


def extract_calendar_event_from_goal(goal: str) -> dict[str, str] | None:
    clean = " ".join((goal or "").split()).strip().rstrip(".!?")
    if not clean or not _CALENDAR_CREATE_RE.search(clean):
        return None
    lower = clean.lower()
    if any(w in lower for w in ("list ", "what's on", "what is on", "check my", "show my", "free slot")):
        return None

    try:
        target_date = _parse_date(clean)
    except ValueError:
        if _extract_time_hm(clean) is not None:
            target_date = date.today()
        else:
            return None

    times: list[tuple[int, int]] = []
    cleaned_for_time = re.sub(r"\d{4}-\d{2}-\d{2}", " ", lower)
    cleaned_for_time = _MONTH_DAY_RE.sub(" ", cleaned_for_time)
    cleaned_for_time = _DAY_MONTH_RE.sub(" ", cleaned_for_time)
    for m in _TIME_TOKEN_RE.finditer(cleaned_for_time):
        raw_h = int(m.group(1))
        raw_m = int(m.group(2)) if m.group(2) is not None else 0
        meridiem = (m.group(3) or "").replace(".", "").lower()
        has_colon = m.group(2) is not None
        if not meridiem and not has_colon:
            continue
        if raw_m > 59:
            continue
        if meridiem in ("am", "pm"):
            if not (1 <= raw_h <= 12):
                continue
            h = raw_h % 12 + (12 if meridiem == "pm" else 0)
            times.append((h, raw_m))
        elif 0 <= raw_h <= 23:
            times.append((raw_h, raw_m))

    start_hm = times[0] if len(times) >= 1 else (9, 0)
    end_hm = times[1] if len(times) >= 2 else ((start_hm[0] + 1) % 24, start_hm[1])

    start_dt = datetime.combine(target_date, datetime.min.time()).replace(hour=start_hm[0], minute=start_hm[1])
    end_dt = datetime.combine(target_date, datetime.min.time()).replace(hour=end_hm[0], minute=end_hm[1])
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(hours=1)

    title = "New Event"
    m_called = _TITLE_CALLED_RE.search(clean)
    if m_called:
        cand = m_called.group(1).strip()
        if cand and cand.lower() not in {"me", "my calendar", "calendar", "an event", "event", "it"}:
            title = cand
    if title == "New Event":
        m_for = _TITLE_FOR_RE.search(clean)
        if m_for:
            cand = m_for.group(1).strip()
            if (
                cand
                and not _MONTH_DAY_RE.search(cand)
                and not _DAY_MONTH_RE.search(cand)
                and cand.lower() not in {"me", "my calendar", "calendar", "an event", "event", "it", "today", "tomorrow", "tonight"}
            ):
                title = cand
    if title == "New Event":
        m_verb = _TITLE_EVENT_VERB_RE.search(clean)
        if m_verb:
            cand = m_verb.group(1).strip()
            cand = re.sub(r"\b(?:calendar\s+)?event\b", "", cand, flags=re.IGNORECASE).strip()
            if cand and cand.lower() not in {"new", "calendar", "my", "a", "an"}:
                title = cand.title() if cand.islower() else cand

    return {
        "title": title,
        "start_datetime": start_dt.strftime("%Y-%m-%d %H:%M"),
        "end_datetime": end_dt.strftime("%Y-%m-%d %H:%M"),
    }


def _create_event(params: dict) -> str:
    title = (params.get("title") or "New Event").strip() or "New Event"
    raw_start = (params.get("start_datetime") or "").strip()
    raw_end = (params.get("end_datetime") or "").strip()

    if not raw_end and _TIME_RANGE_SPLIT_RE.search(raw_start):
        parts = _TIME_RANGE_SPLIT_RE.split(raw_start, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            raw_start, raw_end = parts[0].strip(), parts[1].strip()
    elif _TIME_RANGE_SPLIT_RE.search(raw_start) and raw_start == raw_end:
        parts = _TIME_RANGE_SPLIT_RE.split(raw_start, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            raw_start, raw_end = parts[0].strip(), parts[1].strip()

    start_dt = _parse_datetime(raw_start)
    if raw_end:
        end_dt = _parse_datetime(raw_end, default_date=start_dt.date())
    else:
        end_dt = start_dt + timedelta(hours=1)

    if start_dt.hour == 0 and start_dt.minute == 0 and _extract_time_hm(raw_start) is None:
        start_dt = start_dt.replace(hour=9, minute=0)
    if end_dt.hour == 0 and end_dt.minute == 0 and _extract_time_hm(raw_end) is None:
        end_dt = start_dt + timedelta(hours=1)
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(hours=1)

    escaped_title = escape_as_string(title)
    start_block = _as_date_block("evStart", start_dt)
    end_block = _as_date_block("evEnd", end_dt)

    if not _try_eventkit_create_event(title, start_dt, end_dt):
        script = f'''\
{start_block}
{end_block}
tell application "Calendar"
    activate
    set targetCal to missing value
    repeat with c in every calendar
        try
            if writable of c is true and name of c is not in {{"Birthdays", "US Holidays", "Holidays", "Siri Suggestions"}} then
                set targetCal to c
                exit repeat
            end if
        end try
    end repeat
    if targetCal is missing value then
        set targetCal to calendar 1
    end if
    set newEv to make new event at end of events of targetCal with properties {{summary:"{escaped_title}", start date:evStart, end date:evEnd}}
    try
        view calendar at evStart
        show newEv
    end try
end tell\
'''
        run_applescript(script)
    else:
        try:
            run_applescript(f"{start_block}\ntell application \"Calendar\"\n    activate\n    view calendar at evStart\nend tell", timeout=4.0)
        except Exception:
            pass

    time_str = f"{start_dt.strftime('%b %d')} from {start_dt.strftime('%I:%M %p').lstrip('0')} to {end_dt.strftime('%I:%M %p').lstrip('0')}"
    return f"All set! I've scheduled '{title}' on your calendar for {time_str}."


def _find_free_slot(params: dict) -> str:
    raw_date = params.get("date", "today")
    duration_minutes = int(params.get("duration_minutes", 60))

    target_date = _parse_date(raw_date)

    lines = _try_eventkit_list_events(target_date, target_date)
    if lines is None:
        script = _build_list_events_range_script(target_date, target_date)
        raw = run_applescript(script)
        lines = _parse_event_lines(raw)

    time_pattern = re.compile(r"\((\d{1,2}):(\d{2})\s*\u2013\s*(\d{1,2}):(\d{2})\)")
    events: list[tuple[int, int]] = []
    for line in lines:
        m = time_pattern.search(line)
        if m:
            h1, m1, h2, m2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            events.append((h1 * 60 + m1, h2 * 60 + m2))

    events.sort(key=lambda x: x[0])
    day_start = 8 * 60
    day_end = 20 * 60
    cursor = day_start

    for ev_start, ev_end in events:
        if ev_start > cursor and (ev_start - cursor) >= duration_minutes:
            slot_end = cursor + duration_minutes
            return f"Free slot: {cursor // 60:02d}:{cursor % 60:02d} \u2013 {slot_end // 60:02d}:{slot_end % 60:02d}"
        if ev_end > cursor:
            cursor = ev_end

    if (day_end - cursor) >= duration_minutes:
        slot_end = cursor + duration_minutes
        return f"Free slot: {cursor // 60:02d}:{cursor % 60:02d} \u2013 {slot_end // 60:02d}:{slot_end % 60:02d}"

    date_label = target_date.strftime("%Y-%m-%d")
    return f"No free slot of {duration_minutes} min found on {date_label}."


_calendar_skill = Skill(
    name="calendar",
    description="Read and write Apple Calendar events via AppleScript",
    app_names={"calendar", "apple calendar", "ical"},
)

_calendar_skill.add_intent(
    SkillIntent(
        name="list_events_today",
        description="List all calendar events scheduled for today",
        params=[],
        handler=_list_events_today,
    )
)
_calendar_skill.add_intent(
    SkillIntent(
        name="list_events_range",
        description="List calendar events between two dates (inclusive)",
        params=["start_date", "end_date"],
        handler=_list_events_range,
    )
)
_calendar_skill.add_intent(
    SkillIntent(
        name="create_event",
        description="Create a new calendar event with title, start_datetime, and end_datetime (e.g. 'tomorrow 8am', 'tomorrow 10am')",
        params=["start_datetime"],
        handler=_create_event,
    )
)
_calendar_skill.add_intent(
    SkillIntent(
        name="find_free_slot",
        description="Find the first free time slot of a given duration on a given date",
        params=["date", "duration_minutes"],
        handler=_find_free_slot,
    )
)

register_skill(_calendar_skill)
