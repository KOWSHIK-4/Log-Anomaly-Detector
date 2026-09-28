"""FastAPI application: REST + WebSocket API for the anomaly detector."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import get_settings
from .models import Alert, LogEvent, Metrics
from .notifier import Notifier
from .service import LogMonitorService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("anomaly.api")

settings = get_settings()
service = LogMonitorService(settings)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    # Bind the API loop so the monitor thread can push updates safely.
    service.manager.bind_loop(asyncio.get_running_loop())
    service.start()
    logger.info("Watching log file: %s", settings.log_file)
    logger.info("Notifier mode: %s", service.notifier.status()["mode"])
    try:
        yield
    finally:
        service.stop()


app = FastAPI(
    title="Real-Time Log Anomaly Detector",
    description="Rolling error-rate monitoring with baseline deviation alerts.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------
# REST endpoints
# ----------------------------------------------------------------------
@app.get("/api/health", tags=["system"])
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "log-anomaly-detector",
        "version": app.version,
        "monitorRunning": service.running,
        "uptimeSeconds": round(time.time() - service.started_at, 2),
        "logFile": str(settings.log_file),
        "clients": service.manager.count,
    }


@app.get("/api/status", tags=["system"])
async def status() -> Dict[str, Any]:
    """Compact status used by the dashboard header and health checks."""

    metrics = service.build_metrics()
    return {
        "status": metrics.status.value,
        "uptimeSeconds": round(metrics.uptime_seconds, 2),
        "logFile": metrics.log_file,
        "totalLogs": metrics.total_logs,
        "totalErrors": metrics.total_errors,
        "activeAnomalies": metrics.active_anomalies,
        "notifierMode": metrics.notifier["mode"],
        "clients": service.manager.count,
    }


@app.get("/api/metrics", response_model=None, tags=["metrics"])
async def metrics() -> Dict[str, Any]:
    """Full metrics snapshot (same payload broadcast over the WebSocket)."""

    return service.build_metrics().to_dict()


@app.get("/api/alerts", tags=["alerts"])
async def alerts(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    severity: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
) -> Dict[str, Any]:
    items = service.alerts.list(limit=limit, offset=offset, severity=severity, status=status_filter)
    return {
        "total": service.alerts.total,
        "active": service.alerts.active_count,
        "countsBySeverity": service.alerts.counts_by_severity(),
        "items": [item.to_dict() for item in items],
    }


@app.get("/api/alerts/{alert_id}", tags=["alerts"])
async def alert_detail(alert_id: str) -> Dict[str, Any]:
    alert = service.alerts.get(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="alert not found")
    return alert.to_dict()


@app.get("/api/recent-errors", tags=["metrics"])
async def recent_errors(limit: int = Query(20, ge=1, le=200)) -> Dict[str, Any]:
    snapshot = service.build_metrics()
    return {"items": [event.to_dict() for event in snapshot.recent_errors[:limit]]}


class LogLine(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    line: str = Field(..., min_length=1, description="A single raw log line")


class SimulateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    count: int = Field(20, ge=1, le=5000, description="How many lines to inject")
    error_rate: float = Field(0.5, ge=0.0, le=1.0, alias="errorRate", description="Fraction of ERROR lines")
    level: str = Field("ERROR", description="Level used for the injected error lines")
    message: str = Field("Synthetic error injected via API", description="Message text")


@app.post("/api/log/append", tags=["demo"])
async def append_log(payload: LogLine) -> Dict[str, Any]:
    """Append a raw line to the watched log file (handy for quick tests)."""

    try:
        with open(settings.log_file, "a", encoding="utf-8") as handle:
            handle.write(payload.line.rstrip("\n") + "\n")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"cannot write log file: {exc}") from exc
    return {"status": "appended", "line": payload.line}


@app.post("/api/demo/simulate", tags=["demo"])
async def simulate(payload: SimulateRequest) -> Dict[str, Any]:
    """Inject N synthetic log lines with the given error ratio."""

    import random

    level = payload.level.strip().upper()
    try:
        with open(settings.log_file, "a", encoding="utf-8") as handle:
            for index in range(payload.count):
                is_error = random.random() < payload.error_rate
                line_level = level if is_error else random.choice(["INFO", "INFO", "WARN"])
                stamp = time.strftime("%Y-%m-%dT%H:%M:%S") + f".{int(random.random() * 999):03d}Z"
                handle.write(f"{stamp} {line_level} {payload.message} #{index}\n")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"cannot write log file: {exc}") from exc
    return {"status": "simulated", "count": payload.count, "errorRate": payload.error_rate}


@app.post("/api/demo/reset", tags=["demo"])
async def reset() -> Dict[str, Any]:
    return service.demo_reset()


@app.get("/api/config", tags=["system"])
async def current_config() -> Dict[str, Any]:
    return {
        "logFile": str(settings.log_file),
        "pollInterval": settings.poll_interval,
        "windowSeconds": settings.window_seconds,
        "evaluateEvery": settings.evaluate_every,
        "sigmaMultiplier": settings.sigma_multiplier,
        "minErrorRate": settings.min_error_rate,
        "minSamples": settings.min_samples,
        "warmupWindows": settings.warmup_windows,
        "baselineWindow": settings.baseline_window,
        "recoveryWindows": settings.recovery_windows,
        "severity": {
            "mediumOverage": settings.severity_medium_overage,
            "highOverage": settings.severity_high_overage,
            "criticalOverage": settings.severity_critical_overage,
            "criticalRate": settings.severity_critical_rate,
        },
        "notifier": service.notifier.status(),
    }


@app.put("/api/config/detection", tags=["system"])
async def update_detection_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Live-tune detection thresholds (applies immediately, no restart)."""

    allowed = {
        "sigmaMultiplier": ("sigma_multiplier", float),
        "minErrorRate": ("min_error_rate", float),
        "windowSeconds": ("window_seconds", float),
        "evaluateEvery": ("evaluate_every", float),
        "minSamples": ("min_samples", int),
        "recoveryWindows": ("recovery_windows", int),
        "severityMediumOverage": ("severity_medium_overage", float),
        "severityHighOverage": ("severity_high_overage", float),
        "severityCriticalOverage": ("severity_critical_overage", float),
        "severityCriticalRate": ("severity_critical_rate", float),
    }
    applied: Dict[str, Any] = {}
    for key, value in (payload or {}).items():
        if key not in allowed:
            continue
        attr, caster = allowed[key]
        try:
            casted = caster(value)
        except (TypeError, ValueError):
            continue
        setattr(settings, attr, casted)
        applied[key] = casted
    if applied:
        service.detector.settings = settings
    return {"applied": applied, "config": await current_config()}


@app.post("/api/test-notification", tags=["alerts"])
async def test_notification() -> Dict[str, Any]:
    """Send a synthetic alert through the notifier to verify AWS wiring."""

    from .models import AlertStatus, Severity

    probe = Alert(
        id=Alert.new_id(),
        timestamp=time.time(),
        severity=Severity.LOW,
        status=AlertStatus.ACTIVE,
        error_rate=0.42,
        baseline_rate=0.03,
        deviation=0.39,
        deviation_sigma=6.5,
        threshold_rate=0.12,
        reason="Test notification - AWS/local delivery check",
        window_start=time.time() - settings.window_seconds,
        window_end=time.time(),
        window_seconds=settings.window_seconds,
        sample_size=100,
        error_count=42,
        message_sample="[TEST] synthetic notification",
    )
    service.notifier.notify(probe)
    return {"sent": True, "notifier": service.notifier.status()}


# ----------------------------------------------------------------------
# WebSocket endpoint
# ----------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Pushes ``metrics`` (1/s) and ``alert`` (immediately) events."""

    await service.manager.connect(websocket)
    logger.info("WS client connected (%s total)", service.manager.count)
    last_heartbeat = time.time()
    try:
        # Initial snapshot so the dashboard renders instantly.
        await websocket.send_json({"type": "metrics", "data": service.build_metrics().to_dict()})
        while True:
            try:
                # Short timeout keeps the socket responsive and also acts as
                # the heartbeat clock.
                message = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                if time.time() - last_heartbeat >= 20.0:
                    last_heartbeat = time.time()
                    await websocket.send_json({"type": "ping", "data": {"at": last_heartbeat}})
                continue
            if message == "ping":
                await websocket.send_json({"type": "pong", "data": {"at": time.time()}})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover - transport errors
        logger.info("WS error: %s", exc)
    finally:
        await service.manager.disconnect(websocket)
        logger.info("WS client disconnected (%s left)", service.manager.count)


@app.get("/", tags=["system"])
async def root() -> JSONResponse:
    return JSONResponse(
        {
            "service": "Real-Time Log Anomaly Detector",
            "docs": "/docs",
            "websocket": "/ws",
            "endpoints": [
                "/api/health",
                "/api/status",
                "/api/metrics",
                "/api/alerts",
                "/api/recent-errors",
                "/api/config",
                "/api/demo/simulate",
            ],
        }
    )
