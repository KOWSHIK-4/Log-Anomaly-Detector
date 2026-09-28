"""Tests for the incremental log tailer: appends, truncation and rotation."""

from __future__ import annotations

import os
import time
from pathlib import Path

from app.models import LogLevel
from app.tailer import LogTailer


def write(path: Path, *lines: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


class TestLogTailer:
    def test_reads_only_new_appended_lines(self, tmp_path):
        log = tmp_path / "app.log"
        write(log, "2024-05-14T10:00:00Z INFO first")
        tailer = LogTailer(log, poll_interval=0.01, from_end=False)

        first = tailer.read_new_events()
        assert len(first) == 1
        assert first[0].level is LogLevel.INFO
        assert "first" in first[0].message

        # nothing new -> empty
        assert tailer.read_new_events() == []

        write(log, "2024-05-14T10:00:01Z ERROR second")
        second = tailer.read_new_events()
        assert len(second) == 1
        assert second[0].is_error is True

    def test_offset_advances_only_by_new_bytes(self, tmp_path):
        log = tmp_path / "app.log"
        write(log, "2024-05-14T10:00:00Z INFO a")
        tailer = LogTailer(log, poll_interval=0.01, from_end=False)
        tailer.read_new_events()
        offset_after_first = tailer._offset
        assert offset_after_first == os.path.getsize(log)

        tailer.read_new_events()
        assert tailer._offset == offset_after_first

    def test_partial_line_is_buffered_until_newline(self, tmp_path):
        log = tmp_path / "app.log"
        log.write_text("2024-05-14T10:00:00Z INFO half", encoding="utf-8")
        tailer = LogTailer(log, poll_interval=0.01, from_end=False)

        assert tailer.read_new_events() == []

        with log.open("a", encoding="utf-8") as handle:
            handle.write(" line\n")
        events = tailer.read_new_events()
        assert len(events) == 1
        assert "half line" in events[0].message

    def test_truncation_resets_offset(self, tmp_path):
        log = tmp_path / "app.log"
        write(log, "2024-05-14T10:00:00Z INFO before-rotate")
        tailer = LogTailer(log, poll_interval=0.01, from_end=False)
        assert len(tailer.read_new_events()) == 1

        # logrotate copytruncate style: file shrinks to nothing
        log.write_text("", encoding="utf-8")
        write(log, "2024-05-14T10:05:00Z INFO after-truncate")
        events = tailer.read_new_events()
        assert tailer.truncations >= 1
        assert len(events) == 1
        assert "after-truncate" in events[0].message

    def test_rotation_via_rename(self, tmp_path):
        log = tmp_path / "app.log"
        write(log, "2024-05-14T10:00:00Z INFO original")
        tailer = LogTailer(log, poll_interval=0.01, from_end=False)
        assert len(tailer.read_new_events()) == 1

        os.replace(log, tmp_path / "app.log.1")
        write(log, "2024-05-14T10:10:00Z ERROR new-file")
        events = tailer.read_new_events()
        assert tailer.rotations >= 1
        assert len(events) == 1
        assert events[0].is_error is True

    def test_missing_file_is_tolerated(self, tmp_path):
        log = tmp_path / "nested" / "app.log"
        tailer = LogTailer(log, poll_interval=0.01, from_end=False)
        assert tailer.read_new_events() == []  # created it
        assert log.exists()
        write(log, "2024-05-14T10:00:00Z INFO created")
        assert len(tailer.read_new_events()) == 1

    def test_from_end_skips_existing_content(self, tmp_path):
        log = tmp_path / "app.log"
        write(
            log,
            "2024-05-14T10:00:00Z INFO old-1",
            "2024-05-14T10:00:01Z INFO old-2",
        )
        tailer = LogTailer(log, poll_interval=0.01, from_end=True)
        assert tailer.read_new_events() == []
        write(log, "2024-05-14T10:00:02Z INFO fresh")
        events = tailer.read_new_events()
        assert len(events) == 1
        assert "fresh" in events[0].message

    def test_long_line_truncated(self, tmp_path):
        log = tmp_path / "app.log"
        tailer = LogTailer(log, poll_interval=0.01, from_end=False, max_line_bytes=32)
        write(log, "2024-05-14T10:00:00Z ERROR " + "x" * 500)
        events = tailer.read_new_events()
        assert "[truncated]" in events[0].message
        assert len(events[0].message) < 100

    def test_many_lines_in_one_poll(self, tmp_path):
        log = tmp_path / "app.log"
        tailer = LogTailer(log, poll_interval=0.01, from_end=False)
        write(log, *[f"2024-05-14T10:00:00Z INFO line-{i}" for i in range(500)])
        assert len(tailer.read_new_events()) == 500
