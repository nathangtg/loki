from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.audio.tts.adapter import PCMChunk

DEFAULT_MAX_BUFFERED_BYTES = 96_000
DEFAULT_PUT_TIMEOUT_SECONDS = 5.0


class VoicePCMQueue:
    def __init__(
        self,
        max_buffered_bytes: int = DEFAULT_MAX_BUFFERED_BYTES,
    ):
        self._max_buffered_bytes = max_buffered_bytes
        self._chunks: list["PCMChunk"] = []
        self._base_sequence = 0
        self._buffered_bytes = 0
        self._closed = False
        self._cancelled = False
        self._lock = threading.Lock()
        self._not_full = threading.Condition(self._lock)

    def put(self, chunk: "PCMChunk", timeout: float = DEFAULT_PUT_TIMEOUT_SECONDS) -> bool:
        deadline = time.monotonic() + timeout
        with self._not_full:
            while (
                self._buffered_bytes >= self._max_buffered_bytes
                and not self._closed
                and not self._cancelled
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._not_full.wait(remaining)

            if self._closed or self._cancelled:
                return False

            self._chunks.append(chunk)
            self._buffered_bytes += len(chunk.pcm_f32le)
            return True

    def poll(
        self, after: int, limit: int = 5
    ) -> tuple[list["PCMChunk"], int, int, bool, bool]:
        with self._not_full:
            if after > self._base_sequence:
                trim = min(after - self._base_sequence, len(self._chunks))
                for chunk in self._chunks[:trim]:
                    self._buffered_bytes -= len(chunk.pcm_f32le)
                del self._chunks[:trim]
                self._base_sequence = after
                self._not_full.notify_all()

            offset = max(0, after - self._base_sequence)
            page = self._chunks[offset : offset + limit]
            start_sequence = self._base_sequence + offset
            latest_sequence = self._base_sequence + len(self._chunks)
            return list(page), start_sequence, latest_sequence, self._closed, self._cancelled

    def close(self) -> None:
        with self._not_full:
            self._closed = True
            self._not_full.notify_all()

    def cancel(self) -> None:
        with self._not_full:
            self._cancelled = True
            self._chunks.clear()
            self._buffered_bytes = 0
            self._not_full.notify_all()
