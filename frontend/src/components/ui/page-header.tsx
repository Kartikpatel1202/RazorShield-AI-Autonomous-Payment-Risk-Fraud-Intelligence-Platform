import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/**
 * The banner every page opens with.
 *
 * One component rather than a hand-rolled `<header>` per route, because the
 * thing that makes a console feel like one product is that the eye lands in the
 * same place on every screen. `eyebrow` carries the section name, `meta` the
 * scope of what is shown - dataset size, time range, policy version - which is
 * the line that stops a figure from looking authoritative without saying what
 * it covers.
 */
export function PageHeader({
  eyebrow,
  title,
  description,
  meta,
  actions,
  className,
}: {
  eyebrow?: ReactNode | undefined
  title: string
  description?: ReactNode | undefined
  meta?: ReactNode | undefined
  actions?: ReactNode | undefined
  className?: string | undefined
}) {
  return (
    <header className={cn('flex flex-wrap items-start justify-between gap-4', className)}>
      <div className="min-w-0">
        {eyebrow ? (
          <p className="mb-1 text-xs font-semibold tracking-[0.12em] text-brand uppercase">
            {eyebrow}
          </p>
        ) : null}
        <h1 className="text-xl font-semibold tracking-tight text-content sm:text-2xl">{title}</h1>
        {description ? (
          <p className="mt-1.5 max-w-3xl text-sm leading-relaxed text-content-muted">
            {description}
          </p>
        ) : null}
        {meta ? <div className="mt-2 text-xs text-content-faint">{meta}</div> : null}
      </div>
      {actions ? <div className="flex min-w-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  )
}

/** A dot-separated run of facts. Keeps the meta line from becoming a sentence. */
export function MetaList({ items }: { items: readonly ReactNode[] }) {
  const present = items.filter(Boolean)
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
      {present.map((item, index) => (
        <span key={index} className="inline-flex items-center gap-2">
          {index > 0 ? (
            <span aria-hidden="true" className="text-border-strong">
              ·
            </span>
          ) : null}
          {item}
        </span>
      ))}
    </span>
  )
}
