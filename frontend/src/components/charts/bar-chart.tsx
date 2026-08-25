import { cn } from '@/lib/utils'
import { formatCount } from '@/lib/format'
import { toneClasses, type Tone } from '@/lib/risk'

export interface BarDatum {
  label: string
  count: number
  tone?: Tone | undefined
  /** Optional secondary line, e.g. the numeric range a bucket covers. */
  hint?: string | undefined
}

/**
 * A horizontal bar chart, drawn with plain elements rather than SVG.
 *
 * Horizontal because the labels are words (APPROVE, CRITICAL, reason codes) and
 * horizontal bars give them room to be read without rotation. Each bar carries
 * its own numeric label, so the chart is a table that happens to be sorted by
 * magnitude - readable without estimating lengths against an axis.
 */
export function BarChart({
  data,
  emptyLabel = 'No data',
  showPercentage = true,
  className,
}: {
  data: BarDatum[]
  emptyLabel?: string | undefined
  showPercentage?: boolean | undefined
  className?: string | undefined
}) {
  const total = data.reduce((sum, item) => sum + item.count, 0)
  const max = Math.max(...data.map((item) => item.count), 1)

  if (data.length === 0) {
    return <p className="py-6 text-center text-sm text-content-muted">{emptyLabel}</p>
  }

  return (
    <ul className={cn('flex flex-col gap-2.5', className)}>
      {data.map((item) => {
        const classes = toneClasses(item.tone ?? 'brand')
        const share = total > 0 ? item.count / total : 0
        return (
          <li key={item.label} className="flex flex-col gap-1">
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="font-medium text-content">{item.label}</span>
              <span className="numeric shrink-0 text-content-muted">
                {formatCount(item.count)}
                {showPercentage && total > 0 ? (
                  <span className="ml-1.5 text-content-faint">
                    {(share * 100).toFixed(share < 0.1 && share > 0 ? 2 : 1)}%
                  </span>
                ) : null}
              </span>
            </div>
            <div
              className="h-2 w-full overflow-hidden rounded-full bg-surface-sunken"
              role="img"
              aria-label={`${item.label}: ${formatCount(item.count)}${
                total > 0 ? ` (${(share * 100).toFixed(1)}% of ${formatCount(total)})` : ''
              }`}
            >
              {/* `bg-current` paints the bar in the tone's own text colour, so
                  one token drives both the label and the fill. */}
              <div
                className={cn('h-full rounded-full bg-current', classes.text)}
                style={{
                  width: `${Math.max((item.count / max) * 100, item.count > 0 ? 1.5 : 0)}%`,
                }}
              />
            </div>
            {item.hint ? <p className="text-xs text-content-faint">{item.hint}</p> : null}
          </li>
        )
      })}
    </ul>
  )
}
