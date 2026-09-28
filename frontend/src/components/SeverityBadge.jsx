import { normalizeSeverity } from '../services/api.js'

/**
 * SeverityBadge - the single place severity is rendered.
 * LOW | MEDIUM | HIGH | CRITICAL, always with the same colours app-wide.
 */
export default function SeverityBadge({ severity, size = 'md' }) {
  const value = normalizeSeverity(severity)
  return (
    <span className={`sev sev-${value} sev-${size}`} data-severity={value}>
      {value}
    </span>
  )
}
