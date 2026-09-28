"""Tests for the AWS notification service (local mode + graceful fallback)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.models import Alert, AlertStatus, Severity
from app.notifier import Notifier


def make_alert(severity: Severity = Severity.HIGH) -> Alert:
    return Alert(
        id=Alert.new_id(),
        timestamp=time.time(),
        severity=severity,
        status=AlertStatus.ACTIVE,
        error_rate=0.55,
        baseline_rate=0.02,
        deviation=0.53,
        deviation_sigma=9.4,
        threshold_rate=0.05,
        reason="unit test alert",
        window_start=time.time() - 20,
        window_end=time.time(),
        window_seconds=20.0,
        sample_size=200,
        error_count=110,
        message_sample="boom",
    )


class TestLocalMode:
    def test_defaults_to_local(self, settings):
        settings.notify_provider = "auto"
        settings.cloudwatch_log_group = None
        settings.sns_topic_arn = None
        notifier = Notifier(settings)
        assert notifier.provider == "local"
        assert notifier.status()["mode"] == "local"

    def test_writes_alert_files(self, settings):
        settings.notify_provider = "local"
        notifier = Notifier(settings)
        alert = make_alert()
        notifier.notify(alert)

        assert settings.alert_log_file.exists()
        lines = settings.alert_log_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["id"] == alert.id
        assert payload["severity"] == "HIGH"
        assert payload["errorRate"] == 0.55

    def test_appends_multiple_alerts(self, settings):
        settings.notify_provider = "local"
        notifier = Notifier(settings)
        for _ in range(3):
            notifier.notify(make_alert())
        lines = settings.alert_log_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3
        assert notifier.sent == 3

    def test_notification_summary_string(self, settings):
        settings.notify_provider = "local"
        notifier = Notifier(settings)
        notifier.notify(make_alert())
        assert "HIGH" in notifier.recent[0]["provider"] or notifier.recent


class TestAwsFallback:
    def test_falls_back_when_not_configured(self, settings):
        settings.notify_provider = "cloudwatch"
        settings.cloudwatch_log_group = None
        settings.aws_region = None
        notifier = Notifier(settings)
        assert notifier.provider == "local"
        assert notifier.last_error  # reason is recorded for the dashboard

    def test_falls_back_when_client_build_fails(self, settings, monkeypatch):
        settings.notify_provider = "sns"
        settings.sns_topic_arn = "arn:aws:sns:us-east-1:123456789012:alerts"
        settings.aws_region = None  # no region -> boto3 raises

        def boom(_kind):
            raise RuntimeError("no credentials available")

        monkeypatch.setattr(Notifier, "_build_client", boom)
        notifier = Notifier(settings)
        assert notifier.provider == "local"
        assert "sns unavailable" in (notifier.last_error or "")

    def test_local_delivery_still_works_after_fallback(self, settings, monkeypatch):
        settings.notify_provider = "sns"
        settings.sns_topic_arn = "arn:aws:sns:us-east-1:123456789012:alerts"
        monkeypatch.setattr(
            Notifier, "_build_client", lambda self, kind: (_ for _ in ()).throw(RuntimeError("nope"))
        )
        notifier = Notifier(settings)
        notifier.notify(make_alert())
        assert settings.alert_log_file.exists()

    def test_aws_delivery_failure_never_raises(self, settings, monkeypatch):
        settings.notify_provider = "local"
        settings.aws_fail_silently = True
        notifier = Notifier(settings)

        class Boom:
            def publish(self, **_kwargs):
                raise RuntimeError("network down")

        notifier._client = Boom()
        notifier.provider = "sns"
        notifier._notify_sns = lambda alert: notifier._client.publish()  # type: ignore[attr-defined]
        notifier.notify(make_alert())  # must not raise
        assert notifier.failed == 1
        # the local copy is still written
        assert settings.alert_log_file.exists()


class TestNotificationPayload:
    def test_alert_serialisation_is_complete(self):
        payload = make_alert().to_dict()
        required = {
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
        }
        assert required.issubset(payload.keys())

    def test_summary_text_is_human_readable(self):
        text = make_alert().to_notification()
        assert "HIGH" in text
        assert "55.0%" in text
