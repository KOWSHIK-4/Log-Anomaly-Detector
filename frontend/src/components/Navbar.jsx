import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { CONNECTION_STATES } from '../services/websocket.js'
import { formatRelative } from '../services/api.js'

const LABELS = {
  [CONNECTION_STATES.CONNECTED]: 'Live',
  [CONNECTION_STATES.CONNECTING]: 'Connecting',
  [CONNECTION_STATES.DISCONNECTED]: 'Reconnecting',
}

/**
 * Navbar - product identity, routing and the live connection indicator.
 */
export default function Navbar({ status = CONNECTION_STATES.CONNECTING, source = 'live', lastEventAt, alertCount = 0 }) {
  const [, forceTick] = useState(0)

  // Keeps the "last event Xs ago" label fresh.
  useEffect(() => {
    const timer = setInterval(() => forceTick((n) => n + 1), 1000)
    return () => clearInterval(timer)
  }, [])

  const isConnected = status === CONNECTION_STATES.CONNECTED
  const stateClass = isConnected ? 'ok' : status === CONNECTION_STATES.CONNECTING ? 'pending' : 'down'

  return (
    <header className="navbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          ⬢
        </span>
        <div className="brand-text">
          <span className="brand-name">Log Anomaly Detector</span>
          <span className="brand-sub">Real-time observability &amp; alerting</span>
        </div>
      </div>

      <nav className="nav-links" aria-label="Main navigation">
        <NavLink to="/dashboard" className={({ isActive }) => `nav-link ${isActive ? 'is-active' : ''}`} end>
          Dashboard
        </NavLink>
        <NavLink to="/alerts" className={({ isActive }) => `nav-link ${isActive ? 'is-active' : ''}`}>
          Alerts
          {alertCount > 0 ? <span className="nav-count">{alertCount > 99 ? '99+' : alertCount}</span> : null}
        </NavLink>
      </nav>

      <div className="conn">
        {lastEventAt ? <span className="conn-last">updated {formatRelative(lastEventAt)}</span> : null}
        <span className={`conn-pill conn-${stateClass}`} title={`WebSocket ${status}`}>
          <span className="conn-dot" />
          {LABELS[status] ?? status}
        </span>
        <span className={`conn-pill conn-source ${source === 'demo' ? 'is-demo' : 'is-live'}`}>
          {source === 'demo' ? 'DEMO DATA' : 'LIVE DATA'}
        </span>
      </div>
    </header>
  )
}
