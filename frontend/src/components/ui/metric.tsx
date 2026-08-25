import type { ReactNode } from 'react'

import { Skeleton } from '@/components/ui/states'
import { toneClasses, type Tone } from '@/lib/risk'
import { cn } from '@/lib/utils'

/** The accent rail colour for each tone. Literal classes, not interpolated. */
const RAIL: Record<Tone, string> = {
  positive: 'bg-positive',
  warning: 'bg-warning',
  attention: 'bg-attention',
  danger: 'bg-danger',
  neutral: 'bg-border-strong',
  brand: 'bg-brand',
}

interface MetricProps {
  label: string
  value: ReactNode
  /** The time range or denominator the figure covers. Required by design. */
  scope: string
  tone?: Tone | undefined
  loading?: boolean | undefined
  /** A secondary figure shown under the value - a share, a rate, a count. */
  detail?: ReactNode | undefined
  /** Rendered to the right of the value: a sparkline, a meter, a badge. */
  visual?: ReactNode | undefined
  emphasis?: boolean | undefined
  className?: string | undefined
}

/**
 * One headline figure.
 *
 * `scope` is not optional: a count without its denominator or time range is the
 * kind of number that looks authoritative and means nothing, so the component
 * makes it impossible to render one without saying what it covers.
 *
 * The tone is carried by a 3px rail down the left edge rather than by colouring
 * the number. Colouring the number would make a large approval count read as
 * "good news" at a glance even when it is the thing that should worry you; the
 * rail categorises without editorialising, and the label still says which state
 * it is.
 */
export function Metric({
  label,
  value,
  scope,
  tone = 'neutral',
  loading,
  detail,
  visual,
  emphasis,
  className,
}: MetricProps) {
  return (
    <div
      className={cn(
        'relative flex min-w-0 flex-col gap-1 overflow-hidden rounded-card border border-border-subtle',
        'bg-surface-raised py-3.5 pr-4 pl-5 shadow-flat transition-shadow hover:shadow-raised',
        className,
      )}
    >
      <span aria-hidden="true" className={cn('absolute inset-y-0 left-0 w-[3px]', RAIL[tone])} />

      <p className="truncate text-[0.7rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
        {label}
      </p>

      <div className="flex items-end justify-between gap-3">
        {loading ? (
          <Skeleton className="h-8 w-24" />
        ) : (
          <p
            className={cn(
              'numeric font-semibold tracking-tight text-content',
              emphasis ? 'text-3xl' : 'text-2xl',
            )}
          >
            {value}
          </p>
        )}
        {visual ? <div className="shrink-0 pb-1">{visual}</div> : null}
      </div>

      {detail ? (
        <p className={cn('text-xs font-medium', toneClasses(tone).text)}>{detail}</p>
      ) : null}
      <p className="truncate text-xs text-content-faint" title={scope}>
        {scope}
      </p>
    </div>
  )
}

/**
 * A compact proportion bar.
 *
 * Used beside a count to show its share of a whole. Purely redundant with the
 * text beside it, which is the point - it is a shape the eye can compare across
 * a row of cards without reading eight numbers.
 */
export function ShareBar({ share, tone = 'brand' }: { share: number; tone?: Tone | undefined }) {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(share) ? share : 0))
  return (
    <div
      aria-hidden="true"
      className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-sunken"
      title={`${(clamped * 100).toFixed(1)}%`}
    >
      <div
        className={cn('h-full rounded-full transition-[width] duration-500', RAIL[tone])}
        style={{ width: `${Math.max(clamped * 100, clamped > 0 ? 2 : 0)}%` }}
      />
    </div>
  )
}
