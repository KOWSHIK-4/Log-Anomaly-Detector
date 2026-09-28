"""The background monitor: tails the log file, runs the detector and
publishes events to connected WebSocket clients.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set

from .alert_store import AlertStore
from .config import Settings, get_settings
from .detector import AnomalyDetector
from .models import Alert, LogEvent, Metrics, SystemStatus, WindowStats
from .notifier import Notifier
from .tailer import LogTailer

logger = logging.getLogger("anomaly.monitor")


class ConnectionManager:
    """Tracks WebSocket clients and broadcasts JSON payloads.

    The monitor runs in its own thread, so broadcasts are scheduled onto the
    API event loop with ``run_coroutine_threadsafe`` - sending on a websocket
    from a foreign thread is not safe.
    """

    def __init__(self) -> None:
        self._clients: Set[Any] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def count(self) -> int:
        return len(self._clients)

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the API event loop (called once during startup)."""

        self._loop = loop

    async def connect(self, websocket: Any) -> None:
        await websocket.accept()
        self._clients.add(websocket)

    async def disconnect(self, websocket: Any) -> None:
        self._clients.discard(websocket)

    def broadcast_threadsafe(self, message: Dict[str, Any]) -> None:
        """Fire-and-forget broadcast from any thread."""

        loop = self._loop
        if loop is None or loop.is_closed() or not self._clients:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast(message), loop)
        except RuntimeError:  # loop already shutting down
            pass

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """Broadcast from the API loop itself."""

        if not self._clients:
            return
        await self._broadcast(message)

    async def _broadcast(self, message: Dict[str, Any]) -> None:
        dead = []
        for client in list(self._clients):
            try:
                await client.send_json(message)
            except Exception:
                dead.append(client)
        for client in dead:
            self._clients.discard(client)


class LogMonitorService:
    """Owns the tailer thread, detector, alert store and notifier."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.detector = AnomalyDetector(self.settings)
        self.alerts = AlertStore(self.settings.max_alerts_in_memory)
        self.notifier = Notifier(self.settings)
        self.tailer = LogTailer(
            self.settings.log_file,
            poll_interval=self.settings.poll_interval,
            max_line_bytes=self.settings.max_line_bytes,
            on_error=self._log_tailer_error,
        )
        self.manager = ConnectionManager()
        self.started_at = time.time()
        self.total_logs = 0
        self.total_errors = 0
        self.total_warnings = 0
        self.malformed_lines = 0
        self._recent_errors: Deque[LogEvent] = deque(maxlen=self.settings.recent_errors_kept)
        self._event_times: Deque[float] = deque(maxlen=5000)
        self._last_ingest_at = time.time()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._errors: List[str] = []

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._run, name="log-monitor", daemon=True)
        self._thread.start()
        logger.info("Monitor started on %s", self.settings.log_file)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    def _run(self) -> None:
        try:
            asyncio.run(self._async_run())
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("monitor loop crashed: %s", exc)
            self._errors.append(str(exc))

    async def _async_run(self) -> None:
        self._loop = asyncio.get_running_loop()
        metrics_interval = 1.0
        last_metrics_push = 0.0
        while not self._stop.is_set():
            try:
                events = self.tailer.read_new_events()
                for event in events:
                    self._record(event)
                    self.detector.ingest(event)

                now = time.time()
                if self.detector.due_for_evaluation(now):
                    for alert in self.detector.evaluate(now):
                        self.alerts.add(alert)
                        await self._dispatch_alert(alert)

                if now - last_metrics_push >= metrics_interval:
                    last_metrics_push = now
                    self._broadcast_metrics()
            except Exception as exc:  # keep the loop alive on any error
                logger.exception("monitor iteration failed: %s", exc)
                self._errors.append(str(exc))
            await asyncio.sleep(self.settings.poll_interval)

    def _record(self, event: LogEvent) -> None:
        self.total_logs += 1
        self._event_times.append(event.timestamp)
        self._last_ingest_at = time.time()
        if event.is_error:
            self.total_errors += 1
            self._recent_errors.append(event)
        elif event.is_warning:
            self.total_warnings += 1
        if not event.parse_ok and event.parse_error and "level" in event.parse_error:
            self.malformed_lines += 1

    # ------------------------------------------------------------------
    async def _dispatch_alert(self, alert: Alert) -> None:
        self.manager.broadcast_threadsafe({"type": "alert", "data": alert.to_dict()})
        try:
            self.notifier.notify(alert)
        except Exception as exc:  # pragma: no cover - notifier already guards
            logger.warning("notifier error: %s", exc)
        self._broadcast_metrics()

    def _broadcast_metrics(self) -> None:
        if self.manager.count == 0:
            return
        self.manager.broadcast_threadsafe({"type": "metrics", "data": self.build_metrics().to_dict()})

    def _log_tailer_error(self, message: str) -> None:
        logger.warning("tailer: %s", message)
        self._errors.append(message)
        del self._errors[:-20]

    # ------------------------------------------------------------------
    def lines_per_minute(self) -> float:
        cutoff = time.time() - 60.0
        recent = [ts for ts in self._event_times if ts >= cutoff]
        return len(recent)

    def build_metrics(self) -> Metrics:
        rate, baseline_mean, baseline_std, threshold, ready = self.detector.snapshot()
        current = self.detector.current_window or WindowStats(time.time() - self.settings.window_seconds, time.time())
        status = self.detector.status
        if status is SystemStatus.STARTING and self.total_logs:
            status = SystemStatus.HEALTHY
        return Metrics(
            status=status,
            uptime_seconds=time.time() - self.started_at,
            now=time.time(),
            log_file=str(self.settings.log_file),
            total_logs=self.total_logs,
            total_errors=self.total_errors,
            total_warnings=self.total_warnings,
            parse_failures=self.malformed_lines,
            malformed_lines=self.malformed_lines,
            lines_per_minute=self.lines_per_minute(),
            error_rate=rate,
            baseline_error_rate=baseline_mean,
            threshold_error_rate=threshold,
            deviation=rate - baseline_mean,
            deviation_sigma=(
                (rate - baseline_mean) / max(baseline_std, 0.01) if ready else 0.0
            ),
            current_window=current,
            baseline_mean=baseline_mean,
            baseline_std=baseline_std,
            baseline_samples=self.detector.baseline.count,
            baseline_ready=ready,
            active_anomalies=self.alerts.active_count,
            total_alerts=self.alerts.total,
            notifier=self.notifier.status(),
            trend=self.detector.trend_points,
            recent_errors=list(self._recent_errors)[-25:][::-1],
        )

    def demo_reset(self) -> Dict[str, Any]:
        """Reset counters/alerts (used by the demo control button)."""

        self.total_logs = 0
        self.total_errors = 0
        self.total_warnings = 0
        self.malformed_lines = 0
        self._recent_errors.clear()
        self._event_times.clear()
        self._errors.clear()
        self.alerts = AlertStore(self.settings.max_alerts_in_memory)
        self.detector = AnomalyDetector(self.settings)
        self.started_at = time.time()
        return {"status": "reset"}
