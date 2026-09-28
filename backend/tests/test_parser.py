"""Tests for the log line parser."""

from __future__ import annotations

import time

from app.models import LogLevel
from app.parser import normalize_level, parse_line


class TestParseLine:
    def test_standard_iso_line(self):
        event = parse_line("2024-05-14T10:15:02.114Z INFO  [api-gateway] request completed status=200")
        assert event.level is LogLevel.INFO
        assert event.is_error is False
        assert "request completed" in event.message
        assert "api-gateway" in event.message
        assert event.extra.get("status") == "200"
        assert event.parse_ok is True

    def test_bracketed_error(self):
        event = parse_line("[2024-05-14 10:15:05.771Z] [ERROR] [db-primary] connection refused")
        assert event.level is LogLevel.ERROR
        assert event.is_error is True

    def test_logfmt_level(self):
        event = parse_line('2024/05/14 10:15:04 level=ERROR service=payment msg="upstream timeout"')
        assert event.level is LogLevel.ERROR
        assert "upstream timeout" in event.message
        assert event.extra.get("service") == "payment"

    def test_severity_keyword(self):
        event = parse_line("2024-05-14T10:00:00Z severity:CRITICAL everything is on fire")
        assert event.level is LogLevel.CRITICAL
        assert event.is_error is True

    def test_syslog_style_timestamp(self):
        event = parse_line("14/May/2024:10:15:04 +0000 ERROR nginx upstream timed out")
        assert event.level is LogLevel.ERROR

    def test_malformed_line_does_not_raise(self):
        event = parse_line("total garbage without structure 12345")
        assert event.parse_ok is False
        assert event.level is LogLevel.UNKNOWN
        assert event.parse_error

    def test_missing_timestamp_uses_fallback(self):
        fallback = time.time()
        event = parse_line("ERROR disk full", fallback_timestamp=fallback)
        assert event.level is LogLevel.ERROR
        assert abs(event.timestamp - fallback) < 0.001
        assert "timestamp" in (event.parse_error or "")

    def test_critical_counts_as_error(self):
        for line in (
            "2024-05-14T10:00:00Z CRITICAL db gone",
            "2024-05-14T10:00:00Z FATAL process killed",
        ):
            assert parse_line(line).is_error is True

    def test_warning_aliases(self):
        assert parse_line("2024-05-14T10:00:00Z WARN x").is_warning is True
        assert parse_line("2024-05-14T10:00:00Z WARNING x").is_warning is True
        assert parse_line("2024-05-14T10:00:00Z NOTICE x").level is LogLevel.INFO

    def test_level_aliases(self):
        assert normalize_level("err") is LogLevel.ERROR
        assert normalize_level("CRIT") is LogLevel.CRITICAL
        assert normalize_level("trace") is LogLevel.DEBUG
        assert normalize_level("nonsense") is LogLevel.UNKNOWN

    def test_extra_key_values_parsed(self):
        event = parse_line("2024-05-14T10:00:00Z INFO svc=auth user_id=42 duration_ms=13")
        assert event.extra["svc"] == "auth"
        assert event.extra["user_id"] == "42"
        assert event.extra["duration_ms"] == "13"

    def test_empty_line(self):
        event = parse_line("")
        assert event.parse_ok is False
        assert event.level is LogLevel.UNKNOWN
