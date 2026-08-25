import { useId } from 'react'

import { toneClasses, type Tone } from '@/lib/risk'
import { cn } from '@/lib/utils'

const STROKE: Record<Tone, string> = {
  positive: 'stroke-positive',
  warning: 'stroke-warning',
  attention: 'stroke-attention',
  danger: 'stroke-danger',
  neutral: 'stroke-border-strong',
  brand: 'stroke-brand',
}

/**
 * A tiny trend line.
 *
 * Deliberately unlabelled and `aria-hidden`: it carries shape, not value, and
 * every place it appears the figure it accompanies is already stated in text.
 * A screen reader announcing "chart" here would add nothing but noise.
 *
 * Drawn in a fixed 100x28 viewBox and stretched, so a caller never has to think
 * about coordinate space - the points are normalised into it.
 */
export function Sparkline({
  points,
  tone = 'brand',
  fill = true,
  className,
}: {
  points: readonly number[]
  tone?: Tone | undefined
  fill?: boolean | undefined
  className?: string | undefined
}) {
  const gradientId = useId()
  if (points.length < 2) return null

  const max = Math.max(...points)
  const min = Math.min(...points)
  // A flat series has zero range; dividing by it would put every point at NaN.
  // Rendering it along the midline is the honest shape for "did not change".
  const range = max - min || 1
  const step = 100 / (points.length - 1)

  const coords = points.map((value, index) => {
    const x = index * step
    const y = 26 - ((value - min) / range) * 24
    return `${x.toFixed(2)},${y.toFixed(2)}`
  })

  const line = `M ${coords.join(' L ')}`
  const area = `${line} L 100,28 L 0,28 Z`

  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 100 28"
      preserveAspectRatio="none"
      className={cn('h-7 w-20 overflow-visible', className)}
    >
      {fill ? (
        <>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" className={toneClasses(tone).fill} stopOpacity="0.22" />
              <stop offset="100%" className={toneClasses(tone).fill} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={area} fill={`url(#${gradientId})`} />
        </>
      ) : null}
      <path
        d={line}
        fill="none"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
        className={STROKE[tone]}
      />
    </svg>
  )
}
