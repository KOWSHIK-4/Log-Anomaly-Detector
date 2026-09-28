import { useMemo } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { formatPercent, formatTime } from '../services/api.js'

/** A series older than this gets a live tail point appended. */
const STALE_MS = 10000

const ChartTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null
  const point = payload[0].payload
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-time">{formatTime(point.t)}</div>
      {payload
        .filter((entry) => entry.value !== null && entry.value !== undefined)
        .map((entry) => (
          <div className="chart-tooltip-row" key={entry.dataKey}>
            <span className="chart-tooltip-key" style={{ background: entry.color }} />
            <span className="chart-tooltip-label">{entry.name}</span>
            <span className="chart-tooltip-value">{formatPercent(entry.value)}</span>
          </div>
        ))}
    </div>
  )
}

const LegendKey = ({ color, label, dashed }) => (
  <span className="key">
    <span className={`swatch ${dashed ? 'is-dashed' : ''}`} style={{ background: color }} />
    {label}
  </span>
)

/**
 * ErrorRateChart - error rate over time against the rolling baseline.
 * Accepts new data at any time: the live tail keeps the line growing even
 * when the backend does not send a trend history.
 */
export default function ErrorRateChart({ metrics, height = 300 }) {
  const data = useMemo(() => {
    const series = Array.isArray(metrics?.series) ? metrics.series : []
    if (!series.length) return []

    const last = series[series.length - 1]
    const current = typeof metrics?.currentErrorRate === 'number' ? metrics.currentErrorRate : null
    if (current !== null && Date.now() - last.t > STALE_MS) {
      return [
        ...series,
        {
          t: Date.now(),
          errorRate: current,
          baseline: metrics.baselineErrorRate ?? null,
          threshold: metrics.thresholdErrorRate ?? null,
        },
      ]
    }
    return series
  }, [metrics])

  const hasThreshold = data.some((point) => typeof point.threshold === 'number')
  const hasBaseline = data.some((point) => typeof point.baseline === 'number')

  return (
    <section className="panel chart-panel">
      <div className="panel-head">
        <div>
          <h2>Error Rate vs Baseline</h2>
          <p className="panel-sub">Rolling window error rate for the monitored log stream</p>
        </div>
        <div className="chart-legend">
          <LegendKey color="var(--accent)" label="Error rate" />
          {hasBaseline ? <LegendKey color="var(--ok)" label="Baseline" dashed /> : null}
          {hasThreshold ? <LegendKey color="var(--bad)" label="Alert threshold" dashed /> : null}
        </div>
      </div>

      {data.length === 0 ? (
        <div className="chart-empty">
          <div className="feed-empty-icon" aria-hidden="true">
            ◔
          </div>
          <h3>No window data yet</h3>
          <p>Waiting for the first evaluation window from the detector.</p>
        </div>
      ) : (
        <div className="chart-wrap" style={{ height }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: -12 }}>
              <defs>
                <linearGradient id="errorRateFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#4d8dff" stopOpacity={0.38} />
                  <stop offset="100%" stopColor="#4d8dff" stopOpacity={0.02} />
                </linearGradient>
              </defs>

              <CartesianGrid stroke="#1f2a52" strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="t"
                type="number"
                domain={['dataMin', 'dataMax']}
                tickFormatter={formatTime}
                tick={{ fill: '#6b789f', fontSize: 10.5 }}
                axisLine={{ stroke: '#24305c' }}
                tickLine={false}
                minTickGap={48}
              />
              <YAxis
                width={52}
                tickFormatter={(value) => `${Math.round(value)}%`}
                tick={{ fill: '#6b789f', fontSize: 10.5 }}
                axisLine={false}
                tickLine={false}
                domain={[0, (dataMax) => Math.max(5, Math.ceil(dataMax * 1.25))]}
              />
              <Tooltip content={<ChartTooltip />} cursor={{ stroke: '#4d8dff', strokeOpacity: 0.35 }} />

              {hasThreshold ? (
                <Line
                  type="monotone"
                  dataKey="threshold"
                  name="Threshold"
                  stroke="var(--bad)"
                  strokeWidth={1.3}
                  strokeDasharray="5 4"
                  dot={false}
                  isAnimationActive={false}
                />
              ) : null}
              {hasBaseline ? (
                <Line
                  type="monotone"
                  dataKey="baseline"
                  name="Baseline"
                  stroke="var(--ok)"
                  strokeWidth={1.6}
                  strokeDasharray="4 3"
                  dot={false}
                  isAnimationActive={false}
                />
              ) : null}
              <Area
                type="monotone"
                dataKey="errorRate"
                name="Error rate"
                stroke="var(--accent)"
                strokeWidth={2.2}
                fill="url(#errorRateFill)"
                dot={false}
                activeDot={{ r: 4, strokeWidth: 2, stroke: '#0b1020' }}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  )
}
