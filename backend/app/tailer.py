"""Incremental log file tailer.

Only the newly appended bytes are read on every poll - the file is never
re-read from the start. Handles truncation and rotation (inode/size reset).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Iterator, List, Optional

from .parser import parse_line
from .models import LogEvent

#: How many leading bytes are fingerprinted to detect truncate/rotate.
_HEAD_SIZE = 512


class LogTailer:
    """Follow a growing file and yield newly appended :class:`LogEvent`."""

    def __init__(
        self,
        path: Path,
        poll_interval: float = 0.2,
        max_line_bytes: int = 8192,
        from_end: bool = True,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.path = Path(path)
        self.poll_interval = poll_interval
        self.max_line_bytes = max_line_bytes
        self.on_error = on_error
        self._buffer = b""
        self._offset = 0
        self._inode: Optional[int] = None
        self._head = b""
        self._started = False
        self.rotations = 0
        self.truncations = 0
        self.bytes_read = 0
        if from_end:
            # Skip whatever is already in the file and only follow new data.
            self._offset = self._size()

    # ------------------------------------------------------------------
    def _size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def _current_inode(self) -> Optional[int]:
        try:
            stat = self.path.stat()
        except OSError:
            return None
        if hasattr(stat, "st_ino") and getattr(stat, "st_ino", 0):
            return int(stat.st_ino)
        return int(getattr(stat, "st_dev", 0))

    def _handle_reset(self) -> bool:
        """Detect truncation / rotation. Returns True if state was reset."""

        size = self._size()
        inode = self._current_inode()

        rotated = inode is not None and self._inode is not None and inode != self._inode
        if rotated:
            self.rotations += 1
            self._reset_state()
            self._inode = inode
            return True

        if size < self._offset:
            # File shrank: truncated in place (logrotate copytruncate style).
            self.truncations += 1
            self._reset_state()
            return True

        if self._offset > 0 and self._head:
            # The file may have been truncated and then regrown past the old
            # offset, which a size check alone cannot see. Comparing the head
            # of the file catches that.
            head = self._read_head()
            if head and not head.startswith(self._head[: len(head)]):
                self.truncations += 1
                self._reset_state()
                return True
        return False

    def _reset_state(self) -> None:
        self._offset = 0
        self._buffer = b""
        self._head = b""

    def _read_head(self) -> bytes:
        """First bytes of the file - used to detect truncate/rotate reliably."""

        try:
            with self.path.open("rb") as handle:
                return handle.read(_HEAD_SIZE)
        except OSError:
            return b""

    def _read_new_bytes(self) -> bytes:
        size = self._size()
        if size <= self._offset:
            return b""
        try:
            with self.path.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read(size - self._offset)
        except OSError as exc:  # file disappeared mid-read (rotation window)
            self._report(f"read error: {exc}")
            return b""
        self._offset += len(chunk)
        self.bytes_read += len(chunk)
        if not self._head:
            # Remember the beginning of the file for rotation detection.
            self._head = self._read_head()
        return chunk

    # ------------------------------------------------------------------
    def read_new_events(self) -> List[LogEvent]:
        """Return every complete log line appended since the last call."""

        if not self.path.exists():
            if not self._started:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.path.touch()
                except OSError as exc:
                    self._report(f"cannot create log file: {exc}")
                    return []
            else:
                self._handle_missing()
                return []

        self._handle_reset()
        self._started = True
        self._inode = self._current_inode()

        chunk = self._read_new_bytes()
        if not chunk and not self._buffer:
            return []

        self._buffer += chunk
        events: List[LogEvent] = []
        while b"\n" in self._buffer:
            raw, self._buffer = self._buffer.split(b"\n", 1)
            line = raw.decode("utf-8", errors="replace")
            if self.max_line_bytes and len(line) > self.max_line_bytes:
                line = line[: self.max_line_bytes] + "...[truncated]"
            events.append(parse_line(line))
        return events

    def _handle_missing(self) -> None:
        """The watched file vanished (likely rotated away)."""

        if self._current_inode() is not None:
            return
        if self._inode is not None:
            self.rotations += 1
            self._reset_state()
        self._inode = None
        self._started = False
        self._report("log file missing; waiting for it to reappear")

    def follow(self, stop_event=None) -> Iterator[LogEvent]:
        """Blocking generator that keeps yielding events forever."""

        while stop_event is None or not stop_event.is_set():
            events = self.read_new_events()
            for event in events:
                yield event
            if not events:
                time.sleep(self.poll_interval)

    def _report(self, message: str) -> None:
        if self.on_error:
            try:
                self.on_error(message)
            except Exception:  # pragma: no cover - never let logging break the loop
                pass

    def close(self) -> None:
        try:
            os.close  # no-op, kept for symmetry with future fd-based readers
        except Exception:
            pass
