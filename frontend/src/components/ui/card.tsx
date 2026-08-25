import type { HTMLAttributes, ReactNode } from 'react'

import { cn } from '@/lib/utils'

export function Card({
  className,
  label,
  ...props
}: HTMLAttributes<HTMLDivElement> & { label?: string | undefined }) {
  return (
    <div
      // A labelled card becomes a landmark, so a screen-reader user can jump
      // between dashboard panels instead of walking every figure in order.
      // Unlabelled cards stay plain divs: a region with no name is worse than
      // no region, because it adds a stop that announces nothing.
      {...(label ? { role: 'region', 'aria-label': label } : {})}
      className={cn(
        'min-w-0 rounded-card border border-border-subtle bg-surface-raised',
        'p-4 shadow-flat sm:p-5',
        className,
      )}
      {...props}
    />
  )
}

/**
 * A card's title row.
 *
 * Exists so that "heading on the left, controls on the right, description
 * underneath" is one component rather than a flex wrapper hand-built on every
 * panel - which is how the alignment drifts a pixel per page until nothing
 * lines up.
 */
export function CardHeader({
  title,
  description,
  actions,
  className,
}: {
  title: ReactNode
  description?: ReactNode | undefined
  actions?: ReactNode | undefined
  className?: string | undefined
}) {
  return (
    <div className={cn('flex flex-wrap items-start justify-between gap-3', className)}>
      <div className="min-w-0">
        <CardTitle>{title}</CardTitle>
        {description ? <CardDescription>{description}</CardDescription> : null}
      </div>
      {actions ? <div className="flex min-w-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  )
}

export function CardTitle({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h2
      className={cn('text-sm font-semibold tracking-tight text-content sm:text-base', className)}
      {...props}
    />
  )
}

export function CardDescription({ className, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p className={cn('mt-1 text-sm leading-relaxed text-content-muted', className)} {...props} />
  )
}

/**
 * A caveat pinned to the bottom of a panel.
 *
 * Used for the notes this console is obliged to carry - "simulated, not
 * production traffic", "drift is not fraud", "unlabelled is not negative".
 * Giving them a consistent, quiet treatment is what stops them from being
 * either shouted or dropped.
 */
export function CardNote({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <p
      className={cn(
        'mt-4 border-t border-border-subtle pt-3 text-xs leading-relaxed text-content-faint',
        className,
      )}
    >
      {children}
    </p>
  )
}
