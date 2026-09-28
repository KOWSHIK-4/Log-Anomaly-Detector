"""Alert notification service.

Providers
---------
``local``  : append alerts to a local file (always on, safe default)
``cloudwatch`` : put_log_events to AWS CloudWatch Logs
``sns``    : publish to an AWS SNS topic
``auto``   : use CloudWatch/SNS when configured *and* credentials resolve,
             otherwise silently degrade to ``local``

Credentials are never hard-coded: boto3 resolves them from the standard
chain (env vars, ~/.aws/credentials, IAM role, instance profile).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Settings
from .models import Alert

logger = logging.getLogger("anomaly.notifier")


class Notifier:
    """Fan-out notifier with graceful degradation."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.provider = "local"
        self.enabled = True
        self.sent = 0
        self.failed = 0
        self.last_error: Optional[str] = None
        self.last_sent_at: Optional[float] = None
        self.recent: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._client_kind: Optional[str] = None
        self._client = None
        self._sequence_token: Optional[str] = None
        self._resolve_provider()

    # ------------------------------------------------------------------
    def _resolve_provider(self) -> None:
        requested = (self.settings.notify_provider or "auto").strip().lower()
        cloudwatch_ready = bool(self.settings.cloudwatch_log_group)
        sns_ready = bool(self.settings.sns_topic_arn)

        if requested == "local":
            self.provider = "local"
            return

        target = requested
        if requested == "auto":
            target = "cloudwatch" if cloudwatch_ready else ("sns" if sns_ready else "local")

        if target == "local":
            self.provider = "local"
            return

        try:
            self._client = self._build_client(target)
        except Exception as exc:  # credentials missing / region missing / no boto3
            self.last_error = f"{target} unavailable: {exc}"
            logger.warning("Notifier falling back to local mode: %s", self.last_error)
            self.provider = "local"
            return

        self._client_kind = target
        self.provider = target
        if target == "cloudwatch":
            self._ensure_cloudwatch_stream()

    def _build_client(self, kind: str):
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("boto3 is not installed (pip install -r requirements.txt)") from exc

        session_kwargs: Dict[str, Any] = {}
        if self.settings.aws_profile:
            session_kwargs["profile_name"] = self.settings.aws_profile
        session = boto3.session.Session(**session_kwargs)
        region = self.settings.aws_region or session.region_name
        if not region:
            raise RuntimeError("AWS_REGION / AWS_DEFAULT_REGION is not set")

        if kind == "cloudwatch":
            return session.client("logs", region_name=region)
        return session.client("sns", region_name=region)

    def _ensure_cloudwatch_stream(self) -> None:
        """Create the log group/stream if missing and fetch the sequence token."""

        group = self.settings.cloudwatch_log_group
        stream = self.settings.cloudwatch_log_stream or "anomaly-detector"
        assert self._client is not None and group
        try:
            self._client.create_log_group(logGroupName=group)
        except Exception as exc:
            if "ResourceAlreadyExistsException" not in str(exc):
                logger.debug("create_log_group: %s", exc)
        try:
            self._client.create_log_stream(logGroupName=group, logStreamName=stream)
        except Exception as exc:
            if "ResourceAlreadyExistsException" not in str(exc):
                logger.debug("create_log_stream: %s", exc)
        try:
            resp = self._client.describe_log_streams(
                logGroupName=group, logStreamName=stream, orderBy="LastEventTime", limit=1
            )
            streams = resp.get("logStreams") or []
            if streams:
                self._sequence_token = streams[0].get("uploadSequenceToken")
        except Exception as exc:  # pragma: no cover - network
            self.last_error = str(exc)

    # ------------------------------------------------------------------
    def notify(self, alert: Alert) -> None:
        """Send one alert. Never raises."""
        with self._lock:
            self.sent += 1
            self.recent.insert(0, {"id": alert.id, "provider": self.provider, "at": time.time()})
            del self.recent[10:]
        try:
            if self.provider == "local":
                self._notify_local(alert)
            elif self.provider == "cloudwatch":
                self._notify_cloudwatch(alert)
            elif self.provider == "sns":
                self._notify_sns(alert)
            self.last_sent_at = time.time()
        except Exception as exc:
            with self._lock:
                self.failed += 1
                self.last_error = f"{self.provider}: {exc}"
            logger.warning("Failed to deliver alert %s -> %s", alert.id, self.last_error)
            if not self.settings.aws_fail_silently:
                raise
            if self.provider in {"cloudwatch", "sns"}:
                # Deliveries must never be lost: keep the local copy.
                try:
                    self._notify_local(alert)
                except Exception:
                    pass

    def _notify_local(self, alert: Alert) -> None:
        self._append_jsonl(self.settings.alert_log_file, alert.to_dict())
        self._append_jsonl(self.settings.alert_export_file, alert.to_dict())

    def _append_jsonl(self, path: Path, payload: Dict[str, Any]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload) + "\n")
        except OSError as exc:  # pragma: no cover
            logger.warning("cannot write alert to %s: %s", path, exc)

    def _notify_cloudwatch(self, alert: Alert) -> None:
        assert self._client is not None
        group = self.settings.cloudwatch_log_group
        stream = self.settings.cloudwatch_log_stream or "anomaly-detector"
        kwargs: Dict[str, Any] = {
            "logGroupName": group,
            "logStreamName": stream,
            "logEvents": [
                {
                    "timestamp": int(alert.timestamp * 1000),
                    "message": json.dumps(alert.to_dict()),
                }
            ],
        }
        if self._sequence_token:
            kwargs["sequenceToken"] = self._sequence_token
        resp = self._client.put_log_events(**kwargs)
        self._sequence_token = resp.get("nextSequenceToken")

    def _notify_sns(self, alert: Alert) -> None:
        assert self._client is not None
        # MessageStructure=json requires a "default" key in the message body.
        body = json.dumps({"default": alert.to_notification(), "alert": alert.to_dict()})
        self._client.publish(
            TopicArn=self.settings.sns_topic_arn,
            Message=body,
            Subject=f"[{alert.severity.value}] Log anomaly {alert.id}"[:100],
            MessageStructure="json",
        )

    # ------------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "requested": self.settings.notify_provider,
            "enabled": self.enabled,
            "mode": "aws" if self.provider in {"cloudwatch", "sns"} else "local",
            "cloudwatchLogGroup": self.settings.cloudwatch_log_group if self.provider == "cloudwatch" else None,
            "cloudwatchLogStream": self.settings.cloudwatch_log_stream if self.provider == "cloudwatch" else None,
            "snsTopicArn": self.settings.sns_topic_arn if self.provider == "sns" else None,
            "awsRegion": self.settings.aws_region,
            "sent": self.sent,
            "failed": self.failed,
            "lastError": self.last_error,
            "lastSentAt": self.last_sent_at,
            "localLogFile": str(self.settings.alert_log_file),
        }
