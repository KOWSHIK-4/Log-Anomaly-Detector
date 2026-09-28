/**
 * services/websocket.js
 * ---------------------------------------------------------------------------
 * WebSocket transport for live updates, plus a demo stream that mimics the
 * backend so the whole realtime path is exercised with no backend running.
 *
 * WebSocket contract (what the FastAPI backend should send):
 *   { "type": "metrics", "data": { ...metrics... } }   // every ~1s
 *   { "type": "alert",   "data": { ...alert...   } }   // immediately on anomaly
 *   { "type": "ping",    "data": { "at": 1234 } }      // optional heartbeat
 *   -> client replies with the plain text "ping" and receives "pong".
 *
 * `type` is optional: a bare object that looks like an alert is treated as an
 * alert, a bare object that looks like a metrics snapshot is treated as
 * metrics. Anything unrecognised is dropped instead of crashing the app.
 *
 * Connection states: CONNECTING | CONNECTED | DISCONNECTED
 */

/** Never hard-coded: env var first, otherwise derived from the page origin. */
export function getWebSocketUrl() {
  const configured = import.meta.env.VITE_WS_URL
  if (configured) return configured

  const apiBase = String(import.meta.env.VITE_API_URL ?? import.meta.env.VITE_API_BASE ?? '').replace(/\/+$/, '')
  if (apiBase) return `${apiBase.replace(/^http/i, 'ws')}/ws`

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws`
}

import { createDemoAlert } from './api.js'

export const CONNECTION_STATES = {
  CONNECTING: 'CONNECTING',
  CONNECTED: 'CONNECTED',
  DISCONNECTED: 'DISCONNECTED',
}

/** 1s, 2s, 4s, 8s, ... capped at 15s, with a little jitter. */
const backoffDelay = (attempt) => Math.min(1000 * 2 ** attempt, 15000) + Math.random() * 400

const looksLikeAlert = (payload) =>
  payload && typeof payload === 'object' && (payload.severity || payload.id?.toString().startsWith?.('alert'))

const looksLikeMetrics = (payload) =>
  payload &&
  typeof payload === 'object' &&
  ('totalLogs' in payload || 'errorRate' in payload || 'activeAnomalies' in payload || 'status' in payload)

/**
 * Turn any raw socket payload into `{ type, data }`, or null when it cannot
 * be understood. Never throws - a malformed frame is simply ignored.
 */
export function parseSocketMessage(raw) {
  let payload = raw
  if (typeof payload === 'string') {
    try {
      payload = JSON.parse(payload)
    } catch {
      return null
    }
  }
  if (!payload || typeof payload !== 'object') return null

  const type = String(payload.type ?? payload.event ?? '').toLowerCase()
  if (type.includes('alert') || type.includes('anomaly')) return { type: 'alert', data: payload.data ?? payload.payload ?? payload }
  if (type.includes('metric') || type.includes('stats') || type.includes('snapshot')) return { type: 'metrics', data: payload.data ?? payload.payload ?? payload }
  if (type === 'ping' || type === 'pong') return { type, data: payload.data ?? payload }

  // No usable type field: sniff the shape.
  if (looksLikeAlert(payload)) return { type: 'alert', data: payload }
  if (looksLikeMetrics(payload)) return { type: 'metrics', data: payload }
  return null
}

/** Tiny event emitter shared by the real socket and the demo stream. */
class Emitter {
  #listeners = new Map()

  on(event, handler) {
    if (!this.#listeners.has(event)) this.#listeners.set(event, new Set())
    this.#listeners.get(event).add(handler)
    return () => this.off(event, handler)
  }

  off(event, handler) {
    this.#listeners.get(event)?.delete(handler)
  }

  emit(event, ...args) {
    for (const handler of this.#listeners.get(event) ?? []) {
      try {
        handler(...args)
      } catch {
        /* one bad listener must not break the stream */
      }
    }
  }
}

/* ------------------------------------------------------------------ *
 * LiveSocket - real WebSocket with automatic reconnection
 * ------------------------------------------------------------------ */
export class LiveSocket extends Emitter {
  constructor({ url = getWebSocketUrl(), pingInterval = 20000 } = {}) {
    super()
    this.url = url
    this.pingInterval = pingInterval
    this.status = CONNECTION_STATES.DISCONNECTED
    this.socket = null
    this.attempt = 0
    this.stopped = true
    this.retryTimer = null
    this.pingTimer = null
  }

  #setStatus(status) {
    if (this.status === status) return
    this.status = status
    this.emit('status', status)
  }

  start() {
    this.stopped = false
    this.#connect()
  }

  stop() {
    this.stopped = true
    clearTimeout(this.retryTimer)
    clearInterval(this.pingTimer)
    this.retryTimer = null
    this.pingTimer = null
    const socket = this.socket
    this.socket = null
    if (socket) {
      socket.onopen = socket.onclose = socket.onerror = socket.onmessage = null
      try {
        socket.close()
      } catch {
        /* already closing */
      }
    }
    this.#setStatus(CONNECTION_STATES.DISCONNECTED)
  }

  send(data) {
    try {
      if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(data)
    } catch {
      /* socket died between the check and the send */
    }
  }

  #connect() {
    if (this.stopped) return
    this.#setStatus(CONNECTION_STATES.CONNECTING)

    let socket
    try {
      socket = new WebSocket(this.url)
    } catch (error) {
      this.emit('error', error)
      this.#scheduleReconnect()
      return
    }
    this.socket = socket

    socket.onopen = () => {
      this.attempt = 0
      this.#setStatus(CONNECTION_STATES.CONNECTED)
      clearInterval(this.pingTimer)
      this.pingTimer = setInterval(() => this.send('ping'), this.pingInterval)
    }

    socket.onmessage = (event) => {
      const message = parseSocketMessage(event.data)
      if (!message) return // malformed frame: ignore, keep the socket alive
      if (message.type === 'ping') {
        this.send('ping')
        return
      }
      this.emit('message', message)
    }

    socket.onerror = () => {
      // onclose always follows; reconnection is handled there.
      this.emit('error', new Error(`WebSocket error on ${this.url}`))
    }

    socket.onclose = () => {
      clearInterval(this.pingTimer)
      this.socket = null
      this.#setStatus(CONNECTION_STATES.DISCONNECTED)
      this.#scheduleReconnect()
    }
  }

  #scheduleReconnect() {
    if (this.stopped) return
    const delay = backoffDelay(this.attempt)
    this.attempt += 1
    clearTimeout(this.retryTimer)
    this.retryTimer = setTimeout(() => this.#connect(), delay)
    this.emit('retry', { attempt: this.attempt, delay: Math.round(delay) })
  }
}

/* ------------------------------------------------------------------ *
 * DemoSocket - fake backend emitting the exact same message shapes
 * ------------------------------------------------------------------ */
export class DemoSocket extends Emitter {
  constructor({ metricsInterval = 1500, alertInterval = 11000 } = {}) {
    super()
    this.url = 'demo://local-mock'
    this.status = CONNECTION_STATES.DISCONNECTED
    this.stopped = true
    this.metricsInterval = metricsInterval
    this.alertInterval = alertInterval
    this.timers = []
    this.counter = 0
    this.state = null
  }

  #setStatus(status) {
    if (this.status === status) return
    this.status = status
    this.emit('status', status)
  }

  start({ snapshot } = {}) {
    this.stopped = false
    this.state = snapshot ?? null
    this.#setStatus(CONNECTION_STATES.CONNECTING)
    // A short delay makes the CONNECTING state visible, like a real handshake.
    this.timers.push(setTimeout(() => this.#begin(), 450))
  }

  #begin() {
    if (this.stopped) return
    this.#setStatus(CONNECTION_STATES.CONNECTED)
    this.emit('message', { type: 'metrics', data: this.state })
    this.timers.push(setInterval(() => this.emit('message', { type: 'metrics', data: this.#tick() }), this.metricsInterval))
    this.timers.push(setInterval(() => this.emit('message', { type: 'alert', data: this.#nextAlert() }), this.alertInterval))
  }

  stop() {
    this.stopped = true
    this.timers.forEach((timer) => {
      clearTimeout(timer)
      clearInterval(timer)
    })
    this.timers = []
    this.#setStatus(CONNECTION_STATES.DISCONNECTED)
  }

  send() {
    /* the demo backend needs no client input */
  }

  /** Advance the synthetic metrics: totals grow, the rate drifts. */
  #tick() {
    if (!this.state) return this.state
    const drift = (Math.random() - 0.42) * 3.2
    const current = Math.max(1.1, Math.min(42, this.state.currentErrorRate + drift))
    const logs = 220 + Math.round(Math.random() * 120)
    const errors = Math.round((current / 100) * logs)

    const point = {
      t: Date.now(),
      errorRate: Math.round(current * 100) / 100,
      baseline: this.state.baselineErrorRate,
      threshold: this.state.thresholdErrorRate,
      total: logs,
      errors,
    }
    const series = [...this.state.series, point].slice(-60)

    this.state = {
      ...this.state,
      currentErrorRate: point.errorRate,
      totalLogs: this.state.totalLogs + logs,
      totalErrors: this.state.totalErrors + errors,
      series,
    }
    return this.state
  }

  /** Create the next synthetic alert and reflect it in the metrics. */
  #nextAlert() {
    if (!this.state) return null
    this.counter += 1
    const alert = createDemoAlert(this.counter - 1)
    if (!alert) return null

    this.state = {
      ...this.state,
      anomalyCount: this.state.anomalyCount + 1,
      totalAlerts: this.state.totalAlerts + 1,
      status: alert.severity === 'CRITICAL' || alert.severity === 'HIGH' ? 'ANOMALY' : 'DEGRADED',
      currentErrorRate: alert.errorRate,
    }
    return alert
  }
}
