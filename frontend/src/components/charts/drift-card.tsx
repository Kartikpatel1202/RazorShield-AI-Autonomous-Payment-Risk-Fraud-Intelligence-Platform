import { Badge } from '@/components/ui/badge'
import type { DriftFeature } from '@/lib/api'
import { formatCount, humanizeCode } from '@/lib/format'
import type { Tone } from '@/lib/risk'

/** NORMAL is positive, WATCH warning, DRIFT_DETECTED danger, no data neutral. */
function driftTone(status: string): Tone {
  switch (status) {
    case 'NORMAL':
      return 'positive'
    case 'WATCH':
      return 'warning'
    case 'DRIFT_DETECTED':
      return 'danger'
    default:
      return 'neutral'
  }
}

function driftGlyph(status: string): string {
  switch (status) {
    case 'NORMAL':
      return '✓'
    case 'WATCH':
      return '!'
    case 'DRIFT_DETECTED':
      return '▲'
    default:
      return '–'
  }
}

/**
 * One monitored feature's drift status.
 *
 * The PSI value is shown alongside the band so the reader can see how close a
 * NORMAL sits to WATCH - a status without its underlying number is a verdict
 * the reader has to take on trust.
 */
export function DriftCard({ feature }: { feature: DriftFeature }) {
  const insufficient = feature.status === 'INSUFFICIENT_DATA'

  return (
    <div className="flex flex-col gap-2 rounded-card border border-border-subtle bg-surface-raised p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-semibold tracking-tight">
          {humanizeCode(feature.feature)}
        </span>
        <Badge tone={driftTone(feature.status)} glyph={driftGlyph(feature.status)}>
          {feature.status.replace(/_/g, ' ')}
        </Badge>
      </div>

      <div className="flex items-baseline gap-2">
        <span className="numeric text-2xl font-semibold">
          {feature.psi === null ? '--' : feature.psi.toFixed(3)}
        </span>
        <span className="text-xs text-content-faint">PSI</span>
      </div>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
        <dt className="text-content-muted">Baseline</dt>
        <dd className="numeric text-right">
          {formatCount(feature.baseline_count)}
          {feature.baseline_mean !== null ? (
            <span className="ml-1 text-content-faint">μ {feature.baseline_mean.toFixed(2)}</span>
          ) : null}
        </dd>
        <dt className="text-content-muted">Current</dt>
        <dd className="numeric text-right">
          {formatCount(feature.current_count)}
          {feature.current_mean !== null ? (
            <span className="ml-1 text-content-faint">μ {feature.current_mean.toFixed(2)}</span>
          ) : null}
        </dd>
      </dl>

      {insufficient ? (
        <p className="text-xs text-content-faint">
          Too few rows in one window to compute a meaningful PSI.
        </p>
      ) : null}
    </div>
  )
}
