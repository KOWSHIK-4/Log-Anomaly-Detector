/**
 * hooks/useWebSocket.js
 * ---------------------------------------------------------------------------
 * The single live-data hook used by the whole app.
 *
 * Boot sequence
 *   1. Try the REST API (health + metrics + alerts) in parallel.
 *   2. If the backend answered -> open a real WebSocket and keep a slow REST
 *      poll running as a safety net.
 *   3. If the backend is unreachable (or VITE_DEMO_MODE=always) -> start the
 *      demo stream, which emits the same message shapes.
 *   4. If a live socket cannot connect within DEMO_FALLBACK_MS, degrade to
 *      the demo stream so the dashboard is never dead on stage.
 *
 * Guarantees
 *   - never throws, never blocks the first paint
 *   - malformed frames are dropped (see parseSocketMessage)
 *   - full cleanup on unmount: sockets, timers and pending requests
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  MAX_ALERTS,
  MAX_CHART_POINTS,
  createDemoSnapshot,
  getAlerts,
  getDemoAlerts,
  getHealth,
  getMetrics,
  isDemoDisabled,
  isDemoForced,
  normalizeAlert,
  normalizeMetrics,
} from '../services/api.js'
import { CONNECTION_STATES, DemoSocket, LiveSocket } from '../services/websocket.js'

/** How long a live socket may stay down before demo data takes over. */
const DEMO_FALLBACK_MS = 8000
/** Safety-net REST refresh when the socket is not connected. */
const POLL_INTERVAL_MS = 5000

const appendChartPoint = (series, alert, threshold) => {
  if (typeof alert.errorRate !== 'number') return series
  const point = {
    t: alert.timestamp,
    errorRate: alert.errorRate,
    baseline: alert.baseline,
    threshold,
    total: alert.sampleSize ?? null,
    errors: alert.errorCount ?? null,
  }
  const last = series[series.length - 1]
  if (last && Math.abs(last.t - point.t) < 1000) return [...series.slice(0, -1), point]
  return [...series, point].slice(-MAX_CHART_POINTS)
}

const severityStatus = (severity, current) => {
  if (severity === 'CRITICAL' || severity === 'HIGH') return 'ANOMALY'
  if (severity === 'MEDIUM') return current === 'ANOMALY' ? 'ANOMALY' : 'DEGRADED'
  return current === 'ANOMALY' ? 'ANOMALY' : current === 'DEGRADED' ? 'DEGRADED' : 'HEALTHY'
}

export function useWebSocket() {
  const [metrics, setMetrics] = useState(null)
  const [health, setHealth] = useState(null)
  const [alerts, setAlerts] = useState([])
  const [status, setStatus] = useState(CONNECTION_STATES.CONNECTING)
  const [source, setSource] = useState('live') // 'live' | 'demo'
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [reconnectAttempt, setReconnectAttempt] = useState(0)
  const [lastEventAt, setLastEventAt] = useState(null)

  const socketRef = useRef(null)
  const pollRef = useRef(null)
  const fallbackRef = useRef(null)
  const unsubscribersRef = useRef([])
  /** Latest metrics, readable from timers without re-creating callbacks. */
  const metricsRef = useRef(null)

  useEffect(() => {
    metricsRef.current = metrics
  }, [metrics])

  /* ---------------- message handling ---------------- */

  const handleMetrics = useCallback((raw) => {
    const next = normalizeMetrics(raw)
    if (!next) return // malformed snapshot: keep whatever we already show
    setMetrics(next)
    setError(null)
    setLastEventAt(Date.now())
  }, [])

  const handleAlert = useCallback((raw) => {
    const alert = normalizeAlert(raw)
    if (!alert) return
    setLastEventAt(Date.now())

    setAlerts((previous) => {
      if (previous.some((item) => item.id === alert.id)) return previous
      return [alert, ...previous].slice(0, MAX_ALERTS)
    })

    setMetrics((previous) => {
      if (!previous) return previous
      return {
        ...previous,
        anomalyCount: (previous.anomalyCount ?? 0) + 1,
        totalAlerts: (previous.totalAlerts ?? 0) + 1,
        currentErrorRate: alert.errorRate ?? previous.currentErrorRate,
        status: severityStatus(alert.severity, previous.status),
        series: appendChartPoint(previous.series, alert, previous.thresholdErrorRate),
      }
    })
  }, [])

  const handleMessage = useCallback(
    (message) => {
      if (!message) return
      if (message.type === 'alert') handleAlert(message.data)
      else if (message.type === 'metrics') handleMetrics(message.data)
      // 'ping' / 'pong' are handled inside the socket; ignore here.
    },
    [handleAlert, handleMetrics],
  )

  /* ---------------- socket lifecycle ---------------- */

  const stopSocket = useCallback(() => {
    unsubscribersRef.current.forEach((unsubscribe) => unsubscribe())
    unsubscribersRef.current = []
    socketRef.current?.stop()
    socketRef.current = null
    clearInterval(pollRef.current)
    clearTimeout(fallbackRef.current)
    pollRef.current = null
    fallbackRef.current = null
  }, [])

  const startDemo = useCallback(
    (base) => {
      stopSocket()
      const socket = new DemoSocket()
      socketRef.current = socket
      setSource('demo')
      setError(null)
      // Seed the demo stream with whatever is already on screen so the chart
      // continues from the current point instead of jumping.
      socket.start({ snapshot: base ?? createDemoSnapshot().metrics })
      const unsubscribers = [
        socket.on('message', handleMessage),
        socket.on('status', setStatus),
      ]
      unsubscribersRef.current = unsubscribers
    },
    [handleMessage, stopSocket],
  )

  const startLive = useCallback(() => {
    stopSocket()
    const socket = new LiveSocket()
    socketRef.current = socket
    setSource('live')

    const unsubscribers = [
      socket.on('message', handleMessage),
      socket.on('status', setStatus),
      socket.on('retry', ({ attempt }) => setReconnectAttempt(attempt)),
      socket.on('error', () => setError('Live connection lost - retrying')),
    ]
    unsubscribersRef.current = unsubscribers
    socket.start()

    // Safety net: if the socket never opens, keep REST data fresh; if it is
    // still down after DEMO_FALLBACK_MS and demo data is allowed, take over.
    pollRef.current = setInterval(async () => {
      if (socket.status === CONNECTION_STATES.CONNECTED) return
      try {
        const [nextMetrics, nextAlerts] = await Promise.allSettled([getMetrics(), getAlerts({ limit: 200 })])
        if (nextMetrics.status === 'fulfilled' && nextMetrics.value) setMetrics(nextMetrics.value)
        if (nextAlerts.status === 'fulfilled') setAlerts(nextAlerts.value)
      } catch {
        /* backend down - the demo fallback will take over */
      }
    }, POLL_INTERVAL_MS)

    if (!isDemoDisabled()) {
      fallbackRef.current = setTimeout(() => {
        if (socket.status !== CONNECTION_STATES.CONNECTED) {
          setError('Backend WebSocket unavailable - showing demo stream')
          // Actually hand over to the demo stream (it stops the live socket).
          startDemo(metricsRef.current ?? createDemoSnapshot().metrics)
        }
      }, DEMO_FALLBACK_MS)
    }
  }, [handleMessage, startDemo, stopSocket])

  /* ---------------- boot ---------------- */

  useEffect(() => {
    let cancelled = false

    const boot = async () => {
      const demoSnapshot = createDemoSnapshot()

      if (isDemoDisabled()) {
        startLive()
        setLoading(false)
        return
      }

      // VITE_DEMO_MODE=always: never touch the backend.
      if (isDemoForced()) {
        setHealth(demoSnapshot.health)
        setMetrics(demoSnapshot.metrics)
        setAlerts(getDemoAlerts())
        setSource('demo')
        setError(null)
        startDemo(demoSnapshot.metrics)
        setLoading(false)
        return
      }

      // REST first: if the backend is up we use real data everywhere.
      const [healthResult, metricsResult, alertsResult] = await Promise.allSettled([
        getHealth(),
        getMetrics(),
        getAlerts({ limit: 200 }),
      ])
      if (cancelled) return

      const backendUp = metricsResult.status === 'fulfilled' && Boolean(metricsResult.value)
      const health = healthResult.status === 'fulfilled' ? healthResult.value : null

      if (backendUp) {
        if (health) setHealth(health)
        setMetrics(metricsResult.value)
        setAlerts(alertsResult.status === 'fulfilled' ? alertsResult.value : [])
        setSource('live')
        setError(null)
        startLive()
      } else {
        setHealth(demoSnapshot.health)
        setMetrics(demoSnapshot.metrics)
        setAlerts(getDemoAlerts())
        setError('Backend offline - running on demo data')
        startLive() // will degrade to the demo stream via the fallback timer
      }

      setLoading(false)
    }

    boot().catch(() => {
      if (cancelled) return
      setError('Could not reach the backend - running on demo data')
      setMetrics(createDemoSnapshot().metrics)
      setAlerts(getDemoAlerts())
      startDemo()
      setLoading(false)
    })

    return () => {
      cancelled = true
      stopSocket()
    }
  }, [startDemo, startLive, stopSocket])

  /** Manual refresh (used by the "Reconnect" button in the navbar). */
  const reload = useCallback(async () => {
    setError(null)
    const [metricsResult, alertsResult] = await Promise.allSettled([getMetrics(), getAlerts({ limit: 200 })])
    if (metricsResult.status === 'fulfilled' && metricsResult.value) setMetrics(metricsResult.value)
    if (alertsResult.status === 'fulfilled') setAlerts(alertsResult.value)
    if (metricsResult.status === 'rejected' && alertsResult.status === 'rejected') {
      setError('Backend still unreachable - keeping demo data')
    }
  }, [])

  return {
    metrics,
    health,
    alerts,
    status,
    source,
    loading,
    error,
    reconnectAttempt,
    lastEventAt,
    isConnected: status === CONNECTION_STATES.CONNECTED,
    reload,
  }
}

export { CONNECTION_STATES }
