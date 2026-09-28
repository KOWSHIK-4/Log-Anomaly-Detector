/**
 * MetricCard - the one and only metric tile.
 * Used for Current Error Rate, Baseline, Anomalies, Total Logs, Total Errors.
 */
export default function MetricCard({
  title,
  value,
  unit,
  icon,
  status = 'neutral',
  trend,
  hint,
  loading = false,
}) {
  return (
    <article className={`metric-card tone-${status} ${loading ? 'is-loading' : ''}`}>
      <div className="metric-head">
        <span className="metric-title">{title}</span>
        {icon ? <span className="metric-icon" aria-hidden="true">{icon}</span> : null}
      </div>

      <div className="metric-value">
        <span className="metric-number">{value}</span>
        {unit ? <span className="metric-unit">{unit}</span> : null}
      </div>

      {trend || hint ? (
        <div className="metric-foot">
          {trend ? (
            <span className={`metric-trend trend-${trend.direction ?? 'flat'}`}>
              {trend.direction === 'up' ? '▲' : trend.direction === 'down' ? '▼' : '■'} {trend.label}
            </span>
          ) : null}
          {hint ? <span className="metric-hint">{hint}</span> : null}
        </div>
      ) : null}
    </article>
  )
}
