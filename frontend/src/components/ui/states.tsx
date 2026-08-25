import type { CSSProperties, ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

/** A shimmering placeholder with the shape of the content it replaces. */
export function Skeleton({
  className,
  style,
}: {
  className?: string | undefined
  style?: CSSProperties | undefined
}) {
  return (
    <div
      aria-hidden="true"
      style={style}
      className={cn(
        'animate-shimmer rounded-md',
        // A moving highlight rather than a pulsing block: a pulse reads as
        // something blinking at you, a sweep reads as something arriving.
        'bg-gradient-to-r from-surface-sunken via-surface-raised to-surface-sunken',
        className,
      )}
    />
  )
}

export function LoadingState({
  label = 'Loading',
  rows = 3,
}: {
  label?: string | undefined
  rows?: number | undefined
}) {
  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-3 py-2">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, index) => (
        // Varying widths so the placeholder reads as text rather than as a
        // stack of identical bars.
        <Skeleton key={index} className="h-4" style={{ width: `${100 - (index % 3) * 12}%` }} />
      ))}
    </div>
  )
}

/** A placeholder shaped like the table it stands in for. */
export function TableSkeleton({ rows = 6, columns = 6 }: { rows?: number; columns?: number }) {
  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-2 py-1">
      <span className="sr-only">Loading rows</span>
      {Array.from({ length: rows }, (_, row) => (
        <div key={row} className="flex gap-3">
          {Array.from({ length: columns }, (_, column) => (
            <Skeleton
              key={column}
              className={cn('h-8 flex-1', column === 0 && 'flex-[1.6]')}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

/**
 * Shown when a query succeeded and returned nothing.
 *
 * Deliberately distinct from the error state: "no data matched" and "the
 * request failed" are different facts, and a panel that renders zeros for both
 * misleads the reader.
 */
export function EmptyState({
  title = 'No data',
  description,
  action,
  glyph = '∅',
}: {
  title?: string | undefined
  description?: string | undefined
  action?: ReactNode | undefined
  glyph?: string | undefined
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border-subtle bg-surface-sunken/40 px-6 py-10 text-center">
      <span
        aria-hidden="true"
        className="grid size-9 place-items-center rounded-full bg-surface-raised text-base text-content-faint shadow-flat"
      >
        {glyph}
      </span>
      <p className="text-sm font-semibold text-content">{title}</p>
      {description ? <p className="max-w-md text-sm text-content-muted">{description}</p> : null}
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  )
}

export function ErrorState({
  error,
  onRetry,
  title = 'Could not load this data',
}: {
  error: Error
  onRetry?: (() => void) | undefined
  title?: string | undefined
}) {
  return (
    <div
      role="alert"
      className="flex flex-col items-start gap-3 rounded-lg border border-danger/30 bg-danger-surface px-4 py-4"
    >
      <div className="flex gap-3">
        <span
          aria-hidden="true"
          className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full bg-danger/15 text-xs font-bold text-danger"
        >
          !
        </span>
        <div>
          <p className="text-sm font-semibold text-danger">{title}</p>
          {/* The server's own message, not a generic apology - it usually names
              the exact parameter that was wrong. */}
          <p className="mt-1 text-sm text-content-muted">{error.message}</p>
        </div>
      </div>
      {onRetry ? (
        <Button variant="secondary" size="sm" onClick={onRetry} className="ml-9">
          Try again
        </Button>
      ) : null}
    </div>
  )
}

/**
 * The three states every panel needs, in one place.
 *
 * Using this rather than repeating the conditionals is what keeps a broken
 * chart from ever rendering: there is no path where `children` runs without
 * data.
 */
export function QueryBoundary<T>({
  loading,
  error,
  data,
  onRetry,
  isEmpty,
  emptyTitle,
  emptyDescription,
  loadingRows,
  skeleton,
  children,
}: {
  loading: boolean
  error: Error | undefined
  data: T | undefined
  onRetry?: (() => void) | undefined
  isEmpty?: ((data: T) => boolean) | undefined
  emptyTitle?: string | undefined
  emptyDescription?: string | undefined
  loadingRows?: number | undefined
  /** A placeholder shaped like this panel's real content. */
  skeleton?: ReactNode | undefined
  children: (data: T) => ReactNode
}) {
  if (error) return <ErrorState error={error} onRetry={onRetry} />
  if (loading || data === undefined) return <>{skeleton ?? <LoadingState rows={loadingRows} />}</>
  if (isEmpty?.(data)) return <EmptyState title={emptyTitle} description={emptyDescription} />
  return <>{children(data)}</>
}
