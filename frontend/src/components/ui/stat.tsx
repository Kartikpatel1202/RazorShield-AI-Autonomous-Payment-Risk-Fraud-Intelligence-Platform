import type { ReactNode } from 'react'

import { Metric } from '@/components/ui/metric'
import type { Tone } from '@/lib/risk'

interface StatProps {
  label: string
  value: ReactNode
  /** The time range or denominator the figure covers. Required by design. */
  scope: string
  tone?: Tone | undefined
  loading?: boolean | undefined
  className?: string | undefined
}

/**
 * One headline figure.
 *
 * Now a thin alias over :func:`Metric`, which is the same idea with room for a
 * sparkline and a secondary line. Kept as its own export because a dozen call
 * sites use it and renaming them would be churn without a reader-visible
 * benefit - the two render identically.
 *
 * `scope` is not optional here either: a count without its denominator or time
 * range is the kind of number that looks authoritative and means nothing.
 */
export function Stat({ label, value, scope, tone = 'neutral', loading, className }: StatProps) {
  return (
    <Metric
      label={label}
      value={value}
      scope={scope}
      tone={tone}
      loading={loading}
      className={className}
    />
  )
}
