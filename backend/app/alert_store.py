"""In-memory alert store with a bounded deque."""

from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List, Optional

from .models import Alert, AlertStatus


class AlertStore:
    """Keeps the most recent alerts in memory (no DB needed for the MVP)."""

    def __init__(self, max_alerts: int = 500) -> None:
        self._alerts: Deque[Alert] = deque(maxlen=max_alerts)
        self._by_id: Dict[str, Alert] = {}
        self._total = 0
        self._active: Dict[str, int] = {}  # incident_id -> open alerts
        self._max_alerts = max_alerts

    def add(self, alert: Alert) -> None:
        self._alerts.append(alert)
        self._by_id[alert.id] = alert
        self._total += 1
        if alert.status is AlertStatus.ACTIVE:
            self._active[alert.incident_id] = self._active.get(alert.incident_id, 0) + 1
        else:
            remaining = self._active.get(alert.incident_id, 0) - 1
            if remaining <= 0:
                self._active.pop(alert.incident_id, None)
            else:
                self._active[alert.incident_id] = remaining

    # ------------------------------------------------------------------
    @property
    def total(self) -> int:
        return self._total

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def latest(self) -> Optional[Alert]:
        return self._alerts[-1] if self._alerts else None

    def list(
        self,
        limit: int = 100,
        offset: int = 0,
        severity: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Alert]:
        items = list(self._alerts)
        if severity:
            items = [a for a in items if a.severity.value == severity.upper()]
        if status:
            items = [a for a in items if a.status.value == status.upper()]
        items.reverse()  # newest first
        return items[offset : offset + limit] if limit else items[offset:]

    def get(self, alert_id: str) -> Optional[Alert]:
        return self._by_id.get(alert_id)

    def counts_by_severity(self) -> Dict[str, int]:
        counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
        for alert in self._alerts:
            counts[alert.severity.value] = counts.get(alert.severity.value, 0) + 1
        return counts
