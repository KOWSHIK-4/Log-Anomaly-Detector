import { Link } from 'react-router-dom'
import SeverityBadge from './SeverityBadge.jsx'
import { formatPercent, formatRelative, formatTime } from '../services/api.js'

/**
 * AlertCard - one anomaly, scannable at a glance.
 * Clicking it opens the full details page.
 */
export default function AlertCard({ alert, isNew = false, dense = false }) {
  if (!alert) return null

  const { id, severity, service, timestamp, errorRate, baseline, deviation, message, status, deviationSigma } = alert
  const recovered = status === 'RECOVERED'

  return (
    <Link
      to={`/alerts/${encodeURIComponent(id)}`}
      className={`alert-card sev-border-${severity} ${isNew ? 'is-new' : ''} ${recovered ? 'is-recovered' : ''}`}
    >
      <div className="alert-head">
        <SeverityBadge severity={severity} size="sm" />
        <span className="alert-service" title={service}>
          {service}
        </span>
        {recovered ? <span className="status-tag status-recovered">RECOVERED</span> : null}
        <time className="alert-time" dateTime={new Date(timestamp).toISOString()} title={formatTime(timestamp)}>
          {formatTime(timestamp)} · {formatRelative(timestamp)}
        </time>
      </div>

      <div className="alert-metrics">
        <div className="alert-metric">
          <span className="alert-metric-label">Error Rate</span>
          <span className="alert-metric-value is-bad">{formatPercent(errorRate)}</span>
        </div>
        <div className="alert-metric">
          <span className="alert-metric-label">Baseline</span>
          <span className="alert-metric-value">{formatPercent(baseline)}</span>
        </div>
        <div className="alert-metric">
          <span className="alert-metric-label">Deviation</span>
          <span className="alert-metric-value is-warn">
            {deviation === null || deviation === undefined ? '--' : `+${formatPercent(deviation)}`}
          </span>
        </div>
        {!dense && deviationSigma !== null && deviationSigma !== undefined ? (
          <div className="alert-metric">
            <span className="alert-metric-label">Sigma</span>
            <span className="alert-metric-value">{deviationSigma.toFixed(1)}σ</span>
          </div>
        ) : null}
      </div>

      {message ? <p className="alert-reason">{message}</p> : null}
    </Link>
  )
}
