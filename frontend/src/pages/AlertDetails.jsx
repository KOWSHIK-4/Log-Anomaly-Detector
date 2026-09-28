import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import SeverityBadge from '../components/SeverityBadge.jsx'
import {
  formatDateTime,
  formatNumber,
  formatPercent,
  formatRelative,
  formatUptime,
  getAlertById,
} from '../services/api.js'

/** Render any backend value safely, including nested structures. */
function renderValue(value) {
  if (value === null || value === undefined || value === '') return '--'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return value.toLocaleString()
  if (Array.isArray(value)) {
    if (value.length === 0) return '--'
    return value
      .map((item) => (typeof item === 'object' ? Object.values(item).filter(Boolean).join(' · ') : String(item)))
      .join(' | ')
  }
  if (typeof value === 'object') return Object.values(value).filter((item) => item !== null).join(' · ') || '--'
  return String(value)
}

function DetailItem({ label, value, mono = false, tone = '' }) {
  return (
    <div className="detail-item">
      <span className="detail-label">{label}</span>
      <span className={`detail-value ${mono ? 'mono' : ''} ${tone ? `tone-text-${tone}` : ''}`}>{value}</span>
    </div>
  )
}

export default function AlertDetails({ data }) {
  const { id } = useParams()
  const { alerts } = data

  const fromFeed = useMemo(() => alerts.find((alert) => alert.id === id) ?? null, [alerts, id])
  const [alert, setAlert] = useState(fromFeed)
  const [loading, setLoading] = useState(!fromFeed)
  const [error, setError] = useState(null)

  useEffect(() => {
    setAlert(fromFeed)
    setError(null)
    if (fromFeed) {
      setLoading(false)
      return undefined
    }
    // Not in the live feed (e.g. a deep link) - ask the backend for it.
    let cancelled = false
    setLoading(true)
    getAlertById(id)
      .then((result) => {
        if (cancelled) return
        if (!result) setError('not-found')
      })
      .catch(() => {
        if (!cancelled) setError('unreachable')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [id, fromFeed])

  if (loading) {
    return (
      <div className="page">
        <div className="feed-empty">
          <span className="spinner" />
          <p>Loading alert {id}…</p>
        </div>
      </div>
    )
  }

  if (!alert) {
    return (
      <div className="page">
        <Link to="/alerts" className="back-link">
          ← Back to alerts
        </Link>
        <div className="feed-empty">
          <div className="feed-empty-icon" aria-hidden="true">
            ?
          </div>
          <h3>Alert not found</h3>
          <p>
            {error === 'unreachable'
              ? 'The backend could not be reached and this alert is not in the local feed.'
              : `No alert exists with the id "${id}".`}
          </p>
        </div>
      </div>
    )
  }

  // Everything the backend sent, minus our own wrapper key.
  const isEmptyValue = (value) =>
    value === null || value === undefined || value === '' || (Array.isArray(value) && value.length === 0)
  const extraEntries = Object.entries(alert.extra ?? {}).filter(
    ([key, value]) => key !== 'extra' && !isEmptyValue(value),
  )

  return (
    <div className="page">
      <Link to="/alerts" className="back-link">
        ← Back to alerts
      </Link>

      <section className={`panel detail-hero sev-border-${alert.severity}`}>
        <div className="detail-hero-head">
          <div>
            <div className="detail-hero-badges">
              <SeverityBadge severity={alert.severity} />
              <span className={`status-tag ${alert.status === 'RECOVERED' ? 'status-recovered' : 'status-active'}`}>
                {alert.status}
              </span>
            </div>
            <h1 className="detail-service">{alert.service}</h1>
            <p className="page-sub">
              {formatDateTime(alert.timestamp)} · {formatRelative(alert.timestamp)}
            </p>
          </div>
          <div className="detail-id">
            <span>Alert ID</span>
            <code>{alert.id}</code>
          </div>
        </div>

        <div className="detail-grid">
          <DetailItem label="Current Error Rate" value={formatPercent(alert.errorRate)} tone="bad" mono />
          <DetailItem label="Baseline Error Rate" value={formatPercent(alert.baseline)} tone="ok" mono />
          <DetailItem
            label="Deviation"
            value={alert.deviation === null || alert.deviation === undefined ? '--' : `+${formatPercent(alert.deviation)}`}
            tone="warn"
            mono
          />
          <DetailItem
            label="Deviation (sigma)"
            value={alert.deviationSigma === null || alert.deviationSigma === undefined ? '--' : `${alert.deviationSigma.toFixed(2)}σ`}
            mono
          />
          <DetailItem label="Alert Threshold" value={formatPercent(alert.thresholdRate)} mono />
          <DetailItem label="Samples in Window" value={formatNumber(alert.sampleSize)} mono />
          <DetailItem label="Errors in Window" value={formatNumber(alert.errorCount)} mono />
          <DetailItem label="Window Length" value={alert.windowSeconds ? formatUptime(alert.windowSeconds) : '--'} mono />
        </div>
      </section>

      <div className="detail-cols">
        <section className="panel">
          <div className="panel-head">
            <h2>Reason</h2>
          </div>
          <p className="detail-reason">{alert.reason}</p>
          {alert.message && alert.message !== alert.reason ? (
            <>
              <div className="panel-head">
                <h2>Message</h2>
              </div>
              <p className="detail-reason">{alert.message}</p>
            </>
          ) : null}
          {alert.messageSample ? (
            <>
              <div className="panel-head">
                <h2>Log Sample</h2>
              </div>
              <pre className="log-sample">{alert.messageSample}</pre>
            </>
          ) : null}
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>All Fields</h2>
            <span className="muted small">as received from the backend</span>
          </div>
          <dl className="extra-list">
            {extraEntries.length === 0 ? (
              <p className="panel-note">This alert carries no additional fields.</p>
            ) : (
              extraEntries.map(([key, value]) => (
                <div className="extra-row" key={key}>
                  <dt>{key}</dt>
                  <dd className="mono">{renderValue(value)}</dd>
                </div>
              ))
            )}
          </dl>
        </section>
      </div>

      <p className="detail-note">
        Notifications are published by the backend (e.g. AWS SNS). The browser only requests them over the REST API and
        never holds AWS credentials.
      </p>
    </div>
  )
}
