/**
 * services/api.js
 * ---------------------------------------------------------------------------
 * Single source of truth for every piece of data the UI renders.
 *
 *   1. Configuration  - VITE_API_URL / VITE_DEMO_MODE (never hard-coded URLs)
 *   2. Transport      - fetch() with timeout + multi-path fallback
 *   3. Normalisation  - snake_case/camelCase, epoch-s/ms/ISO, ratio/percent
 *                       all collapsed into ONE canonical shape here, so a
 *                       backend rename never requires touching a component.
 *   4. Demo data      - used automatically whenever the backend is unreachable
 *
 * Expected backend contract (see README notes in the final report):
 *   GET  /api/health            -> { status, version, uptimeSeconds, ... }
 *   GET  /api/metrics           -> { errorRate, baselineErrorRate, ... }
 *   GET  /api/alerts            -> { total, items: [...] }
 *   GET  /api/alerts/{id}       -> { ...alert }
 *   POST /api/notifications/test-> { sent: true }
 *
 * Every path is declared in ENDPOINTS and can be an array of candidates:
 * the first one that answers wins, so both `/api/health` and `/health`
 * work without editing any component.
 */

/* ------------------------------------------------------------------ *
 * 1. Configuration
 * ------------------------------------------------------------------ */

/** Base URL of the FastAPI backend. Empty string = same origin (Vite proxy). */
export const API_BASE = String(
  import.meta.env.VITE_API_URL ?? import.meta.env.VITE_API_BASE ?? '',
)
  .replace(/\/+$/, '')

/**
 * Demo mode:
 *   'auto'   (default) try the real backend, silently fall back to demo data
 *   'always' force demo data (perfect for a judge demo, no backend needed)
 *   'never'  real backend only
 */
export const DEMO_MODE = String(import.meta.env.VITE_DEMO_MODE ?? 'auto').toLowerCase()

export const isDemoForced = () => DEMO_MODE === 'always'
export const isDemoDisabled = () => DEMO_MODE === 'never'

/** The endpoint contract. Change paths here, nowhere else. */
export const ENDPOINTS = {
  health: ['/api/health', '/health'],
  status: ['/api/status'],
  metrics: ['/api/metrics'],
  alerts: ['/api/alerts'],
  alertById: (id) => [`/api/alerts/${encodeURIComponent(id)}`],
  recentErrors: ['/api/recent-errors'],
  testNotification: ['/api/notifications/test', '/api/test-notification'],
}

export const SEVERITIES = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']
export const SEVERITY_RANK = { CRITICAL: 3, HIGH: 2, MEDIUM: 1, LOW: 0 }
export const UNKNOWN_SERVICE = 'unknown-service'
export const MAX_CHART_POINTS = 60
export const MAX_ALERTS = 300

/* ------------------------------------------------------------------ *
 * 2. Transport
 * ------------------------------------------------------------------ */

const asArray = (value) => (Array.isArray(value) ? value : [value])

/** fetch() with a hard timeout - a hung backend must never freeze the UI. */
async function fetchOnce(path, { method = 'GET', body, timeout = 8000 } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    })
    if (!response.ok) throw new Error(`HTTP ${response.status} on ${path}`)
    const text = await response.text()
    if (!text) return null
    try {
      return JSON.parse(text)
    } catch {
      throw new Error(`Malformed JSON from ${path}`)
    }
  } finally {
    clearTimeout(timer)
  }
}

/** Try each candidate path in order; throw the last error if all fail. */
async function request(paths, options) {
  let lastError = new Error('Request failed')
  for (const path of asArray(paths)) {
    try {
      return await fetchOnce(path, options)
    } catch (error) {
      lastError = error
    }
  }
  throw lastError
}

/* ------------------------------------------------------------------ *
 * 3. Normalisation helpers
 * ------------------------------------------------------------------ */

const pick = (source, keys) => {
  if (!source || typeof source !== 'object') return undefined
  for (const key of keys) {
    const value = source[key]
    if (value !== undefined && value !== null && value !== '') return value
  }
  return undefined
}

const toNumber = (value) => {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

/**
 * Ratios and percentages are indistinguishable by name, so accept both:
 * a value <= 1 is treated as a fraction (0.31 -> 31%), anything above 1 is
 * already a percentage (31.2 -> 31.2%). The UI only ever sees percent.
 */
export const toPercent = (value) => {
  const number = toNumber(value)
  if (number === null) return null
  const percent = Math.abs(number) <= 1 ? number * 100 : number
  return Math.round(percent * 100) / 100
}

/** Accepts epoch seconds, epoch millis or an ISO string -> epoch millis. */
export const toEpochMs = (value) => {
  if (value === undefined || value === null || value === '') return Date.now()
  if (typeof value === 'number' && Number.isFinite(value)) {
    if (value > 1e12) return value
    if (value > 1e9) return Math.round(value * 1000)
    return value
  }
  if (typeof value === 'string') {
    if (/^-?\d+(\.\d+)?$/.test(value.trim())) return toEpochMs(Number(value))
    const parsed = Date.parse(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return Date.now()
}

export const normalizeSeverity = (value) => {
  if (typeof value === 'number' && SEVERITIES[value]) return SEVERITIES[value]
  const text = String(value ?? '').trim().toUpperCase()
  if (SEVERITIES.includes(text)) return text
  const aliases = { WARNING: 'MEDIUM', WARN: 'MEDIUM', INFO: 'LOW', MAJOR: 'HIGH', SEVERE: 'CRITICAL' }
  return aliases[text] ?? 'LOW'
}

/**
 * The service name is optional in the payload. When it is missing we try to
 * recover it from the message text, and only then fall back to a placeholder
 * so a missing field can never break the ServiceFilter.
 */
const SERVICE_PATTERN = /\b([a-z0-9][a-z0-9_-]*-service)\b/i

const deriveService = (raw, text) => {
  const explicit = pick(raw, ['service', 'serviceName', 'service_name', 'app', 'application', 'component'])
  if (typeof explicit === 'string' && explicit.trim()) return explicit.trim()

  const fromText = String(text ?? '').match(SERVICE_PATTERN)
  if (fromText) return fromText[1]

  // Last resort: the log sample and top-error messages often name the service.
  try {
    const fromPayload = JSON.stringify(raw ?? {}).match(SERVICE_PATTERN)
    if (fromPayload) return fromPayload[1]
  } catch {
    /* circular payload - give up and use the placeholder */
  }
  return UNKNOWN_SERVICE
}

/** Canonical alert shape consumed by every component. */
export function normalizeAlert(raw) {
  if (!raw || typeof raw !== 'object') return null

  const id = String(pick(raw, ['id', 'alertId', 'alert_id', 'uuid']) ?? `alert-${Date.now()}`)
  const timestamp = toEpochMs(pick(raw, ['timestamp', 'ts', 'time', 'createdAt', 'created_at', 'detectedAt']))
  const reason = String(pick(raw, ['reason', 'message', 'description', 'summary']) ?? 'Anomaly detected')
  const messageSample = String(pick(raw, ['messageSample', 'message_sample', 'sample', 'logLine', 'raw']) ?? '')

  const errorRate = toPercent(pick(raw, ['errorRate', 'error_rate', 'currentErrorRate', 'current_error_rate', 'rate']))
  const baseline = toPercent(pick(raw, ['baseline', 'baselineRate', 'baseline_rate', 'baselineErrorRate', 'baseline_error_rate', 'expected']))
  let deviation = toPercent(pick(raw, ['deviation', 'delta', 'diff', 'errorDelta']))
  if (deviation === null && errorRate !== null && baseline !== null) deviation = errorRate - baseline

  return {
    id,
    timestamp,
    severity: normalizeSeverity(pick(raw, ['severity', 'level', 'priority'])),
    status: String(pick(raw, ['status', 'state']) ?? 'ACTIVE').toUpperCase(),
    service: deriveService(raw, [reason, messageSample, raw?.message, raw?.error].join(' ')),
    errorRate,
    baseline,
    deviation,
    message: String(pick(raw, ['message', 'reason', 'summary']) ?? reason),
    reason,
    // Extra context (thresholds, sigma, sample size, ...) kept for the
    // details page so nothing the backend sends is ever thrown away.
    extra: raw,
    messageSample,
    deviationSigma: toNumber(pick(raw, ['deviationSigma', 'deviation_sigma', 'sigma', 'zScore'])),
    thresholdRate: toPercent(pick(raw, ['thresholdRate', 'threshold_rate', 'threshold'])),
    sampleSize: toNumber(pick(raw, ['sampleSize', 'sample_size', 'samples', 'windowTotal'])),
    errorCount: toNumber(pick(raw, ['errorCount', 'error_count', 'errors'])),
    windowSeconds: toNumber(pick(raw, ['windowSeconds', 'window_seconds', 'window'])),
    incidentId: pick(raw, ['incidentId', 'incident_id']) ?? null,
  }
}

/** Build the chart series, filling a rolling baseline where none is sent. */
function buildSeries(raw, baselineErrorRate) {
  const trend = pick(raw, ['trend', 'series', 'history', 'points', 'windows', 'errorRateTrend'])
  if (!Array.isArray(trend)) return []

  const points = trend
    .filter((point) => point && typeof point === 'object')
    .map((point) => {
      const rate = toPercent(pick(point, ['errorRate', 'error_rate', 'rate', 'value']))
      return {
        t: toEpochMs(pick(point, ['windowEnd', 'window_end', 'timestamp', 'time', 't'])),
        errorRate: rate ?? 0,
        baseline: toPercent(pick(point, ['baseline', 'baselineRate', 'baseline_rate'])),
        threshold: toPercent(pick(point, ['threshold', 'thresholdRate', 'threshold_rate'])),
        total: toNumber(pick(point, ['total', 'count', 'logs', 'samples'])) ?? null,
        errors: toNumber(pick(point, ['errors', 'errorCount', 'error_count'])) ?? null,
      }
    })
    .filter((point) => Number.isFinite(point.t))
    .sort((a, b) => a.t - b.t)
    .slice(-MAX_CHART_POINTS)

  // Rolling baseline over the preceding points; the metric value is the
  // fallback so the line is never blank.
  return points.map((point, index) => {
    if (point.baseline !== null) return point
    const window = points.slice(Math.max(0, index - 10), index).map((p) => p.errorRate)
    const rolling = window.length
      ? Math.round((window.reduce((sum, value) => sum + value, 0) / window.length) * 100) / 100
      : baselineErrorRate
    return { ...point, baseline: rolling }
  })
}

/** Canonical metrics shape consumed by every component. */
export function normalizeMetrics(raw) {
  if (!raw || typeof raw !== 'object') return null

  const currentErrorRate = toPercent(
    pick(raw, ['currentErrorRate', 'current_error_rate', 'errorRate', 'error_rate', 'errorPercentage']),
  )
  const baselineErrorRate = toPercent(
    pick(raw, ['baselineErrorRate', 'baseline_error_rate', 'baseline', 'baselineRate', 'baseline_rate']),
  )
  const anomalyCount =
    toNumber(pick(raw, ['anomalyCount', 'anomaly_count', 'activeAnomalies', 'active_anomalies', 'anomalies'])) ?? 0

  const rawStatus = String(pick(raw, ['status', 'systemStatus', 'system_status', 'state']) ?? 'HEALTHY').toUpperCase()
  const series = buildSeries(raw, baselineErrorRate ?? 0)
  const lastPoint = series.length ? series[series.length - 1].errorRate : null

  return {
    status: rawStatus,
    currentErrorRate: currentErrorRate ?? lastPoint ?? 0,
    baselineErrorRate: baselineErrorRate ?? 0,
    thresholdErrorRate: toPercent(pick(raw, ['thresholdErrorRate', 'threshold_error_rate', 'threshold'])),
    deviation: toPercent(pick(raw, ['deviation', 'delta'])),
    deviationSigma: toNumber(pick(raw, ['deviationSigma', 'deviation_sigma', 'sigma'])),
    anomalyCount,
    totalAlerts: toNumber(pick(raw, ['totalAlerts', 'total_alerts', 'alertCount'])) ?? anomalyCount,
    totalLogs: toNumber(pick(raw, ['totalLogs', 'total_logs', 'logsProcessed', 'processed'])) ?? 0,
    totalErrors: toNumber(pick(raw, ['totalErrors', 'total_errors', 'errors'])) ?? 0,
    totalWarnings: toNumber(pick(raw, ['totalWarnings', 'total_warnings', 'warnings'])) ?? 0,
    logsPerMinute: toNumber(pick(raw, ['linesPerMinute', 'lines_per_minute', 'logsPerMinute', 'lpm'])),
    uptimeSeconds: toNumber(pick(raw, ['uptimeSeconds', 'uptime_seconds', 'uptime'])),
    logFile: pick(raw, ['logFile', 'log_file', 'file', 'path']) ?? null,
    notifier: pick(raw, ['notifier', 'notifications']) ?? null,
    series,
    recentErrors: Array.isArray(pick(raw, ['recentErrors', 'recent_errors'])) ? pick(raw, ['recentErrors', 'recent_errors']) : [],
    extra: raw,
  }
}

export function normalizeHealth(raw) {
  if (!raw || typeof raw !== 'object') return null
  return {
    status: String(pick(raw, ['status', 'state']) ?? 'unknown').toLowerCase(),
    version: pick(raw, ['version']) ?? null,
    uptimeSeconds: toNumber(pick(raw, ['uptimeSeconds', 'uptime_seconds', 'uptime'])),
    monitorRunning: pick(raw, ['monitorRunning', 'monitor_running']) ?? null,
    logFile: pick(raw, ['logFile', 'log_file']) ?? null,
  }
}

/** Accepts a bare array, {items}, {alerts} or {data}. */
export const normalizeAlertList = (payload) => {
  const list = Array.isArray(payload)
    ? payload
    : pick(payload ?? {}, ['items', 'alerts', 'data', 'results', 'results'])
  if (!Array.isArray(list)) return []
  return list.map(normalizeAlert).filter(Boolean).sort((a, b) => b.timestamp - a.timestamp)
}

/* ------------------------------------------------------------------ *
 * 4. Public REST API
 * ------------------------------------------------------------------ */

export async function getHealth() {
  return normalizeHealth(await request(ENDPOINTS.health, { timeout: 5000 }))
}

export async function getMetrics() {
  return normalizeMetrics(await request(ENDPOINTS.metrics, { timeout: 8000 }))
}

export async function getAlerts({ limit = 200, severity, service, status } = {}) {
  const params = new URLSearchParams()
  if (limit) params.set('limit', String(limit))
  if (severity && severity !== 'ALL') params.set('severity', severity)
  if (status) params.set('status', status)
  const query = params.toString()
  const path = query ? `${ENDPOINTS.alerts[0]}?${query}` : ENDPOINTS.alerts[0]
  return normalizeAlertList(await request(path, { timeout: 8000 }))
}

export async function getAlertById(id) {
  if (!id) return null
  return normalizeAlert(await request(ENDPOINTS.alertById(id), { timeout: 6000 }))
}

/**
 * Ask the BACKEND to publish a test notification (e.g. to AWS SNS).
 * The browser never holds AWS credentials - it only triggers the call.
 * Never throws: notification problems must not break the dashboard.
 */
export async function sendTestNotification() {
  try {
    const result = await request(ENDPOINTS.testNotification, { method: 'POST', timeout: 10000 })
    return { ok: true, result }
  } catch (error) {
    return { ok: false, error: error.message }
  }
}

/* ------------------------------------------------------------------ *
 * 5. Demo / mock data
 * ------------------------------------------------------------------ */

/** Build a complete, believable backend payload used in demo mode. */
export function createDemoSnapshot() {
  const now = Date.now()
  const trend = []
  let rate = 4.5
  for (let i = 29; i >= 0; i -= 1) {
    const spikeAt = 12 - i
    if (spikeAt >= 0 && spikeAt < 6) rate = 18 + Math.random() * 14
    else rate = Math.max(1.2, 4.2 + Math.sin((spikeAt / 6) * Math.PI) * 3 + (Math.random() - 0.5) * 1.6)
    trend.push({
      windowStart: now - (i + 1) * 20000,
      windowEnd: now - i * 20000,
      total: 240 + Math.round(Math.random() * 90),
      errors: Math.round((rate / 100) * 260),
      errorRate: Math.round(rate * 1e6) / 1e6,
    })
  }

  const metrics = normalizeMetrics({
    status: 'ANOMALY',
    uptimeSeconds: 1840,
    totalLogs: 184520,
    totalErrors: 9640,
    totalWarnings: 3120,
    linesPerMinute: 1180,
    errorRate: 0.312,
    baselineErrorRate: 0.048,
    thresholdErrorRate: 0.061,
    deviation: 0.264,
    deviationSigma: 7.4,
    activeAnomalies: 3,
    totalAlerts: 6,
    logFile: '/var/log/payments/app.log',
    notifier: { mode: 'demo', provider: 'local' },
    trend,
  })

  return { health: normalizeHealth({ status: 'ok', version: '1.0.0-demo', uptimeSeconds: 1840 }), metrics }
}

const DEMO_ALERTS = [
  {
    id: 'alert-demo-0001', timestamp: Date.now() - 4000, severity: 'CRITICAL', status: 'ACTIVE',
    service: 'payment-service', errorRate: 31.2, baseline: 4.8, deviation: 26.4,
    message: 'Error rate significantly exceeds baseline',
    reason: 'Error rate is 7.4 sigma above the rolling baseline (threshold 6.1%)',
    deviationSigma: 7.4, thresholdRate: 6.1, sampleSize: 268, errorCount: 84, windowSeconds: 20,
  },
  {
    id: 'alert-demo-0002', timestamp: Date.now() - 42000, severity: 'HIGH', status: 'ACTIVE',
    service: 'auth-service', errorRate: 17.6, baseline: 3.1, deviation: 14.5,
    message: 'Sustained authentication failures above baseline',
    reason: 'Error rate 17.6% over 3 consecutive windows (baseline 3.1%)',
    deviationSigma: 5.1, thresholdRate: 5.4, sampleSize: 310, errorCount: 55, windowSeconds: 20,
  },
  {
    id: 'alert-demo-0003', timestamp: Date.now() - 95000, severity: 'MEDIUM', status: 'ACTIVE',
    service: 'database-service', errorRate: 11.9, baseline: 4.2, deviation: 7.7,
    message: 'Connection pool timeouts detected',
    reason: 'Error rate 11.9% is 2.9x the alert threshold',
    deviationSigma: 3.6, thresholdRate: 5.4, sampleSize: 244, errorCount: 29, windowSeconds: 20,
  },
  {
    id: 'alert-demo-0004', timestamp: Date.now() - 180000, severity: 'LOW', status: 'RECOVERED',
    service: 'checkout-service', errorRate: 8.4, baseline: 4.6, deviation: 3.8,
    message: 'Short error burst recovered on its own',
    reason: 'Error rate peaked at 8.4% then returned to baseline',
    deviationSigma: 2.1, thresholdRate: 5.4, sampleSize: 198, errorCount: 17, windowSeconds: 20,
  },
  {
    id: 'alert-demo-0005', timestamp: Date.now() - 320000, severity: 'MEDIUM', status: 'RECOVERED',
    service: 'notification-service', errorRate: 9.7, baseline: 2.9, deviation: 6.8,
    message: 'Delivery webhook retries spiking',
    reason: 'Error rate 9.7% is 3.3x the alert threshold',
    deviationSigma: 3.2, thresholdRate: 5.4, sampleSize: 176, errorCount: 17, windowSeconds: 20,
  },
  {
    id: 'alert-demo-0006', timestamp: Date.now() - 640000, severity: 'LOW', status: 'RECOVERED',
    service: 'payment-service', errorRate: 7.1, baseline: 4.8, deviation: 2.3,
    message: 'Minor latency-driven error increase',
    reason: 'Error rate 7.1% briefly above the 5.4% threshold',
    deviationSigma: 2.0, thresholdRate: 5.4, sampleSize: 205, errorCount: 15, windowSeconds: 20,
  },
]

export const getDemoAlerts = () => normalizeAlertList(DEMO_ALERTS)

/** One new synthetic alert, used by the demo WebSocket stream. */
export function createDemoAlert(index = 0) {
  const catalogue = [
    { severity: 'CRITICAL', service: 'payment-service', errorRate: 31.2, baseline: 4.8, message: 'Error rate significantly exceeds baseline' },
    { severity: 'HIGH', service: 'auth-service', errorRate: 19.4, baseline: 3.1, message: 'Authentication failures above baseline' },
    { severity: 'MEDIUM', service: 'database-service', errorRate: 12.1, baseline: 4.2, message: 'Connection pool timeouts detected' },
    { severity: 'LOW', service: 'notification-service', errorRate: 7.8, baseline: 2.9, message: 'Delivery webhook retries spiking' },
    { severity: 'HIGH', service: 'checkout-service', errorRate: 21.7, baseline: 4.6, message: 'Payment capture timeouts' },
  ]
  const pick = catalogue[index % catalogue.length]
  const jitter = (value) => Math.round(value * (0.9 + Math.random() * 0.25) * 100) / 100
  const errorRate = jitter(pick.errorRate)
  const baseline = jitter(pick.baseline)
  return normalizeAlert({
    id: `alert-demo-live-${Date.now()}-${index}`,
    timestamp: Date.now(),
    severity: pick.severity,
    status: 'ACTIVE',
    service: pick.service,
    errorRate,
    baseline,
    deviation: Math.round((errorRate - baseline) * 100) / 100,
    message: pick.message,
    reason: `${pick.message} (demo stream)`,
    deviationSigma: Math.round((errorRate / Math.max(baseline, 0.1)) * 10) / 10,
    thresholdRate: 5.4,
    sampleSize: 180 + Math.round(Math.random() * 140),
    errorCount: Math.round((errorRate / 100) * 240),
    windowSeconds: 20,
  })
}

/* ------------------------------------------------------------------ *
 * 6. Display formatters (shared so every screen formats identically)
 * ------------------------------------------------------------------ */

export const formatPercent = (value, digits = 1) =>
  typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(digits)}%` : '--'

export const formatNumber = (value) =>
  typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : '--'

export const formatTime = (value) => {
  const ms = toEpochMs(value)
  return new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export const formatDateTime = (value) => {
  const ms = toEpochMs(value)
  const date = new Date(ms)
  return `${date.toLocaleDateString([], { day: '2-digit', month: 'short' })} ${formatTime(ms)}`
}

export const formatRelative = (value) => {
  const delta = Math.round((Date.now() - toEpochMs(value)) / 1000)
  if (delta < 5) return 'just now'
  if (delta < 60) return `${delta}s ago`
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`
  return `${Math.floor(delta / 86400)}d ago`
}

export const formatUptime = (seconds) => {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '--'
  const total = Math.max(0, Math.floor(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}
