"""Tests for the anomaly detector: baseline, deviation, severity, dedup."""

from __future__ import annotations

import time
from pathlib import Path
from typing import List

import pytest

from app.detector import AnomalyDetector
from app.models import Alert, AlertStatus, LogEvent, LogLevel, Severity, SystemStatus

BASE_TIME = 1_700_000_000.0


def make_event(level: str = "INFO", offset: float = 0.0, message: str = "ok") -> LogEvent:
    return LogEvent(
        timestamp=BASE_TIME - offset,
        level=LogLevel(level),
        message=message,
        raw=f"line {message}",
    )


class Scenario:
    """Drives a detector with a virtual clock.

    Each :meth:`step` replaces the contents of the sliding window, which is
    what really happens in a rolling window over a live log stream.
    """

    def __init__(self, detector: AnomalyDetector) -> None:
        self.detector = detector
        self.clock = BASE_TIME

    @property
    def _stride(self) -> float:
        # Advance slightly more than one window so the previous window's
        # events fall out - each step fully replaces the window contents.
        return self.detector.settings.window_seconds + 1.0

    def step(self, errors: int, total: int, message: str = "evt") -> List[Alert]:
        """Advance one window containing ``errors`` errors out of ``total``."""

        self.clock += self._stride
        for index in range(total):
            level = "ERROR" if index < errors else "INFO"
            self.detector.ingest(
                LogEvent(
                    timestamp=self.clock,
                    level=LogLevel(level),
                    message=f"{message}-{index}",
                    raw=f"line {message}-{index}",
                )
            )
        return self.detector.evaluate(self.clock)

    def step_aged(self, errors: int, total: int, age: float) -> List[Alert]:
        """Same as :meth:`step` but the events are ``age`` seconds old."""

        self.clock += self._stride
        for index in range(total):
            level = "ERROR" if index < errors else "INFO"
            self.detector.ingest(
                LogEvent(
                    timestamp=self.clock - age,
                    level=LogLevel(level),
                    message=f"old-{index}",
                    raw=f"line old-{index}",
                )
            )
        return self.detector.evaluate(self.clock)


@pytest.fixture()
def scenario(settings):
    detector = AnomalyDetector(settings)
    return detector, Scenario(detector)


def warm(detector: AnomalyDetector, scenario: Scenario, windows: int = 4) -> None:
    """Run a few normal windows so the baseline is established."""

    for _ in range(windows):
        scenario.step(errors=1, total=50)


class TestBaseline:
    def test_baseline_learns_normal_rate(self, scenario):
        detector, sim = scenario
        assert detector.baseline.ready is False
        warm(detector, sim, windows=3)
        assert detector.baseline.ready is True
        assert detector.baseline.mean == pytest.approx(0.02, abs=0.02)
        assert detector.baseline.std >= 0.0

    def test_no_alerts_while_normal(self, scenario):
        detector, sim = scenario
        alerts: List[Alert] = []
        for _ in range(6):
            alerts.extend(sim.step(errors=1, total=50))
        assert alerts == []
        assert detector.status is SystemStatus.HEALTHY

    def test_threshold_is_mean_plus_k_sigma(self, scenario):
        detector, sim = scenario
        warm(detector, sim, windows=4)
        mean, std = detector.baseline.mean, detector.baseline.std
        expected = max(mean + settings_k(detector) * std, detector.settings.min_error_rate)
        assert detector.threshold_for(mean, std) == pytest.approx(expected)


def settings_k(detector: AnomalyDetector) -> float:
    return detector.settings.sigma_multiplier


class TestAnomalyDetection:
    def test_error_burst_raises_alert(self, scenario):
        detector, sim = scenario
        warm(detector, sim, windows=4)
        baseline_mean = detector.baseline.mean

        alerts = sim.step(errors=40, total=50)  # 80% errors
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.status is AlertStatus.ACTIVE
        assert alert.error_rate == pytest.approx(0.8)
        assert alert.baseline_rate == pytest.approx(baseline_mean)
        assert alert.deviation > 0
        assert alert.deviation_sigma > detector.settings.sigma_multiplier
        assert alert.threshold_rate > baseline_mean
        assert alert.severity is Severity.CRITICAL
        assert alert.error_count == 40
        assert alert.sample_size == 50
        assert "exceeds the alert threshold" in alert.reason
        assert detector.status is SystemStatus.ANOMALY

    def test_alert_has_all_required_fields(self, scenario):
        detector, sim = scenario
        warm(detector, sim, windows=4)
        alert = sim.step(errors=40, total=50)[0]
        payload = alert.to_dict()
        for key in (
            "id",
            "timestamp",
            "severity",
            "errorRate",
            "baselineRate",
            "deviation",
            "reason",
            "windowStart",
            "windowEnd",
            "status",
        ):
            assert key in payload
        assert alert.id.startswith("alert-")
        assert alert.window_end - alert.window_start == pytest.approx(detector.settings.window_seconds, abs=1)

    def test_top_error_messages_are_grouped(self, scenario):
        detector, sim = scenario
        warm(detector, sim, windows=4)
        sim.clock += detector.settings.window_seconds
        for index in range(30):
            detector.ingest(
                LogEvent(
                    timestamp=sim.clock,
                    level=LogLevel.ERROR,
                    message=f"connection refused host=db-{index} port=5432",
                    raw="",
                )
            )
        for index in range(20):
            detector.ingest(
                LogEvent(
                    timestamp=sim.clock,
                    level=LogLevel.INFO,
                    message="ok",
                    raw="",
                )
            )
        alert = detector.evaluate(sim.clock)[0]
        assert alert.top_error_messages
        assert alert.top_error_messages[0]["count"] == 30
        assert "connection refused" in alert.top_error_messages[0]["message"]

    def test_small_fluctuation_does_not_alert(self, scenario):
        detector, sim = scenario
        warm(detector, sim, windows=5)
        alerts = sim.step(errors=3, total=50)  # 6% - just above the 5% floor
        for alert in alerts:
            assert alert.severity in (Severity.LOW, Severity.MEDIUM)

    def test_min_samples_blocks_tiny_windows(self, settings, scenario):
        detector, sim = scenario
        settings.min_samples = 50
        warm(detector, sim, windows=4)
        assert sim.step(errors=5, total=6) == []

    def test_window_ignores_events_outside_it(self, settings, scenario):
        detector, sim = scenario
        settings.window_seconds = 5.0
        warm(detector, sim, windows=4)
        # 100 errors that are 1 hour old must be ignored
        assert sim.step_aged(errors=100, total=100, age=3600) == []
        stats = detector.current_window
        assert stats.errors == 0
        assert stats.error_rate == 0.0


class TestSeverity:
    @pytest.mark.parametrize(
        "errors,total,expected",
        [
            (8, 100, Severity.LOW),         # 8%  vs 5% threshold -> 0.6x overage
            (20, 100, Severity.MEDIUM),     # 20% -> 3x
            (40, 100, Severity.HIGH),       # 40% -> 7x
            (90, 100, Severity.CRITICAL),   # 90% -> 17x and >= critical rate
        ],
    )
    def test_severity_levels(self, scenario, errors, total, expected):
        detector, sim = scenario
        warm(detector, sim, windows=4)
        alerts = sim.step(errors=errors, total=total)
        assert alerts, "expected an alert"
        assert alerts[0].severity is expected

    def test_severity_escalates_within_one_incident(self, scenario):
        detector, sim = scenario
        warm(detector, sim, windows=4)
        first = sim.step(errors=8, total=100)
        assert first[0].severity is Severity.LOW
        incident = first[0].incident_id

        second = sim.step(errors=60, total=100)
        assert len(second) == 1
        assert second[0].severity is Severity.CRITICAL
        assert second[0].incident_id == incident

    def test_repeated_low_severity_is_suppressed(self, settings, scenario):
        detector, sim = scenario
        settings.min_error_rate = 0.01
        settings.severity_medium_overage = 100.0
        settings.severity_high_overage = 1000.0
        settings.severity_critical_overage = 10000.0
        settings.severity_critical_rate = 0.99
        warm(detector, sim, windows=4)
        first = sim.step(errors=5, total=100)
        assert first[0].severity is Severity.LOW
        assert sim.step(errors=6, total=100) == []  # same severity -> duplicate


class TestDeduplication:
    def test_no_duplicate_alerts_while_anomaly_persists(self, scenario):
        detector, sim = scenario
        warm(detector, sim, windows=4)
        assert len(sim.step(errors=90, total=100)) == 1
        for _ in range(5):
            assert sim.step(errors=90, total=100) == []

    def test_single_recovery_alert_closes_incident(self, scenario):
        detector, sim = scenario
        warm(detector, sim, windows=4)
        active = sim.step(errors=90, total=100)[0]

        alerts: List[Alert] = []
        for _ in range(detector.settings.recovery_windows):
            alerts.extend(sim.step(errors=1, total=100))
        assert len(alerts) == 1
        assert alerts[0].status is AlertStatus.RECOVERED
        assert alerts[0].incident_id == active.incident_id
        assert "recovered" in alerts[0].reason.lower()
        assert detector.status is SystemStatus.HEALTHY

        assert sim.step(errors=1, total=100) == []  # no duplicate recovery

    def test_new_incident_after_recovery(self, scenario):
        detector, sim = scenario
        warm(detector, sim, windows=4)
        first = sim.step(errors=90, total=100)[0]
        for _ in range(detector.settings.recovery_windows):
            sim.step(errors=1, total=100)
        second = sim.step(errors=90, total=100)[0]
        assert second.incident_id != first.incident_id
        assert second.id != first.id


class TestBaselineHygiene:
    def test_anomalous_windows_do_not_pollute_baseline(self, scenario):
        detector, sim = scenario
        warm(detector, sim, windows=6)
        mean_before = detector.baseline.mean
        for _ in range(4):
            sim.step(errors=90, total=100)
        assert detector.baseline.mean == pytest.approx(mean_before, abs=0.02)

    def test_trend_is_capped(self, settings):
        settings.trend_points_kept = 5
        detector = AnomalyDetector(settings)
        sim = Scenario(detector)
        for _ in range(20):
            sim.step(errors=1, total=10)
        assert len(detector.trend) == 5
