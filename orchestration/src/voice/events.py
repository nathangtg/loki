from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any

MAX_BUFFERED_EVENTS = 256


@dataclass(frozen=True)
class VoiceEvent:
    sequence: int
    type: str
    data: dict[str, Any] = field(default_factory=dict)


class VoiceEventLog:
    def __init__(self, max_buffered: int = MAX_BUFFERED_EVENTS):
        self._max_buffered = max_buffered
        self._events: deque[VoiceEvent] = deque()
        self._next_sequence = 1
        self._dropped = 0
        self._lock = threading.Lock()

    def emit(self, event_type: str, **data: Any) -> VoiceEvent:
        with self._lock:
            event = VoiceEvent(self._next_sequence, event_type, data)
            self._next_sequence += 1
            self._events.append(event)
            while len(self._events) > self._max_buffered:
                self._events.popleft()
                self._dropped += 1
            return event

    def poll(self, after: int = 0) -> tuple[list[VoiceEvent], int, int]:
        with self._lock:
            events = [e for e in self._events if e.sequence > after]
            latest = self._next_sequence - 1
            return events, latest, self._dropped
