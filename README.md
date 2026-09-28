# Real-Time Log Anomaly Detector with Alert Feed

Watches a growing log file, computes a **rolling error rate** over a sliding
window, learns a **baseline** of normal behaviour, raises **anomaly alerts**
with a **severity level** the moment the rate deviates, streams everything to a
**real-time React dashboard over WebSockets**, and **notifies AWS CloudWatch
Logs / SNS** when configured (with a safe local mock mode when it is not).

No database, no Docker, no auth — just Python + FastAPI + React.

---

## 1. Quick start

Two terminals, from the project root.

### Terminal 1 — backend

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend
```

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend
```

Backend: <http://127.0.0.1:8000> · API docs: <http://127.0.0.1:8000/docs>

### Terminal 2 — log generator (the demo)

```powershell
.\.venv\Scripts\python backend\app\generator.py --demo
```

### Terminal 3 — frontend

```powershell
cd frontend
npm install
npm run dev
```

Dashboard: **<http://localhost:5173>** (redirects to `/dashboard`)

> **Use `localhost`, not `127.0.0.1`.** The Vite dev server binds to IPv6
> loopback (`::1`) by default, so `http://127.0.0.1:5173` will refuse the
> connection. This is a Vite default, not a bug in the app.

---

## 2. Project structure

```
log-anomaly-detector/
├── requirements.txt              # backend deps (root copy)
├── .env.example                  # every backend setting, documented
├── README.md
│
├── backend/
│   ├── requirements.txt          # identical to root copy
│   ├── app/
│   │   ├── config.py             # env-var driven Settings singleton
│   │   ├── models.py             # LogEvent, WindowStats, Alert, Metrics, enums
│   │   ├── parser.py             # tolerant multi-format line parser
│   │   ├── tailer.py             # incremental follow, rotation/truncation safe
│   │   ├── detector.py           # sliding window, baseline, severity, dedup
│   │   ├── alert_store.py        # bounded in-memory alert ring
│   │   ├── notifier.py           # CloudWatch / SNS / local mock
│   │   ├── service.py            # monitor thread + WebSocket fan-out
│   │   ├── main.py               # FastAPI REST + /ws
│   │   └── generator.py          # synthetic log generator / demo script
│   ├── data/
│   │   ├── sample.log            # 13-line sample of every supported format
│   │   ├── demo.log              # live demo target (auto-created)
│   │   └── alerts/alerts.log     # every alert emitted (mock mode)
│   └── tests/                    # 73 tests
│
└── frontend/
    ├── package.json  vite.config.js  index.html  .env.example
    └── src/
        ├── App.jsx  main.jsx  index.css  api.js
        ├── components/  Navbar, MetricCard, AlertFeed, AlertCard,
        │                SeverityBadge, ErrorRateChart, ServiceFilter
        ├── pages/       Dashboard, Alerts, AlertDetails
        ├── hooks/       useWebSocket.js
        └── services/    api.js, websocket.js
```

---

## 3. How detection works

Deliberately simple and explainable — no ML, no black box.

**1. Sliding window.** Every `DETECT_EVALUATE_EVERY` (2 s) the events whose
timestamp falls inside the last `DETECT_WINDOW_SECONDS` (20 s) are aggregated
into one *window sample* of `error_rate = errors / total`.
Counted as errors: `ERROR`, `CRITICAL`, `FATAL`.

**2. Baseline.** The trailing `DETECT_BASELINE_WINDOW` (15) **non-anomalous**
window samples give `mean` and `std`. Anomalous windows are deliberately
*excluded*, so an incident never rewrites what "normal" means.

**3. Threshold.**

```
threshold = max( mean + σ_multiplier × std ,  min_error_rate ,  mean + 0.5pp )
          = max( mean + 3.0 × std           ,  5%          ,  mean + 0.5pp )
```

A window breaches when `error_rate > threshold` **and** the window holds at
least `DETECT_MIN_SAMPLES` (20) events — so an idle window of 2 events never
trips an alert.

**4. Severity** is driven by *how far past the threshold* we are:

```
overage = (error_rate − threshold) / threshold
```

| Severity | Condition | Typical error rate @ 5% threshold |
|----------|-----------|-----------------------------------|
| `LOW` | any confirmed breach | ~6–14% |
| `MEDIUM` | overage ≥ 2 (≥ 3× threshold) | ~15–19% |
| `HIGH` | overage ≥ 5 (≥ 6× threshold) | ~20–34% |
| `CRITICAL` | overage ≥ 10 (≥ 11× threshold) **or** `error_rate ≥ 50%` | ≥ 55% |

**5. Duplicate suppression.** While an incident is open no new alert is
raised — *unless* the severity escalates, in which case a new higher-severity
alert is emitted against the same `incidentId`. That is how one outage
produces a readable `LOW → MEDIUM → HIGH → CRITICAL` escalation instead of
alert spam. After `DETECT_RECOVERY_WINDOWS` (2) consecutive normal windows the
incident is closed with a single `RECOVERED` alert.

**Warm-up.** The first `DETECT_WARMUP_WINDOWS` (2) windows only learn the
baseline and report status `STARTING`; they cannot raise alerts.

---

## 4. Alert shape

```jsonc
{
  "id": "alert-c04464f6cf6e",
  "timestamp": 1772123456.78,
  "severity": "MEDIUM",              // LOW | MEDIUM | HIGH | CRITICAL
  "status": "ACTIVE",                // ACTIVE | RECOVERED
  "errorRate": 0.343062,             // current window
  "baselineRate": 0.074333,          // learned normal
  "deviation": 0.268729,             // errorRate − baselineRate
  "deviationSigma": 12.4,            // (errorRate − mean) / max(std, 0.01)
  "thresholdRate": 0.050,            // the bar that was crossed
  "reason": "Error rate 34.3% over the last 20s window exceeds the alert
             threshold 5.0% (baseline 7.4% + 3σ=0.0%). Deviation 12.4σ,
             5.9× the threshold, 137 errors in 399 events.",
  "windowStart": 1772123436.78,
  "windowEnd":   1772123456.78,
  "windowSeconds": 20.0,
  "sampleSize": 399,                 // events in window
  "errorCount": 137,
  "messageSample": "payment gateway returned HTTP 502 …",
  "topErrorMessages": [ { "message": "connection refused host=<id>", "count": 88 } ],
  "incidentId": "inc-3-1772123456",
  "sequence": 7
}
```

---

## 5. API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | liveness, uptime, connected WS clients |
| GET | `/api/status` | compact status for the dashboard header |
| GET | `/api/metrics` | full metrics snapshot (same payload as the WS `metrics` message) |
| GET | `/api/alerts` | newest first; `?limit=&offset=&severity=&status=` |
| GET | `/api/alerts/{alert_id}` | one alert, or 404 |
| GET | `/api/recent-errors` | latest error events with messages |
| GET | `/api/config` | effective detection + notifier config |
| PUT | `/api/config/detection` | live-tune thresholds, no restart |
| POST | `/api/log/append` | append one raw line to the watched file |
| POST | `/api/demo/simulate` | inject N synthetic lines at a chosen error rate |
| POST | `/api/demo/reset` | clear counters, baseline and alerts |
| POST | `/api/test-notification` | fire a probe alert through the notifier |
| WS | `/ws` | `metrics` (1/s), `alert` (immediately), `ping`/`pong` |

Interactive docs: <http://127.0.0.1:8000/docs>

### WebSocket messages

```jsonc
{ "type": "metrics", "data": { /* == GET /api/metrics */ } }
{ "type": "alert",   "data": { /* alert shape above */ } }
{ "type": "ping",    "data": { "at": 1772123456.78 } }   // reply "ping" -> "pong"
```

The monitor thread never touches a socket directly; pushes are scheduled onto
the API event loop with `run_coroutine_threadsafe`. If the socket drops, the
frontend reconnects with exponential backoff and falls back to REST polling.

---

## 6. AWS integration

**Nothing here is required to run the project.** With no AWS configuration the
app runs in *local mock mode*: every alert is appended to
`backend/data/alerts/alerts.log` (and `alerts.json`) and the dashboard header
shows `LOCAL (mock)`.

### 6a. Credentials

Never put keys in `.env`, in `frontend/.env*`, or in the code. boto3 resolves
credentials through its standard chain:

1. `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` env vars
2. shared credentials file `~/.aws/credentials` (selectable via `AWS_PROFILE`)
3. EC2 instance profile / ECS task role / EKS IRSA *(recommended)*

```powershell
# Option A - named profile
aws sso login --profile my-codeathon-profile
$env:AWS_PROFILE = "my-codeathon-profile"

# Option B - env vars for the session
$env:AWS_REGION = "us-east-1"
$env:AWS_ACCESS_KEY_ID = "AKIA…"
$env:AWS_SECRET_ACCESS_KEY = "…"
```

### 6b. CloudWatch Logs

```powershell
$env:AWS_REGION                      = "us-east-1"
$env:CLOUDWATCH_LOG_GROUP            = "/aws/codeathon/log-anomaly-detector"
$env:CLOUDWATCH_LOG_STREAM           = "anomaly-alerts"
$env:NOTIFY_PROVIDER                 = "cloudwatch"   # or leave "auto"
```

The log group and stream are created on startup if missing, and the stream's
`uploadSequenceToken` is tracked so `PutLogEvents` never fails with
`InvalidSequenceTokenException`.

Required IAM permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams"
    ],
    "Resource": "*"
  }]
}
```

### 6c. SNS

```powershell
$env:AWS_REGION        = "us-east-1"
$env:SNS_TOPIC_ARN     = "arn:aws:sns:us-east-1:123456789012:log-anomaly-alerts"
$env:NOTIFY_PROVIDER   = "sns"        # or leave "auto"
```

The message is published with `MessageStructure=json` — the human-readable
line lives under `default`, the full alert JSON under `alert` — so an email /
SMS / Lambda subscriber gets something readable while a Lambda can parse the
payload. Required permission: `sns:Publish`.

### 6d. Provider selection

`NOTIFY_PROVIDER` is one of:

| Value | Behaviour |
|-------|-----------|
| `local` | always write to the local alert files (safe default) |
| `cloudwatch` | require CloudWatch; fall back to local if it fails |
| `sns` | require SNS; fall back to local if it fails |
| `auto` *(default)* | use CloudWatch if `CLOUDWATCH_LOG_GROUP` is set, else SNS if `SNS_TOPIC_ARN` is set, else local |

A failed AWS delivery **never** breaks the monitor: the error is recorded in
`GET /api/metrics → notifier.lastError`, shown in the dashboard, and the alert
is still written locally. Verify the wiring at any time with:

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/test-notification
```

> **Status: implemented, not yet verified against a live AWS account.** The
> local/fallback path is covered by tests; the CloudWatch and SNS calls have
> not been executed against real AWS credentials in this environment. Treat
> 6b/6c as untested until someone runs the probe command above with real
> credentials.

---

## 7. Demo

### Scripted demo (walks every severity)

```powershell
.\.venv\Scripts\python backend\app\generator.py --demo
```

| Phase | Error rate | Expected on the dashboard |
|-------|-----------|---------------------------|
| 1 | 2% steady | baseline learned, status → `HEALTHY` |
| 2 | 8% | `LOW` alert |
| 3 | 8% → 35% ramp | escalates to `MEDIUM` / `HIGH` |
| 4 | 85% | escalates to `CRITICAL` |
| 5 | 2% | `RECOVERED` alert, incident closed |

Give it ~30 s of warm-up before phase 2 registers — the baseline must be
learned first.

### Individual scenarios

```powershell
.\.venv\Scripts\python backend\app\generator.py --scenario normal  --rate 0.02
.\.venv\Scripts\python backend\app\generator.py --scenario ramp    --rate 0.35 --duration 60
.\.venv\Scripts\python backend\app\generator.py --scenario spike   --rate 0.9
.\.venv\Scripts\python backend\app\generator.py --scenario recovery --rate 0.01
```

Flags: `--lines N` (per tick), `--interval S`, `--duration S`, `--truncate`.

### From the dashboard

The **Demo Controls** panel injects 200 lines at 2% / 8% / 40% / 90%, sends a
test notification, and resets counters — no terminal needed.

### Supported log formats

```
2024-05-14T10:15:02.114Z INFO  [api-gateway] request completed status=200 duration_ms=42
[2024-05-14 10:15:05.771Z] [ERROR] [db-primary] database connection refused
2024/05/14 10:15:04 level=ERROR service=payment msg="upstream timeout"
14/May/2024:10:15:04 +0000 ERROR nginx upstream timed out
2024-05-14T10:15:05 FATAL db-primary cluster unreachable
```

`ERROR` / `CRITICAL` / `FATAL` count as errors; `WARN` / `WARNING` are tracked
separately. `key=value` pairs are extracted into `extra`. A line with no
recognisable level becomes an event with `parseOk=false` and is counted under
`malformedLines` — **it never crashes the monitor**.

---

## 8. Configuration

Copy `.env.example` to `.env` at the project root. Everything is optional;
the defaults below are what the app uses out of the box.

### Monitoring
| Variable | Default | Meaning |
|----------|---------|---------|
| `LOG_FILE` | `backend/data/demo.log` | file to watch (created if absent) |
| `LOG_POLL_INTERVAL` | `0.2` | seconds between tail polls |
| `LOG_MAX_LINE_BYTES` | `8192` | per-line truncation guard |

### Detection
| Variable | Default | Meaning |
|----------|---------|---------|
| `DETECT_WINDOW_SECONDS` | `20` | sliding window length |
| `DETECT_EVALUATE_EVERY` | `2` | evaluation interval |
| `DETECT_SIGMA_MULTIPLIER` | `3.0` | `k` in `mean + k·std` |
| `DETECT_MIN_ERROR_RATE` | `0.05` | absolute floor for the threshold |
| `DETECT_MIN_SAMPLES` | `20` | minimum events before a window can alert |
| `DETECT_WARMUP_WINDOWS` | `2` | baseline-only windows at startup |
| `DETECT_BASELINE_WINDOW` | `15` | trailing windows kept as baseline |
| `DETECT_RECOVERY_WINDOWS` | `2` | normal windows before closing an incident |

### Severity
| Variable | Default |
|----------|---------|
| `SEVERITY_MEDIUM_OVERAGE` | `2.0` |
| `SEVERITY_HIGH_OVERAGE` | `5.0` |
| `SEVERITY_CRITICAL_OVERAGE` | `10.0` |
| `SEVERITY_CRITICAL_RATE` | `0.5` |

### API / notification
| Variable | Default |
|----------|---------|
| `HOST` / `PORT` | `127.0.0.1` / `8000` |
| `CORS_ORIGINS` | `*` |
| `MAX_ALERTS_IN_MEMORY` | `500` |
| `NOTIFY_PROVIDER` | `auto` |
| `ALERT_LOG_FILE` | `backend/data/alerts/alerts.log` |
| `ALERT_EXPORT_FILE` | `backend/data/alerts/alerts.json` |

The dashboard can retune `sigmaMultiplier` live via
`PUT /api/config/detection` (slider in the Detection Config panel).

### Frontend
Copy `frontend/.env.example` → `frontend/.env.local`. Leave `VITE_API_URL` /
`VITE_WS_URL` empty to use the Vite dev proxy (the default). `VITE_DEMO_MODE`
(`auto` / `always` / `never`) lets the UI run on fake data with no backend —
useful when demoing to a room with no Wi-Fi.

---

## 9. Tests

```powershell
.\.venv\Scripts\python -m pytest backend\tests -q
```

```
73 passed
```

Coverage: parser formats + malformed input, incremental tailing (appends,
partial lines, truncate-and-regrow, rename rotation, missing file, oversized
lines), baseline learning, breach detection, all four severity levels,
escalation, duplicate suppression, recovery, baseline hygiene, notifier
fallback, every REST endpoint, and the WebSocket alert push.

---

## 10. Robustness notes

- **Incremental reads** — only the newly appended bytes are read; the file is
  never re-scanned from the start.
- **Rotation & truncation** — detected by inode change *and* by a head-of-file
  fingerprint, so a `copytruncate` logrotate (file shrinks, then regrows past
  the old offset) is caught too. State resets cleanly and a counter is exposed.
- **Partial lines** — buffered until the newline arrives, then parsed once.
- **Malformed input** — never raises; counted and surfaced in the UI.
- **Long lines** — truncated at `LOG_MAX_LINE_BYTES`.
- **Monitor resilience** — every loop iteration is wrapped; a failure is
  logged and the loop continues, so the backend keeps monitoring even while
  the frontend is closed.
- **In-memory state only** — alerts are a bounded ring buffer (no DB), which
  is all the MVP needs.

---

## 11. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `http://127.0.0.1:5173` refuses the connection | Vite binds IPv6 loopback — use `http://localhost:5173` |
| Dashboard stuck on "Connecting…" | backend not running; check `http://127.0.0.1:8000/api/health` |
| Header shows `POLLING` not `LIVE` | WebSocket blocked — the UI has fallen back to REST polling and still works |
| No alerts during a demo | give it ~30 s of low-error traffic first so the baseline is learned; confirm via `GET /api/metrics → baselineReady` |
| `baselineReady` stays false | window is too empty — increase `LOG_FILE` traffic or lower `DETECT_MIN_SAMPLES` |
| Alerts are too sensitive / not sensitive enough | adjust `DETECT_SIGMA_MULTIPLIER` (live) or `DETECT_MIN_ERROR_RATE` |
| Notifier says `local (mock)` | no AWS config resolved — see §6; this is the intended fallback |
| `Address already in use` | `netstat -ano | findstr :8000` then `taskkill /PID <pid> /F` |

---

## 12. Known limitations / remaining work

Honest list of what is **not** done or not proven:

1. **AWS delivery is unverified.** The CloudWatch and SNS code paths have not
   been executed against a real AWS account — only the local/fallback path is
   test-covered. Run `POST /api/test-notification` with real credentials to
   close this out.
2. **The dashboard has not been visually reviewed in a browser.** It is
   verified through HTTP/WebSocket responses and a passing production build,
   not by looking at it. Do this before demoing.
3. **Frontend ownership is muddled.** The React app was rewritten mid-project
   by another contributor ("Member 3") onto a `react-router-dom` + `recharts`
   multi-page structure. Components from the earlier single-page draft were
   overwritten or deleted. It builds and runs, but the file history is
   inconsistent and `components/AlertFeed.jsx` / `ErrorRateChart.jsx` are now
   the newer versions, not the originals.
4. **Dev server is IPv6-only** (see §1). Add `server.host: '127.0.0.1'` to
   `frontend/vite.config.js` if an IPv4-only judge machine is a risk.
5. **`HIGH` and `CRITICAL` have not been observed in a live run** — only in
   unit tests. Re-run the `--demo` script end to end to confirm the full
   escalation ladder appears on screen.
6. **State is not persisted.** Restarting the backend clears alerts and the
   learned baseline. Fine for a demo, not for production.
7. **No authentication** on the API or WebSocket, by design.
8. **Single-file monitor** — it watches one log file. Multiple files would
   need one `LogMonitorService` per file.
9. `frontend.zip` (~18 MB) is sitting loose in the project root and should be
   removed or archived outside the submission.
10. Several orphaned `node` processes from earlier dev runs may still be
    holding port 5173.

---

## 13. Credits

Backend, detection logic, demo generator, tests and documentation were built
for this project. The React dashboard was subsequently reworked by a second
contributor into a routed multi-page app; see limitation 3.
