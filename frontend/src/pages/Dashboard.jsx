import { useMemo, useState } from 'react'
import AlertFeed from '../components/AlertFeed.jsx'
import ErrorRateChart from '../components/ErrorRateChart.jsx'
import MetricCard from '../components/MetricCard.jsx'
import ServiceFilter from '../components/ServiceFilter.jsx'
import SeverityBadge from '../components/SeverityBadge.jsx'
import { SEVERITIES, formatNumber, formatPercent, formatUptime } from '../services/api.js'

const STATUS_TONE = {
  HEALTHY: 'ok',
  DEGRADED: 'warn',
  ANOMALY: 'bad',
  STARTING: 'pending',
}

/** Current state of the detector, shown as the headline of the dashboard. */
function SystemStatus({ metrics, health, source, status }) {
  const value = String(metrics?.status ?? 'UNKNOWN').toUpperCase()
  const tone = STATUS_TONE[value] ?? 'pending'
  const notifierMode = metrics?.notifier?.mode

  return (
    <section className={`system-status tone-${tone}`}>
      <span className="status-dot" aria-hidden="true" />
      <div className="status-main">
        <span className="status-label">System Status</span>
        <span className="status-value">{value}</span>
      </div>
      <div className="status-meta">
        <span>
          <em>Uptime</em> {formatUptime(metrics?.uptimeSeconds ?? health?.uptimeSeconds)}
        </span>
        <span>
          <em>Stream</em> {source === 'demo' ? 'demo generator' : 'live log monitor'}
        </span>
        <span>
          <em>Socket</em> {status?.toLowerCase()}
        </span>
        {notifierMode ? (
          <span title="Notification transport - AWS credentials stay on the backend">
            <em>Notifier</em> {String(notifierMode).toUpperCase()}
          </span>
        ) : null}
      </div>
    </section>
  )
}

/** Live severity tally, derived from the alerts already received. */
function SeverityBreakdown({ alerts }) {
  const counts = useMemo(() => {
    const tally = Object.fromEntries(SEVERITIES.map((severity) => [severity, 0]))
    alerts.forEach((alert) => {
      if (tally[alert.severity] !== undefined) tally[alert.severity] += 1
    })
    return tally
  }, [alerts])

  return (
    <div className="severity-breakdown">
      {SEVERITIES.map((severity) => (
        <div className="sev-row" key={severity}>
          <SeverityBadge severity={severity} size="sm" />
          <span className="sev-bar">
            <span
              className={`sev-bar-fill fill-${severity}`}
              style={{ width: `${alerts.length ? (counts[severity] / alerts.length) * 100 : 0}%` }}
            />
          </span>
          <span className="sev-count">{counts[severity]}</span>
        </div>
      ))}
    </div>
  )
}

export default function Dashboard({ data }) {
  const { metrics, alerts, health, loading, error, status, source } = data
  const [service, setService] = useState('ALL')

  const services = useMemo(
    () => Array.from(new Set(alerts.map((alert) => alert.service).filter(Boolean))),
    [alerts],
  )
  const serviceCounts = useMemo(() => {
    const tally = {}
    alerts.forEach((alert) => {
      tally[alert.service] = (tally[alert.service] ?? 0) + 1
    })
    return tally
  }, [alerts])

  const visibleAlerts = useMemo(
    () => (service === 'ALL' ? alerts : alerts.filter((alert) => alert.service === service)),
    [alerts, service],
  )

  // Metric tones follow the live numbers so the eye goes straight to trouble.
  const errorRate = metrics?.currentErrorRate
  const baseline = metrics?.baselineErrorRate
  const rateTone =
    typeof errorRate !== 'number'
      ? 'neutral'
      : errorRate >= 15
        ? 'crit'
        : errorRate >= 8
          ? 'bad'
          : errorRate > 0 && baseline > 0 && errorRate >= baseline * 2
            ? 'warn'
            : 'ok'

  const deviation = typeof errorRate === 'number' && typeof baseline === 'number' ? errorRate - baseline : null

  return (
    <div className="page">
      {error ? (
        <div className="banner banner-warn" role="status">
          <strong>Demo mode</strong>
          <span>{error}</span>
        </div>
      ) : null}

      <SystemStatus metrics={metrics} health={health} source={source} status={status} />

      <section className="metrics-grid">
        <MetricCard
          title="Current Error Rate"
          value={formatPercent(errorRate)}
          icon="⚠"
          status={rateTone}
          trend={
            deviation !== null
              ? { direction: deviation > 0 ? 'up' : 'down', label: `${deviation > 0 ? '+' : ''}${formatPercent(deviation)} vs baseline` }
              : undefined
          }
          hint={metrics?.thresholdErrorRate ? `threshold ${formatPercent(metrics.thresholdErrorRate)}` : undefined}
          loading={loading}
        />
        <MetricCard
          title="Baseline Error Rate"
          value={formatPercent(baseline)}
          icon="◈"
          status="neutral"
          hint={metrics?.deviationSigma ? `${metrics.deviationSigma.toFixed(1)}σ deviation` : 'rolling average'}
          loading={loading}
        />
        <MetricCard
          title="Active Anomalies"
          value={formatNumber(metrics?.anomalyCount)}
          icon="⚑"
          status={(metrics?.anomalyCount ?? 0) > 0 ? 'bad' : 'ok'}
          hint={`${formatNumber(metrics?.totalAlerts)} total`}
          loading={loading}
        />
        <MetricCard
          title="Logs Processed"
          value={formatNumber(metrics?.totalLogs)}
          unit="lines"
          icon="≡"
          status="neutral"
          hint={metrics?.logsPerMinute ? `${formatNumber(metrics.logsPerMinute)}/min` : undefined}
          loading={loading}
        />
        <MetricCard
          title="Total Errors"
          value={formatNumber(metrics?.totalErrors)}
          unit="events"
          icon="✕"
          status={(metrics?.totalErrors ?? 0) > 0 ? 'warn' : 'ok'}
          hint={metrics?.totalWarnings ? `${formatNumber(metrics.totalWarnings)} warnings` : undefined}
          loading={loading}
        />
      </section>

      <ErrorRateChart metrics={metrics} />

      <div className="dash-bottom">
        <section className="panel feed-panel">
          <div className="panel-head">
            <div>
              <h2>Live Alert Feed</h2>
              <p className="panel-sub">Newest first · pushed over WebSocket, no refresh needed</p>
            </div>
            <span className="live-tag">
              <span className="live-dot" /> LIVE
            </span>
          </div>
          <AlertFeed alerts={visibleAlerts} loading={loading} dense maxHeight={520} />
        </section>

        <aside className="dash-side">
          <section className="panel">
            <div className="panel-head">
              <h2>Service Filter</h2>
            </div>
            <ServiceFilter services={services} value={service} onChange={setService} counts={serviceCounts} />
            <p className="panel-note">
              {visibleAlerts.length} of {alerts.length} alerts shown
            </p>
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>Alerts by Severity</h2>
            </div>
            <SeverityBreakdown alerts={alerts} />
          </section>
        </aside>
      </div>
    </div>
  )
}
