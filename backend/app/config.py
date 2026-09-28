"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:  # optional dependency, keeps the app working without python-dotenv
    from dotenv import load_dotenv

    _ROOT = Path(__file__).resolve().parents[2]
    load_dotenv(_ROOT / ".env")
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:  # pragma: no cover - dotenv is optional
    pass


BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LOG_FILE = BACKEND_DIR / "data" / "demo.log"
DEFAULT_ALERT_LOG = BACKEND_DIR / "data" / "alerts" / "alerts.log"
DEFAULT_ALERT_EXPORT = BACKEND_DIR / "data" / "alerts" / "alerts.json"


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """Runtime settings. Every field can be overridden with an env var."""

    # --- log monitoring -------------------------------------------------
    log_file: Path = field(default_factory=lambda: Path(_env_str("LOG_FILE", str(DEFAULT_LOG_FILE))))
    poll_interval: float = field(default_factory=lambda: _env_float("LOG_POLL_INTERVAL", 0.2))
    max_line_bytes: int = field(default_factory=lambda: _env_int("LOG_MAX_LINE_BYTES", 8192))
    recent_errors_kept: int = field(default_factory=lambda: _env_int("RECENT_ERRORS_KEPT", 100))
    trend_points_kept: int = field(default_factory=lambda: _env_int("TREND_POINTS_KEPT", 240))

    # --- detection ------------------------------------------------------
    window_seconds: float = field(default_factory=lambda: _env_float("DETECT_WINDOW_SECONDS", 20.0))
    evaluate_every: float = field(default_factory=lambda: _env_float("DETECT_EVALUATE_EVERY", 2.0))
    min_samples: int = field(default_factory=lambda: _env_int("DETECT_MIN_SAMPLES", 20))
    warmup_windows: int = field(default_factory=lambda: _env_int("DETECT_WARMUP_WINDOWS", 2))
    baseline_window: int = field(default_factory=lambda: _env_int("DETECT_BASELINE_WINDOW", 15))
    sigma_multiplier: float = field(default_factory=lambda: _env_float("DETECT_SIGMA_MULTIPLIER", 3.0))
    min_error_rate: float = field(default_factory=lambda: _env_float("DETECT_MIN_ERROR_RATE", 0.05))
    recovery_windows: int = field(default_factory=lambda: _env_int("DETECT_RECOVERY_WINDOWS", 2))

    # --- severity -------------------------------------------------------
    # "overage" = (error_rate - threshold) / threshold
    severity_medium_overage: float = field(
        default_factory=lambda: _env_float("SEVERITY_MEDIUM_OVERAGE", 2.0)
    )
    severity_high_overage: float = field(default_factory=lambda: _env_float("SEVERITY_HIGH_OVERAGE", 5.0))
    severity_critical_overage: float = field(
        default_factory=lambda: _env_float("SEVERITY_CRITICAL_OVERAGE", 10.0)
    )
    severity_critical_rate: float = field(default_factory=lambda: _env_float("SEVERITY_CRITICAL_RATE", 0.5))

    # --- api ------------------------------------------------------------
    host: str = field(default_factory=lambda: _env_str("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))
    cors_origins: str = field(default_factory=lambda: _env_str("CORS_ORIGINS", "*"))
    max_alerts_in_memory: int = field(default_factory=lambda: _env_int("MAX_ALERTS_IN_MEMORY", 500))

    # --- notification ---------------------------------------------------
    notify_provider: str = field(default_factory=lambda: _env_str("NOTIFY_PROVIDER", "auto"))
    alert_log_file: Path = field(default_factory=lambda: Path(_env_str("ALERT_LOG_FILE", str(DEFAULT_ALERT_LOG))))
    alert_export_file: Path = field(
        default_factory=lambda: Path(_env_str("ALERT_EXPORT_FILE", str(DEFAULT_ALERT_EXPORT)))
    )

    # --- aws ------------------------------------------------------------
    aws_region: Optional[str] = field(default_factory=lambda: os.getenv("AWS_REGION"))
    aws_profile: Optional[str] = field(default_factory=lambda: os.getenv("AWS_PROFILE"))
    cloudwatch_log_group: Optional[str] = field(default_factory=lambda: os.getenv("CLOUDWATCH_LOG_GROUP"))
    cloudwatch_log_stream: Optional[str] = field(default_factory=lambda: os.getenv("CLOUDWATCH_LOG_STREAM"))
    sns_topic_arn: Optional[str] = field(default_factory=lambda: os.getenv("SNS_TOPIC_ARN"))
    aws_fail_silently: bool = field(default_factory=lambda: _env_bool("AWS_FAIL_SILENTLY", True))

    @property
    def cors_origin_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        return [item.strip() for item in raw.split(",") if item.strip()]

    def ensure_directories(self) -> None:
        for directory in (self.log_file.parent, self.alert_log_file.parent, self.alert_export_file.parent):
            directory.mkdir(parents=True, exist_ok=True)
        if not self.log_file.exists():
            self.log_file.touch()


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Return the process-wide settings singleton."""

    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_directories()
    return _settings


def reset_settings() -> None:
    """Used by tests to force a reload of the environment."""

    global _settings
    _settings = None
