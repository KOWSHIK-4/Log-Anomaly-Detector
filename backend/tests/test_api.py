"""End-to-end tests for the FastAPI app: REST endpoints and WebSocket."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from app.models import LogLevel


def stamp() -> str:
    """A *current* ISO-8601 timestamp - the sliding window ignores old lines."""

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def append(settings, *lines: str) -> None:
    with open(settings.log_file, "a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


def append_now(settings, level: str, message: str, count: int = 1) -> None:
    ts = stamp()
    append(settings, *[f"{ts} {level} {message}"] * count)


class TestSystemEndpoints:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["monitorRunning"] is True

    def test_status(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("status", "totalLogs", "totalErrors", "activeAnomalies", "notifierMode"):
            assert key in body

    def test_root(self, client):
        assert client.get("/").status_code == 200

    def test_openapi_schema(self, client):
        assert client.get("/openapi.json").status_code == 200


class TestMetrics:
    def test_metrics_shape(self, client):
        body = client.get("/api/metrics").json()
        for key in (
            "status",
            "errorRate",
            "baselineErrorRate",
            "thresholdErrorRate",
            "totalLogs",
            "totalErrors",
            "activeAnomalies",
            "currentWindow",
            "baselineReady",
            "notifier",
            "trend",
            "recentErrors",
        ):
            assert key in body, f"missing {key}"
        assert isinstance(body["trend"], list)

    def test_logged_lines_are_counted(self, client, settings):
        before = client.get("/api/metrics").json()["totalLogs"]
        ts = stamp()
        append(
            settings,
            f"{ts} INFO one",
            f"{ts} ERROR two",
            f"{ts} CRITICAL three",
        )
        deadline = time.time() + 6
        body = client.get("/api/metrics").json()
        while time.time() < deadline and body["totalLogs"] < before + 3:
            time.sleep(0.1)
            body = client.get("/api/metrics").json()
        assert body["totalLogs"] >= before + 3
        assert body["totalErrors"] >= 2

    def test_malformed_lines_are_counted(self, client, settings):
        append(settings, "this line has no level at all")
        deadline = time.time() + 6
        body = client.get("/api/metrics").json()
        while time.time() < deadline and body["malformedLines"] < 1:
            time.sleep(0.1)
            body = client.get("/api/metrics").json()
        assert body["malformedLines"] >= 1

    def test_recent_errors_endpoint(self, client, settings):
        append(settings, f"{stamp()} ERROR disk failure on node 3")
        deadline = time.time() + 6
        body = client.get("/api/recent-errors").json()
        while time.time() < deadline and not body["items"]:
            time.sleep(0.1)
            body = client.get("/api/recent-errors").json()
        assert body["items"]
        assert body["items"][0]["level"] in ("ERROR", "CRITICAL", "FATAL")


class TestAlerts:
    def test_alerts_shape(self, client):
        body = client.get("/api/alerts").json()
        for key in ("total", "active", "countsBySeverity", "items"):
            assert key in body
        assert isinstance(body["items"], list)

    def test_alert_404(self, client):
        assert client.get("/api/alerts/does-not-exist").status_code == 404

    def test_alert_filters_accepted(self, client):
        assert client.get("/api/alerts?severity=HIGH&limit=5").status_code == 200
        assert client.get("/api/alerts?status=ACTIVE").status_code == 200
        assert client.get("/api/alerts?limit=0").status_code == 422


class TestConfig:
    def test_get_config(self, client):
        body = client.get("/api/config").json()
        assert "windowSeconds" in body
        assert "sigmaMultiplier" in body
        assert "notifier" in body
        assert body["notifier"]["mode"] in ("local", "aws")

    def test_live_threshold_update(self, client, settings):
        resp = client.put("/api/config/detection", json={"sigmaMultiplier": 5.0, "minErrorRate": 0.11})
        assert resp.status_code == 200
        applied = resp.json()["applied"]
        assert applied["sigmaMultiplier"] == 5.0
        assert applied["minErrorRate"] == 0.11
        assert settings.sigma_multiplier == 5.0

    def test_invalid_update_is_ignored(self, client, settings):
        before = settings.sigma_multiplier
        client.put("/api/config/detection", json={"sigmaMultiplier": "not-a-number", "bogus": 1})
        assert settings.sigma_multiplier == before


class TestDemoHelpers:
    def test_append_endpoint(self, client, settings):
        resp = client.post("/api/log/append", json={"line": f"{stamp()} INFO injected"})
        assert resp.status_code == 200
        assert "injected" in settings.log_file.read_text(encoding="utf-8")

    def test_simulate_endpoint(self, client, settings):
        resp = client.post("/api/demo/simulate", json={"count": 50, "errorRate": 1.0})
        assert resp.status_code == 200
        deadline = time.time() + 6
        while time.time() < deadline:
            if client.get("/api/metrics").json()["totalLogs"] >= 50:
                break
            time.sleep(0.1)
        body = client.get("/api/metrics").json()
        assert body["totalLogs"] >= 50
        assert body["totalErrors"] >= 50

    def test_simulate_validation(self, client):
        assert client.post("/api/demo/simulate", json={"count": 0}).status_code == 422
        assert client.post("/api/demo/simulate", json={"errorRate": 5}).status_code == 422

    def test_reset(self, client, settings):
        client.post("/api/demo/simulate", json={"count": 10, "errorRate": 0.5})
        resp = client.post("/api/demo/reset")
        assert resp.status_code == 200
        assert client.get("/api/metrics").json()["totalLogs"] == 0

    def test_test_notification(self, client, settings):
        resp = client.post("/api/test-notification")
        assert resp.status_code == 200
        assert resp.json()["notifier"]["mode"] == "local"
        assert settings.alert_log_file.exists()


class TestWebSocket:
    def test_metrics_are_pushed(self, client):
        with client.websocket_connect("/ws") as ws:
            first = ws.receive_json()
            assert first["type"] == "metrics"
            assert "errorRate" in first["data"]
            second = ws.receive_json()
            assert second["type"] == "metrics"

    def test_alert_is_pushed(self, client, settings):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # initial metrics
            settings.min_samples = 5

            # 1) let the monitor learn a healthy baseline first
            append_now(settings, "INFO", "normal traffic", 200)
            deadline = time.time() + 6
            while time.time() < deadline:
                if client.get("/api/metrics").json()["baselineReady"]:
                    break
                time.sleep(0.1)
            assert client.get("/api/metrics").json()["baselineReady"] is True

            # 2) flood the window with errors to trip the detector
            append_now(settings, "ERROR", "synthetic failure", 400)

            received = None
            for _ in range(80):
                message = ws.receive_json()
                if message["type"] == "alert":
                    received = message["data"]
                    break
            assert received is not None, "no alert pushed over the websocket"
            for key in ("id", "severity", "errorRate", "baselineRate", "reason", "timestamp", "status"):
                assert key in received
            assert received["errorRate"] > received["baselineRate"]

    def test_ping_pong(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_text("ping")
            for _ in range(30):
                message = ws.receive_json()
                if message["type"] == "pong":
                    break
            else:
                raise AssertionError("no pong received")
