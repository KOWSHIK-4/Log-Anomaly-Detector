import { useEffect, useRef, useState } from 'react'
import AlertCard from './AlertCard.jsx'

/** How long a freshly arrived alert keeps its "just came in" highlight. */
const HIGHLIGHT_MS = 7000

/**
 * AlertFeed - newest alerts first, live over WebSocket.
 * Never requires a refresh: the list re-renders as new alerts arrive.
 */
export default function AlertFeed({ alerts = [], loading = false, dense = false, maxHeight }) {
  const [highlightId, setHighlightId] = useState(null)
  const newestIdRef = useRef(null)
  const timerRef = useRef(null)

  useEffect(() => {
    const newest = alerts[0]
    if (!newest) return
    if (newestIdRef.current === null) {
      newestIdRef.current = newest.id
      return // first render: not a new arrival
    }
    if (newest.id === newestIdRef.current) return
    newestIdRef.current = newest.id
    setHighlightId(newest.id)
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => setHighlightId(null), HIGHLIGHT_MS)
  }, [alerts])

  useEffect(() => () => clearTimeout(timerRef.current), [])

  if (loading && alerts.length === 0) {
    return (
      <div className="feed-empty">
        <span className="spinner" />
        <p>Loading alerts…</p>
      </div>
    )
  }

  if (alerts.length === 0) {
    return (
      <div className="feed-empty">
        <div className="feed-empty-icon" aria-hidden="true">
          ✓
        </div>
        <h3>No anomalies detected</h3>
        <p>The error rate is tracking its baseline. Alerts will appear here the moment one fires.</p>
      </div>
    )
  }

  return (
    <>
      <div className="feed" style={maxHeight ? { maxHeight } : undefined}>
        {alerts.map((alert) => (
          <AlertCard key={alert.id} alert={alert} isNew={alert.id === highlightId} dense={dense} />
        ))}
      </div>
      <p className="feed-foot">Showing {alerts.length} alert{alerts.length === 1 ? '' : 's'} · newest first</p>
    </>
  )
}
