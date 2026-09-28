"""Shared pytest fixtures."""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Keep tests hermetic: point everything at an isolated data dir *before* the
# settings singleton is created.
_TMP = BACKEND_DIR / "data" / ".pytest"
_TMP.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("LOG_FILE", str(_TMP / "test.log"))
os.environ.setdefault("ALERT_LOG_FILE", str(_TMP / "alerts.log"))
os.environ.setdefault("ALERT_EXPORT_FILE", str(_TMP / "alerts.json"))
os.environ["NOTIFY_PROVIDER"] = "local"
os.environ["DETECT_WINDOW_SECONDS"] = "10"
os.environ["DETECT_EVALUATE_EVERY"] = "1"
os.environ["DETECT_MIN_SAMPLES"] = "5"
os.environ["DETECT_WARMUP_WINDOWS"] = "1"
os.environ["DETECT_RECOVERY_WINDOWS"] = "1"

#: Fields the ``settings`` fixture overrides.
_OVERRIDES = (
    "log_file",
    "alert_log_file",
    "alert_export_file",
    "poll_interval",
    "evaluate_every",
    "window_seconds",
    "min_samples",
    "warmup_windows",
    "recovery_windows",
    "baseline_window",
    "min_error_rate",
    "sigma_multiplier",
    "severity_medium_overage",
    "severity_high_overage",
    "severity_critical_overage",
    "severity_critical_rate",
    "notify_provider",
    "cloudwatch_log_group",
    "sns_topic_arn",
    "aws_region",
)


@pytest.fixture()
def settings(tmp_path):
    """Point the *shared* settings singleton at an isolated temp directory.

    The app module and the tests must use the same ``Settings`` instance,
    otherwise mutations here would not reach the running service.
    """

    from app.config import get_settings

    cfg = get_settings()
    saved = {name: copy.copy(getattr(cfg, name)) for name in _OVERRIDES}

    cfg.log_file = tmp_path / "app.log"
    cfg.alert_log_file = tmp_path / "alerts.log"
    cfg.alert_export_file = tmp_path / "alerts.json"
    cfg.poll_interval = 0.02
    cfg.evaluate_every = 0.5
    cfg.window_seconds = 10.0
    cfg.min_samples = 5
    cfg.warmup_windows = 1
    cfg.recovery_windows = 1
    cfg.baseline_window = 10
    cfg.min_error_rate = 0.05
    cfg.sigma_multiplier = 3.0
    cfg.severity_medium_overage = 2.0
    cfg.severity_high_overage = 5.0
    cfg.severity_critical_overage = 10.0
    cfg.severity_critical_rate = 0.5
    cfg.notify_provider = "local"
    cfg.cloudwatch_log_group = None
    cfg.sns_topic_arn = None
    cfg.aws_region = None
    cfg.ensure_directories()

    # Rebuild the service so its tailer points at the new log file.
    from app import main
    from app.service import LogMonitorService

    main.settings = cfg
    main.service = LogMonitorService(cfg)

    try:
        yield cfg
    finally:
        for name, value in saved.items():
            setattr(cfg, name, value)


@pytest.fixture()
def client(settings):
    from fastapi.testclient import TestClient

    from app import main

    with TestClient(main.app) as test_client:
        yield test_client
