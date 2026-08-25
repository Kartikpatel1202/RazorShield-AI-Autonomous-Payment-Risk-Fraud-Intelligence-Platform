import { toneClasses, type Tone } from '@/lib/risk'
import { cn } from '@/lib/utils'

const TRACK: Record<Tone, string> = {
  positive: 'bg-positive',
  warning: 'bg-warning',
  attention: 'bg-attention',
  danger: 'bg-danger',
  neutral: 'bg-border-strong',
  brand: 'bg-brand',
}

/**
 * A horizontal bar for a bounded score, with the thresholds that matter marked
 * on it.
 *
 * The markers are the point. A fraud probability of 0.62 means nothing on its
 * own; 0.62 shown against the policy's own medium/high/block thresholds says
 * immediately which side of which line it fell. The numbers come from the
 * policy the backend reports - none is hardcoded here.
 */
export function RiskGauge({
  value,
  max = 1,
  tone = 'neutral',
  markers = [],
  label,
  valueLabel,
  className,
}: {
  value: number | null | undefined
  max?: number | undefined
  tone?: Tone | undefined
  markers?: readonly { readonly at: number; readonly label: string }[] | undefined
  label: string
  valueLabel: string
  className?: string | undefined
}) {
  const known = value !== null && value !== undefined && Number.isFinite(value)
  const share = known ? Math.max(0, Math.min(1, value / max)) : 0

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[0.7rem] font-semibold tracking-[0.06em] text-content-muted uppercase">
          {label}
        </span>
        <span className={cn('numeric text-sm font-semibold', toneClasses(tone).text)}>
          {known ? valueLabel : '--'}
        </span>
      </div>

      <div
        role="meter"
        aria-label={label}
        aria-valuenow={known ? value : undefined}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuetext={known ? valueLabel : 'not scored'}
        className="relative h-2 w-full overflow-hidden rounded-full bg-surface-sunken"
      >
        <div
          className={cn('h-full rounded-full transition-[width] duration-700', TRACK[tone])}
          style={{ width: `${Math.max(share * 100, known && share > 0 ? 1.5 : 0)}%` }}
        />
        {markers.map((marker) => (
          <span
            key={marker.label}
            aria-hidden="true"
            title={`${marker.label}: ${marker.at}`}
            className="absolute inset-y-0 w-px bg-content/35"
            style={{ left: `${Math.min(100, (marker.at / max) * 100)}%` }}
          />
        ))}
      </div>

      {markers.length > 0 ? (
        <div className="flex flex-wrap gap-x-3 text-[0.65rem] text-content-faint">
          {markers.map((marker) => (
            <span key={marker.label} className="numeric">
              {marker.label} {marker.at}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  )
}
