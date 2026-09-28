"""Log line parsing.

Supports the common formats and never raises on malformed input: an
unparsable line still becomes a LogEvent (with ``parse_ok=False``) so it is
counted in metrics instead of crashing the monitor.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional, Tuple

from .models import LogEvent, LogLevel

# 2024-01-01T12:00:00.123Z  /  2024-01-01 12:00:00,123  /  01/Jan/2024:12:00:00 +0000
_TS_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?)"),
    re.compile(r"^(?P<ts>\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}\s[+-]\d{4})"),
    re.compile(r"^(?P<ts>\d{4}/\d{2}/\d{2}\s\d{2}:\d{2}:\d{2})"),
    re.compile(r"^\[?(?P<ts>\d{2}:\d{2}:\d{2}(?:[.,]\d{1,3})?)\]?"),
)

# [LEVEL] / LEVEL: / level=LEVEL / bare LEVEL token
_LEVEL_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"[\[\(<]\s*(?P<lvl>DEBUG|INFO|WARN|WARNING|NOTICE|ERROR|ERR|CRITICAL|CRIT|FATAL|SEVERE|TRACE)\s*[\]\)>]"),
    re.compile(
        r"\b(?:level|lvl|severity)\s*[=:]\s*\"?(?P<lvl>DEBUG|INFO|WARN|WARNING|NOTICE|ERROR|ERR|CRITICAL|CRIT|FATAL|SEVERE|TRACE)\"?",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*(?P<lvl>DEBUG|INFO|WARN|WARNING|NOTICE|ERROR|ERR|CRITICAL|CRIT|FATAL|SEVERE|TRACE)\b\s*[:\-\]]", re.IGNORECASE),
    re.compile(r"^\s*(?P<lvl>DEBUG|INFO|WARN|WARNING|NOTICE|ERROR|ERR|CRITICAL|CRIT|FATAL|SEVERE|TRACE)\s+(?=\S)"),
)

#: Last-resort pattern: a bare uppercase level token. Only trusted near the
#: start of the line so that words like "error" inside a message are ignored.
_BARE_LEVEL_RE = re.compile(
    r"(?<![\w])(?P<lvl>DEBUG|INFO|WARN|WARNING|NOTICE|ERROR|ERR|CRITICAL|CRIT|FATAL|SEVERE|TRACE)(?![\w])"
)
_BARE_LEVEL_MAX_OFFSET = 100

_LEVEL_ALIASES = {
    "NOTICE": LogLevel.INFO,
    "TRACE": LogLevel.DEBUG,
    "ERR": LogLevel.ERROR,
    "CRIT": LogLevel.CRITICAL,
    "SEVERE": LogLevel.CRITICAL,
}

_TS_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%d/%b/%Y:%H:%M:%S %z",
    "%H:%M:%S.%f",
    "%H:%M:%S",
)


def normalize_level(raw: str) -> LogLevel:
    token = raw.strip().upper()
    if token in _LEVEL_ALIASES:
        return _LEVEL_ALIASES[token]
    try:
        return LogLevel(token)
    except ValueError:
        return LogLevel.UNKNOWN


def _parse_timestamp(token: str, fallback: float) -> Tuple[float, bool]:
    cleaned = token.strip().replace(",", ".")
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+0000"
    cleaned = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", cleaned)
    for fmt in _TS_FORMATS:
        try:
            parsed = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            return parsed.timestamp(), True
        return parsed.timestamp(), True
    return fallback, False


def _extract_timestamp(line: str, fallback: float) -> Tuple[Optional[str], float, bool]:
    for pattern in _TS_PATTERNS:
        match = pattern.search(line)
        if not match:
            continue
        token = match.group("ts")
        epoch, ok = _parse_timestamp(token, fallback)
        if ok:
            return token, epoch, True
    return None, fallback, False


def _extract_level(line: str) -> Tuple[Optional[str], str]:
    """Return (level_token, remainder_of_line)."""

    best: Optional[re.Match] = None
    best_pattern: Optional[re.Pattern] = None
    for pattern in _LEVEL_PATTERNS:
        match = pattern.search(line)
        if not match:
            continue
        # Prefer the earliest level token in the line.
        if best is None or match.start() < best.start():
            best, best_pattern = match, pattern
    if best is None:
        bare = _BARE_LEVEL_RE.search(line, 0, _BARE_LEVEL_MAX_OFFSET)
        if bare:
            best = bare
    if best is None:
        return None, line
    remainder = (line[: best.start()] + " " + line[best.end() :]).strip(" \t|-:")
    return best.group("lvl"), remainder


def _extract_key_values(text: str) -> dict:
    extras: dict = {}
    for key, value in re.findall(r"\b([A-Za-z_][\w.\-]{1,30})=(\"[^\"]*\"|'[^']*'|\S+)", text):
        if key in {"level", "lvl", "severity"}:
            continue
        extras[key] = value.strip("\"'")
    return extras


def _strip_leading_timestamp(remainder: str, raw: str) -> str:
    """Drop a leading timestamp token from the message for readability."""

    candidate = remainder.strip()
    if not candidate:
        return raw.strip()
    for pattern in _TS_PATTERNS:
        match = pattern.match(candidate)
        if match:
            rest = candidate[match.end() :].lstrip(" \t[]()<>|-:")
            if rest:
                return rest
    return candidate


def parse_line(line: str, fallback_timestamp: Optional[float] = None) -> LogEvent:
    """Parse a single raw log line into a :class:`LogEvent`."""

    import time as _time

    raw = line.rstrip("\r\n")
    fallback = fallback_timestamp if fallback_timestamp is not None else _time.time()
    timestamp_token, epoch, ts_ok = _extract_timestamp(raw, fallback)
    level_token, remainder = _extract_level(raw)

    if level_token is None:
        # No recognisable level: keep the event but flag it as malformed so
        # the dashboard can surface data-quality problems.
        return LogEvent(
            timestamp=epoch,
            level=LogLevel.UNKNOWN,
            message=raw.strip(),
            raw=raw,
            parse_ok=False,
            parse_error="unrecognized log level",
            extra={"timestampToken": timestamp_token} if timestamp_token else {},
        )

    level = normalize_level(level_token)
    message = _strip_leading_timestamp(remainder.strip(), raw)
    return LogEvent(
        timestamp=epoch,
        level=level,
        message=message,
        raw=raw,
        parse_ok=True,
        parse_error=None if ts_ok else "timestamp missing/invalid, used ingestion time",
        extra=_extract_key_values(message),
    )
