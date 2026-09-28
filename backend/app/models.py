"""Pydantic models / dataclasses shared by the whole backend."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    FATAL = "FATAL"
    UNKNOWN = "UNKNOWN"

    @property
    def rank(self) -> int:
        return _LEVEL_RANK.get(self.value, 0)


_LEVEL_RANK: Dict[str, int] = {
    "DEBUG": 10,
    "INFO": 20,
    "WARN": 30,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
    "FATAL": 60,
    "UNKNOWN": 0,
}

#: Levels that count towards the "error rate" metric.
ERROR_LEVELS = {LogLevel.ERROR, LogLevel.CRITICAL, LogLevel.FATAL}
#: Levels that count towards the "warning rate" metric.
WARNING_LEVELS = {LogLevel.WARN, LogLevel.WARNING}


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RECOVERED = "RECOVERED"


class SystemStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    ANOMALY = "ANOMALY"
    STARTING = "STARTING"


@dataclass(slots=True)
class LogEvent:
    """A single parsed log line."""

    timestamp: float
    level: LogLevel
    message: str
    raw: str
    source: str = "file"
    line_number: Optional[int] = None
    parse_ok: bool = True
    parse_error: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_error(self) -> bool:
        return self.level in ERROR_LEVELS

    @property
    def is_warning(self) -> bool:
        return self.level in WARNING_LEVELS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "level": self.level.value,
            "message": self.message,
            "raw": self.raw,
            "source": self.source,
            "lineNumber": self.line_number,
            "parseOk": self.parse_ok,
            "parseError": self.parse_error,
            "extra": self.extra,
        }


@dataclass(slots=True)
class WindowStats:
    """Aggregated stats for one evaluation window."""

    window_start: float
    window_end: float
    total: int = 0
    errors: int = 0
    warnings: int = 0
    error_rate: float = 0.0
    warning_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "windowStart": self.window_start,
            "windowEnd": self.window_end,
            "total": self.total,
            "errors": self.errors,
            "warnings": self.warnings,
            "errorRate": round(self.error_rate, 6),
            "warningRate": round(self.warning_rate, 6),
        }


@dataclass(slots=True)
class Alert:
    """An anomaly report surfaced to the UI and (optionally) AWS."""

    id: str
    timestamp: float
    severity: Severity
    status: AlertStatus
    error_rate: float
    baseline_rate: float
    deviation: float
    deviation_sigma: float
    threshold_rate: float
    reason: str
    window_start: float
    window_end: float
    window_seconds: float
    sample_size: int
    error_count: int
    message_sample: str = ""
    top_error_messages: List[Dict[str, Any]] = field(default_factory=list)
    incident_id: str = ""
    sequence: int = 0

    @staticmethod
    def new_id() -> str:
        return f"alert-{uuid.uuid4().hex[:12]}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "severity": self.severity.value,
            "status": self.status.value,
            "errorRate": round(self.error_rate, 6),
            "baselineRate": round(self.baseline_rate, 6),
            "deviation": round(self.deviation, 6),
            "deviationSigma": round(self.deviation_sigma, 3),
            "thresholdRate": round(self.threshold_rate, 6),
            "reason": self.reason,
            "windowStart": self.window_start,
            "windowEnd": self.window_end,
            "windowSeconds": round(self.window_seconds, 3),
            "sampleSize": self.sample_size,
            "errorCount": self.error_count,
            "messageSample": self.message_sample,
            "topErrorMessages": self.top_error_messages,
            "incidentId": self.incident_id,
            "sequence": self.sequence,
        }

    def to_notification(self) -> str:
        return (
            f"[{self.severity.value}] Log anomaly detected | "
            f"error_rate={self.error_rate:.1%} baseline={self.baseline_rate:.1%} "
            f"deviation={self.deviation_sigma:.1f} sigma over {self.window_seconds:.0f}s window | {self.reason}"
        )


@dataclass(slots=True)
class Metrics:
    """Full snapshot pushed to the dashboard."""

    status: SystemStatus
    uptime_seconds: float
    now: float
    log_file: str
    total_logs: int
    total_errors: int
    total_warnings: int
    parse_failures: int
    malformed_lines: int
    lines_per_minute: float
    error_rate: float
    baseline_error_rate: float
    threshold_error_rate: float
    deviation: float
    deviation_sigma: float
    current_window: WindowStats
    baseline_mean: float
    baseline_std: float
    baseline_samples: int
    baseline_ready: bool
    active_anomalies: int
    total_alerts: int
    notifier: Dict[str, Any]
    trend: List[WindowStats] = field(default_factory=list)
    recent_errors: List[LogEvent] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "uptimeSeconds": round(self.uptime_seconds, 2),
            "now": self.now,
            "logFile": self.log_file,
            "totalLogs": self.total_logs,
            "totalErrors": self.total_errors,
            "totalWarnings": self.total_warnings,
            "parseFailures": self.parse_failures,
            "malformedLines": self.malformed_lines,
            "linesPerMinute": round(self.lines_per_minute, 2),
            "errorRate": round(self.error_rate, 6),
            "baselineErrorRate": round(self.baseline_error_rate, 6),
            "thresholdErrorRate": round(self.threshold_error_rate, 6),
            "deviation": round(self.deviation, 6),
            "deviationSigma": round(self.deviation_sigma, 3),
            "currentWindow": self.current_window.to_dict(),
            "baselineMean": round(self.baseline_mean, 6),
            "baselineStd": round(self.baseline_std, 6),
            "baselineSamples": self.baseline_samples,
            "baselineReady": self.baseline_ready,
            "activeAnomalies": self.active_anomalies,
            "totalAlerts": self.total_alerts,
            "notifier": self.notifier,
            "trend": [point.to_dict() for point in self.trend],
            "recentErrors": [event.to_dict() for event in self.recent_errors],
        }


def now() -> float:
    return time.time()
