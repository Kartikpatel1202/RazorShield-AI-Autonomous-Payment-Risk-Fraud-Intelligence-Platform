import type { Bucket } from '@/lib/api'
import { formatCount } from '@/lib/format'
import { cn } from '@/lib/utils'

/**
 * A vertical histogram for the fraud-probability distribution.
 *
 * Log-scaled heights. The distribution is extremely skewed - roughly 96% of
 * transactions sit in the lowest bucket - and on a linear scale every other bar
 * would be invisible, hiding exactly the tail a risk analyst is looking for.
 * The axis says so explicitly rather than letting the reader assume linear.
 */
export function Histogram({
  buckets,
  className,
}: {
  buckets: Bucket[]
  className?: string | undefined
}) {
  const max = Math.max(...buckets.map((b) => b.count), 1)
  const scale = (count: number) => (count <= 0 ? 0 : Math.log10(count + 1) / Math.log10(max + 1))
  const total = buckets.reduce((sum, b) => sum + b.count, 0)

  if (total === 0) {
    return <p className="py-8 text-center text-sm text-content-muted">No predictions stored.</p>
  }

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <div className="flex h-40 items-end gap-1" role="img"
        aria-label={`Fraud probability distribution across ${buckets.length} buckets, ${formatCount(total)} predictions total.`}>
        {buckets.map((bucket) => {
          const height = scale(bucket.count) * 100
          const isTail = (bucket.lower ?? 0) >= 0.5
          return (
            <div key={bucket.label} className="flex min-w-0 flex-1 flex-col items-center gap-1">
              <span className="numeric text-[10px] text-content-faint">
                {bucket.count > 0 ? formatCount(bucket.count) : ''}
              </span>
              <div
                className={cn(
                  'w-full rounded-t-sm',
                  isTail ? 'bg-danger/70' : 'bg-brand/70',
                )}
                style={{ height: `${Math.max(height, bucket.count > 0 ? 2 : 0)}%` }}
                title={`${bucket.label}: ${formatCount(bucket.count)}`}
              />
            </div>
          )
        })}
      </div>
      <div className="flex gap-1">
        {buckets.map((bucket) => (
          <span
            key={bucket.label}
            className="numeric min-w-0 flex-1 text-center text-[9px] text-content-faint"
          >
            {(bucket.lower ?? 0).toFixed(1)}
          </span>
        ))}
      </div>
      <p className="text-xs text-content-faint">
        Fraud probability, ten equal buckets. Bar heights are log-scaled - the distribution is
        heavily skewed toward zero, and a linear axis would hide the high-risk tail entirely.
      </p>
    </div>
  )
}
