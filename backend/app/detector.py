"""Anomaly detection: sliding window error rate vs. a learned baseline.

Method (deliberately simple and explainable)
--------------------------------------------
1. every ``DETECT_EVALUATE_EVERY`` seconds the events of the last
   ``DETECT_WINDOW_SECONDS`` are aggregated into one window sample
2. historical (non-anomalous) samples form the baseline: mean + stddev
3. an anomaly is flagged when ``error_rate > mean + sigma_multiplier * std``
   (floored by ``min_error_rate``) and the window holds at least
   ``min_samples`` events
4. severity comes from the *overage*: how many multiples of the threshold
   the current error rate is
       overage = (error_rate - threshold) / threshold
       LOW      : any confirmed breach
       MEDIUM   : overage >= severity_medium_overage
       HIGH     : overage >= severity_high_overage
       CRITICAL : overage >= severity_critical_overage
                  or error_rate >= severity_critical_rate
5. duplicate suppression: while an incident is open no new alert is raised
   unless the severity *escalates*; recovery closes the incident.
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from .config import Settings
from .models import Alert, AlertStatus, LogEvent, Severity, SystemStatus, WindowStats

_ID_RE = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_NUM_RE = re.compile(r"\d+")


def signature(message: str) -> str:
    """Collapse variable parts of a message so similar errors group together."""

    cleaned = _ID_RE.sub("<id>", message)
    cleaned = _NUM_RE.sub("<n>", cleaned)
    return cleaned[:160]


SEVERITY_RANK: Dict[str, int] = {
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}


@dataclass
class Baseline:
    """Trailing statistics of historical window error rates."""

    samples: Deque[float] = field(default_factory=lambda: deque(maxlen=50))
    mean: float = 0.0
    std: float = 0.0
    count: int = 0
    min_rate: float = 0.0
    max_rate: float = 0.0

    @property
    def ready(self) -> bool:
        return self.count > 0

    def add(self, rate: float, maxlen: int) -> None:
        if maxlen and len(self.samples) >= maxlen:
            self.samples.popleft()
        self.samples.append(rate)
        self.count += 1
        self.min_rate = min(self.samples)
        self.max_rate = max(self.samples)
        self.mean = math.fsum(self.samples) / len(self.samples)
        if len(self.samples) > 1:
            variance = math.fsum((v - self.mean) ** 2 for v in self.samples) / (len(self.samples) - 1)
            self.std = math.sqrt(variance)
        else:
            self.std = 0.0

    def to_dict(self) -> dict:
        return {
            "mean": round(self.mean, 6),
            "std": round(self.std, 6),
            "count": self.count,
            "min": round(self.min_rate, 6),
            "max": round(self.max_rate, 6),
        }


@dataclass
class Incident:
    """An open anomaly; used to suppress duplicate alerts."""

    incident_id: str
    severity: Severity
    started_at: float
    alert_count: int = 1
    peak_rate: float = 0.0

    def escalate_to(self, severity: Severity) -> bool:
        if SEVERITY_RANK[severity.value] > SEVERITY_RANK[self.severity.value]:
            self.severity = severity
            self.alert_count += 1
            return True
        return False


class AnomalyDetector:
    """Sliding-window anomaly detector over a stream of :class:`LogEvent`."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.baseline = Baseline()
        self.trend: Deque[WindowStats] = deque(maxlen=max(1, settings.trend_points_kept))
        self._events: Deque[LogEvent] = deque()
        self._window_errors: List[LogEvent] = []
        self._last_evaluation = time.time()
        self._last_eval_stats: Optional[WindowStats] = None
        self._recover_count = 0
        self.incident: Optional[Incident] = None
        self._incident_seq = 0
        self._alert_seq = 0
        self.status = SystemStatus.STARTING
        self.current_threshold = 0.0
        self.warmup_remaining = settings.warmup_windows
        self.windows_evaluated = 0

    # ------------------------------------------------------------------
    def ingest(self, event: LogEvent) -> None:
        """Register a newly observed log event (kept for the sliding window)."""

        self._events.append(event)

    def prune(self, now_ts: Optional[float] = None) -> None:
        """Drop events that fell out of the sliding window."""

        now_ts = now_ts if now_ts is not None else time.time()
        cutoff = now_ts - self.settings.window_seconds * 2
        if len(self._events) > 1000:
            self._events = deque(e for e in self._events if e.timestamp >= cutoff)

    # ------------------------------------------------------------------
    def _window_stats(self, now_ts: float) -> WindowStats:
        start = now_ts - self.settings.window_seconds
        total = errors = warnings = 0
        errors_seen: List[LogEvent] = []
        for event in self._events:
            if event.timestamp < start:
                continue
            total += 1
            if event.is_error:
                errors += 1
                errors_seen.append(event)
            elif event.is_warning:
                warnings += 1
        stats = WindowStats(
            window_start=start,
            window_end=now_ts,
            total=total,
            errors=errors,
            warnings=warnings,
        )
        if total:
            stats.error_rate = errors / total
            stats.warning_rate = warnings / total
        self._window_errors = errors_seen
        return stats

    def due_for_evaluation(self, now_ts: Optional[float] = None) -> bool:
        now_ts = now_ts if now_ts is not None else time.time()
        return (now_ts - self._last_evaluation) >= self.settings.evaluate_every

    # ------------------------------------------------------------------
    def threshold_for(self, mean: float, std: float) -> float:
        """Threshold = mean + k*sigma, floored so it stays meaningful."""

        s = self.settings
        return max(mean + s.sigma_multiplier * std, s.min_error_rate, mean + 0.005)

    def classify_severity(self, error_rate: float, threshold: float) -> Tuple[Severity, float]:
        """Return (severity, overage). overage = multiples of threshold above it."""

        s = self.settings
        safe_threshold = max(threshold, 1e-6)
        overage = (error_rate - safe_threshold) / safe_threshold
        if error_rate >= s.severity_critical_rate or overage >= s.severity_critical_overage:
            severity = Severity.CRITICAL
        elif overage >= s.severity_high_overage:
            severity = Severity.HIGH
        elif overage >= s.severity_medium_overage:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW
        return severity, overage

    # ------------------------------------------------------------------
    def evaluate(self, now_ts: Optional[float] = None) -> List[Alert]:
        """Run one evaluation pass; return the alerts produced (0, 1 or 2)."""

        now_ts = now_ts if now_ts is not None else time.time()
        self._last_evaluation = now_ts
        self.windows_evaluated += 1
        self.prune(now_ts)

        stats = self._window_stats(now_ts)
        self._last_eval_stats = stats
        self.trend.append(stats)

        produced: List[Alert] = []
        mean, std = self.baseline.mean, self.baseline.std

        # ---- warm-up: learn what "normal" looks like --------------------
        if not self.baseline.ready:
            if stats.total > 0:
                self.baseline.add(stats.error_rate, self.settings.baseline_window)
                if self.warmup_remaining > 0:
                    self.warmup_remaining -= 1
            self.current_threshold = self.threshold_for(self.baseline.mean, self.baseline.std)
            self.status = SystemStatus.HEALTHY if self.warmup_remaining <= 0 else SystemStatus.STARTING
            return produced

        threshold = self.threshold_for(mean, std)
        self.current_threshold = threshold
        effective_std = max(std, 0.01)  # floor keeps the sigma score finite/meaningful
        deviation_sigma = (stats.error_rate - mean) / effective_std
        enough_samples = stats.total >= self.settings.min_samples
        breached = enough_samples and stats.error_rate > threshold

        if breached:
            self._recover_count = 0
        elif self.incident is not None:
            self._recover_count += 1

        # ---- open / escalate -------------------------------------------
        if breached:
            severity, overage = self.classify_severity(stats.error_rate, threshold)
            if self.incident is None:
                self._incident_seq += 1
                self.incident = Incident(
                    incident_id=f"inc-{self._incident_seq}-{int(stats.window_end)}",
                    severity=severity,
                    started_at=now_ts,
                    peak_rate=stats.error_rate,
                )
                self.status = SystemStatus.ANOMALY
                produced.append(
                    self._build_alert(stats, mean, std, threshold, deviation_sigma, overage, severity)
                )
            else:
                self.incident.peak_rate = max(self.incident.peak_rate, stats.error_rate)
                if self.incident.escalate_to(severity):
                    self.status = SystemStatus.ANOMALY
                    produced.append(
                        self._build_alert(
                            stats, mean, std, threshold, deviation_sigma, overage, severity
                        )
                    )
                else:
                    # same (or lower) severity: suppressed as a duplicate
                    self.status = SystemStatus.ANOMALY

        # ---- recovery ---------------------------------------------------
        elif self.incident is not None and self._recover_count >= self.settings.recovery_windows:
            produced.append(self._build_recovery(self.incident, stats, mean, std, threshold))
            self.incident = None
            self._recover_count = 0
            self.status = SystemStatus.HEALTHY

        # ---- healthy -----------------------------------------------------
        elif self.incident is None:
            self.status = SystemStatus.HEALTHY

        # Only normal windows update the baseline - an anomaly must never
        # become the new "normal".
        if not breached:
            self.baseline.add(stats.error_rate, self.settings.baseline_window)
        return produced

    # ------------------------------------------------------------------
    def _build_alert(
        self,
        stats: WindowStats,
        mean: float,
        std: float,
        threshold: float,
        deviation_sigma: float,
        overage: float,
        severity: Severity,
    ) -> Alert:
        assert self.incident is not None
        self._alert_seq += 1
        counter = Counter(signature(event.message) for event in self._window_errors)
        top = [{"message": msg, "count": count} for msg, count in counter.most_common(3)]
        sample = self._window_errors[-1].message if self._window_errors else ""
        reason = (
            f"Error rate {stats.error_rate:.1%} over the last {self.settings.window_seconds:.0f}s window "
            f"exceeds the alert threshold {threshold:.1%} "
            f"(baseline {mean:.1%} + {self.settings.sigma_multiplier:g}σ={self.settings.sigma_multiplier * std:.1%}). "
            f"Deviation {deviation_sigma:.1f}σ, {overage:.1f}× the threshold, "
            f"{stats.errors} errors in {stats.total} events."
        )
        return Alert(
            id=Alert.new_id(),
            timestamp=time.time(),
            severity=severity,
            status=AlertStatus.ACTIVE,
            error_rate=stats.error_rate,
            baseline_rate=mean,
            deviation=stats.error_rate - mean,
            deviation_sigma=deviation_sigma,
            threshold_rate=threshold,
            reason=reason,
            window_start=stats.window_start,
            window_end=stats.window_end,
            window_seconds=self.settings.window_seconds,
            sample_size=stats.total,
            error_count=stats.errors,
            message_sample=sample,
            top_error_messages=top,
            incident_id=self.incident.incident_id,
            sequence=self._alert_seq,
        )

    def _build_recovery(
        self, incident: Incident, stats: WindowStats, mean: float, std: float, threshold: float
    ) -> Alert:
        self._alert_seq += 1
        duration = stats.window_end - incident.started_at
        return Alert(
            id=Alert.new_id(),
            timestamp=time.time(),
            severity=incident.severity,
            status=AlertStatus.RECOVERED,
            error_rate=stats.error_rate,
            baseline_rate=mean,
            deviation=stats.error_rate - mean,
            deviation_sigma=(stats.error_rate - mean) / max(std, 0.01),
            threshold_rate=threshold,
            reason=(
                f"Incident {incident.incident_id} recovered after {duration:.0f}s: error rate fell to "
                f"{stats.error_rate:.1%} (baseline {mean:.1%}, threshold {threshold:.1%}). "
                f"Peak error rate during the incident: {incident.peak_rate:.1%}."
            ),
            window_start=stats.window_start,
            window_end=stats.window_end,
            window_seconds=self.settings.window_seconds,
            sample_size=stats.total,
            error_count=stats.errors,
            message_sample="",
            top_error_messages=[],
            incident_id=incident.incident_id,
            sequence=self._alert_seq,
        )

    # ------------------------------------------------------------------
    @property
    def active_alert(self) -> Optional[Alert]:
        return None

    @property
    def current_window(self) -> Optional[WindowStats]:
        return self._last_eval_stats or (self.trend[-1] if self.trend else None)

    @property
    def trend_points(self) -> List[WindowStats]:
        return list(self.trend)

    def snapshot(self) -> Tuple[float, float, float, float, bool]:
        """Return (error_rate, baseline_mean, baseline_std, threshold, ready)."""

        stats = self.current_window
        rate = stats.error_rate if stats else 0.0
        threshold = self.current_threshold or self.threshold_for(self.baseline.mean, self.baseline.std)
        return rate, self.baseline.mean, self.baseline.std, threshold, self.baseline.ready
