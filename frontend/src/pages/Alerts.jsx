import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import ServiceFilter from '../components/ServiceFilter.jsx'
import SeverityBadge from '../components/SeverityBadge.jsx'
import { SEVERITIES, formatDateTime, formatPercent, sendTestNotification } from '../services/api.js'

const ALL = 'ALL'

/**
 * Alerts - every alert the backend has stored, filterable by service and
 * severity. Selecting a row opens the AlertDetails page.
 */
export default function Alerts({ data }) {
  const { alerts, loading, source, reload } = data
  const navigate = useNavigate()

  const [service, setService] = useState(ALL)
  const [severity, setSeverity] = useState(ALL)
  const [notifying, setNotifying] = useState(false)
  const [notice, setNotice] = useState(null)

  const services = useMemo(() => Array.from(new Set(alerts.map((alert) => alert.service).filter(Boolean))), [alerts])
  const serviceCounts = useMemo(() => {
    const tally = {}
    alerts.forEach((alert) => {
      tally[alert.service] = (tally[alert.service] ?? 0) + 1
    })
    return tally
  }, [alerts])

  const severityCounts = useMemo(() => {
    const tally = { [ALL]: alerts.length }
    SEVERITIES.forEach((level) => {
      tally[level] = alerts.filter((alert) => alert.severity === level).length
    })
    return tally
  }, [alerts])

  const visible = useMemo(
    () =>
      alerts.filter(
        (alert) =>
          (service === ALL || alert.service === service) && (severity === ALL || alert.severity === severity),
      ),
    [alerts, service, severity],
  )

  useEffect(() => {
    if (!notice) return undefined
    const timer = setTimeout(() => setNotice(null), 6000)
    return () => clearTimeout(timer)
  }, [notice])

  const runTestNotification = async () => {
    setNotifying(true)
    setNotice(null)
    const result = await sendTestNotification()
    setNotifying(false)
    setNotice(
      result.ok
        ? { tone: 'ok', text: 'Test notification sent - the backend published it to its configured channel (e.g. AWS SNS).' }
        : { tone: 'bad', text: `Could not reach the notification endpoint${source === 'demo' ? ' (backend offline)' : ''}.` },
    )
  }

  return (
    <div className="page">
      <section className="page-head">
        <div>
          <h1>Alerts</h1>
          <p className="page-sub">
            {alerts.length} alert{alerts.length === 1 ? '' : 's'} recorded · {visible.length} matching the current filters
          </p>
        </div>
        <div className="page-actions">
          <button type="button" className="btn" onClick={runTestNotification} disabled={notifying}>
            {notifying ? 'Sending…' : 'Send test notification'}
          </button>
          <button type="button" className="btn btn-ghost" onClick={reload}>
            Refresh
          </button>
        </div>
      </section>

      {notice ? (
        <div className={`banner banner-${notice.tone === 'ok' ? 'ok' : 'bad'}`} role="status">
          {notice.text}
        </div>
      ) : null}

      <section className="panel">
        <div className="filter-bar">
          <div className="filter-group">
            <span className="filter-label">Service</span>
            <ServiceFilter services={services} value={service} onChange={setService} counts={serviceCounts} />
          </div>
          <div className="filter-group">
            <span className="filter-label">Severity</span>
            <div className="service-filter" role="group" aria-label="Filter alerts by severity">
              {[ALL, ...SEVERITIES].map((level) => (
                <button
                  type="button"
                  key={level}
                  className={`pill ${severity === level ? 'is-active' : ''} ${level !== ALL ? `pill-${level.toLowerCase()}` : ''}`}
                  aria-pressed={severity === level}
                  onClick={() => setSeverity(level)}
                >
                  {level === ALL ? 'All' : level}
                  <span className="pill-count">{severityCounts[level] ?? 0}</span>
                </button>
              ))}
            </div>
          </div>
        </div>

        {loading && alerts.length === 0 ? (
          <div className="feed-empty">
            <span className="spinner" />
            <p>Loading alerts…</p>
          </div>
        ) : visible.length === 0 ? (
          <div className="feed-empty">
            <div className="feed-empty-icon" aria-hidden="true">
              ✓
            </div>
            <h3>{alerts.length === 0 ? 'No alerts yet' : 'No alerts match these filters'}</h3>
            <p>
              {alerts.length === 0
                ? 'The detector has not raised an anomaly. This list stays empty while the error rate tracks its baseline.'
                : 'Try a different service or severity.'}
            </p>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="alerts-table">
              <thead>
                <tr>
                  <th>Severity</th>
                  <th>Service</th>
                  <th>Detected</th>
                  <th className="num">Error Rate</th>
                  <th className="num">Baseline</th>
                  <th className="num">Deviation</th>
                  <th>Reason</th>
                  <th aria-label="Open" />
                </tr>
              </thead>
              <tbody>
                {visible.map((alert) => (
                  <tr
                    key={alert.id}
                    className={`sev-row-bg sev-bg-${alert.severity}`}
                    tabIndex={0}
                    onClick={() => navigate(`/alerts/${encodeURIComponent(alert.id)}`)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        navigate(`/alerts/${encodeURIComponent(alert.id)}`)
                      }
                    }}
                  >
                    <td>
                      <SeverityBadge severity={alert.severity} size="sm" />
                    </td>
                    <td className="cell-service">{alert.service}</td>
                    <td className="cell-time">{formatDateTime(alert.timestamp)}</td>
                    <td className="num cell-bad">{formatPercent(alert.errorRate)}</td>
                    <td className="num">{formatPercent(alert.baseline)}</td>
                    <td className="num cell-warn">
                      {alert.deviation === null || alert.deviation === undefined ? '--' : `+${formatPercent(alert.deviation)}`}
                    </td>
                    <td className="cell-reason" title={alert.reason}>
                      {alert.reason}
                    </td>
                    <td className="cell-open" aria-hidden="true">
                      →
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
