import { useId, useMemo, useState } from 'react'

import { formatCount, formatDate } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { TrendPoint } from '@/lib/api'

interface Series {
  readonly key: keyof Pick<TrendPoint, 'volume' | 'high_risk' | 'review' | 'blocked'>
  readonly label: string
  /** CSS colour token, so the palette stays semantic. */
  readonly color: string
  /** Volume dwarfs the rest, so it is drawn as an area and the rest as lines. */
  readonly area?: boolean
}

const SERIES: readonly Series[] = [
  { key: 'volume', label: 'Volume', color: 'var(--color-brand)', area: true },
  { key: 'high_risk', label: 'High risk', color: 'var(--color-warning)' },
  { key: 'review', label: 'Review', color: 'var(--color-attention)' },
  { key: 'blocked', label: 'Blocked', color: 'var(--color-danger)' },
]

const WIDTH = 760
const HEIGHT = 220
const PADDING = { top: 12, right: 12, bottom: 26, left: 44 }

/**
 * A daily time series over real transaction timestamps.
 *
 * Hand-drawn SVG rather than a charting dependency: four series on one linear
 * scale needs about sixty lines, and owning it means the palette is the app's
 * own semantic tokens and there is no bundle cost.
 *
 * The chart is not the only representation of this data - a summary table sits
 * beneath it, so the figures are readable without interpreting a line.
 */
export function TrendChart({ points, className }: { points: TrendPoint[]; className?: string | undefined }) {
  const gradientId = useId()
  const [hover, setHover] = useState<number | null>(null)

  const { paths, maxValue, plotWidth, plotHeight } = useMemo(() => {
    const plotW = WIDTH - PADDING.left - PADDING.right
    const plotH = HEIGHT - PADDING.top - PADDING.bottom
    const max = Math.max(...points.flatMap((p) => SERIES.map((s) => p[s.key])), 1)

    const x = (index: number) =>
      PADDING.left + (points.length <= 1 ? plotW / 2 : (index / (points.length - 1)) * plotW)
    const y = (value: number) => PADDING.top + plotH - (value / max) * plotH

    const built = SERIES.map((series) => {
      const line = points
        .map((point, index) => `${index === 0 ? 'M' : 'L'}${x(index)},${y(point[series.key])}`)
        .join(' ')
      const area = series.area
        ? `${line} L${x(points.length - 1)},${PADDING.top + plotH} L${x(0)},${PADDING.top + plotH} Z`
        : undefined
      return { series, line, area }
    })

    return { paths: built, maxValue: max, plotWidth: plotW, plotHeight: plotH }
  }, [points])

  if (points.length === 0) {
    return (
      <p className="py-10 text-center text-sm text-content-muted">
        No transactions in this window.
      </p>
    )
  }

  const xFor = (index: number) =>
    PADDING.left +
    (points.length <= 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth)
  const active = hover !== null ? points[hover] : undefined
  const firstDay = points[0]?.day ?? null
  const lastDay = points[points.length - 1]?.day ?? null

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {SERIES.map((series) => (
          <span key={series.key} className="flex items-center gap-1.5 text-xs text-content-muted">
            <span
              aria-hidden="true"
              className="size-2.5 rounded-sm"
              style={{ backgroundColor: series.color }}
            />
            {series.label}
          </span>
        ))}
      </div>

      <div className="table-scroll">
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="h-56 w-full min-w-[34rem]"
          role="img"
          aria-label={`Daily transaction volume and risk dispositions across ${points.length} days. Peak value ${formatCount(maxValue)}.`}
          onMouseLeave={() => setHover(null)}
        >
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--color-brand)" stopOpacity="0.18" />
              <stop offset="100%" stopColor="var(--color-brand)" stopOpacity="0.01" />
            </linearGradient>
          </defs>

          {[0, 0.5, 1].map((fraction) => {
            const y = PADDING.top + plotHeight - fraction * plotHeight
            return (
              <g key={fraction}>
                <line
                  x1={PADDING.left}
                  x2={WIDTH - PADDING.right}
                  y1={y}
                  y2={y}
                  stroke="var(--color-border-subtle)"
                  strokeWidth={1}
                />
                <text
                  x={PADDING.left - 8}
                  y={y + 4}
                  textAnchor="end"
                  className="fill-content-faint text-[10px]"
                >
                  {formatCount(Math.round(maxValue * fraction))}
                </text>
              </g>
            )
          })}

          {paths.map(({ series, line, area }) => (
            <g key={series.key}>
              {area ? <path d={area} fill={`url(#${gradientId})`} /> : null}
              <path
                d={line}
                fill="none"
                stroke={series.color}
                strokeWidth={series.area ? 1.75 : 1.5}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            </g>
          ))}

          {hover !== null ? (
            <line
              x1={xFor(hover)}
              x2={xFor(hover)}
              y1={PADDING.top}
              y2={PADDING.top + plotHeight}
              stroke="var(--color-border-strong)"
              strokeWidth={1}
            />
          ) : null}

          {/* Invisible hit areas: one column per day, so hovering anywhere in
              the column selects that day rather than requiring pixel accuracy
              on a 1.5px line. */}
          {points.map((point, index) => (
            <rect
              key={point.day}
              x={xFor(index) - plotWidth / Math.max(points.length * 2, 1)}
              y={PADDING.top}
              width={Math.max(plotWidth / points.length, 2)}
              height={plotHeight}
              fill="transparent"
              onMouseEnter={() => setHover(index)}
            />
          ))}

          <text
            x={PADDING.left}
            y={HEIGHT - 6}
            className="fill-content-faint text-[10px]"
          >
            {formatDate(firstDay)}
          </text>
          <text
            x={WIDTH - PADDING.right}
            y={HEIGHT - 6}
            textAnchor="end"
            className="fill-content-faint text-[10px]"
          >
            {formatDate(lastDay)}
          </text>
        </svg>
      </div>

      <div
        aria-live="polite"
        className="min-h-[1.5rem] text-xs text-content-muted"
      >
        {active ? (
          <span className="flex flex-wrap gap-x-4 gap-y-1">
            <span className="font-medium text-content">{formatDate(active.day)}</span>
            {SERIES.map((series) => (
              <span key={series.key}>
                {series.label}:{' '}
                <span className="numeric text-content">{formatCount(active[series.key])}</span>
              </span>
            ))}
          </span>
        ) : (
          <span className="text-content-faint">Hover the chart for a daily breakdown.</span>
        )}
      </div>
    </div>
  )
}
