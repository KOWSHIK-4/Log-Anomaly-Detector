import { UNKNOWN_SERVICE } from '../services/api.js'

/**
 * ServiceFilter - filter alerts by service.
 *
 * The backend is not required to expose a service list, so the services are
 * derived from the alerts already received. With no services at all it still
 * renders (just the "All Services" pill) instead of crashing.
 */
export default function ServiceFilter({ services = [], value = 'ALL', onChange, counts = {} }) {
  const derived = Array.from(
    new Set(services.filter((service) => typeof service === 'string' && service.trim())),
  ).sort((a, b) => a.localeCompare(b))

  // The placeholder only appears when it is actually in use (or when there is
  // nothing else to show) - never as a misleading zero-count pill.
  const showUnknown = derived.length === 0 || (counts[UNKNOWN_SERVICE] ?? 0) > 0
  const list = Array.from(new Set(showUnknown ? [...derived, UNKNOWN_SERVICE] : derived)).sort((a, b) =>
    a.localeCompare(b),
  )

  const options = ['ALL', ...list]
  const total = Object.values(counts).reduce((sum, count) => sum + (count || 0), 0)

  return (
    <div className="service-filter" role="group" aria-label="Filter alerts by service">
      {options.map((service) => {
        const active = value === service
        const count = service === 'ALL' ? total : (counts[service] ?? 0)
        return (
          <button
            type="button"
            key={service}
            className={`pill ${active ? 'is-active' : ''}`}
            aria-pressed={active}
            onClick={() => onChange?.(service)}
          >
            {service === 'ALL' ? 'All Services' : service}
            <span className="pill-count">{count}</span>
          </button>
        )
      })}
    </div>
  )
}
