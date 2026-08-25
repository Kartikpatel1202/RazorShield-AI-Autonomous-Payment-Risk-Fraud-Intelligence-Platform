import type { ReactNode } from 'react'

import { toneClasses, type Tone } from '@/lib/risk'
import { cn } from '@/lib/utils'

const DOT: Record<Tone, string> = {
  positive: 'bg-positive',
  warning: 'bg-warning',
  attention: 'bg-attention',
  danger: 'bg-danger',
  neutral: 'bg-border-strong',
  brand: 'bg-brand',
}

export interface TimelineEntry {
  readonly id: string
  readonly title: ReactNode
  readonly timestamp?: ReactNode | undefined
  readonly tone?: Tone | undefined
  readonly glyph?: string | undefined
  readonly body?: ReactNode | undefined
  readonly meta?: ReactNode | undefined
}

/**
 * A vertical sequence of events.
 *
 * Used for the audit log and for a transaction's own history. The rail is drawn
 * with a `border-l` on the list and absolutely positioned dots rather than a
 * per-item line segment, so the last item's rail stops at its dot instead of
 * trailing into empty space.
 */
export function Timeline({ entries }: { entries: readonly TimelineEntry[] }) {
  return (
    <ol className="relative ml-2 flex flex-col gap-5 border-l border-border-subtle pl-6">
      {entries.map((entry, index) => (
        <li key={entry.id} className="relative">
          <span
            aria-hidden="true"
            className={cn(
              'absolute top-1 -left-[1.9rem] grid size-4 place-items-center rounded-full',
              'ring-4 ring-surface-raised',
              DOT[entry.tone ?? 'neutral'],
            )}
          >
            {entry.glyph ? (
              <span className="text-[0.55rem] leading-none font-bold text-brand-contrast">
                {entry.glyph}
              </span>
            ) : null}
          </span>

          {/* The rail is a border on the <ol>, so it runs the full height. This
              masks the segment below the final dot, which would otherwise hang
              past the last event as though more were coming. */}
          {index === entries.length - 1 ? (
            <span
              aria-hidden="true"
              className="absolute top-4 -left-[1.55rem] h-[calc(100%+1.5rem)] w-px bg-surface-raised"
            />
          ) : null}

          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <p className="text-sm font-semibold text-content">{entry.title}</p>
            {entry.timestamp ? (
              <p className="numeric text-xs whitespace-nowrap text-content-faint">
                {entry.timestamp}
              </p>
            ) : null}
          </div>
          {entry.body ? <div className="mt-1 text-sm text-content-muted">{entry.body}</div> : null}
          {entry.meta ? <div className="mt-2">{entry.meta}</div> : null}
        </li>
      ))}
    </ol>
  )
}

/**
 * The pipeline a transaction moves through, drawn as connected stages.
 *
 * Horizontal on a wide screen, vertical below `sm` - a five-stage horizontal
 * flow on a phone becomes five unreadable columns.
 */
export interface FlowStage {
  readonly key: string
  readonly label: string
  readonly value: ReactNode
  readonly state: 'done' | 'active' | 'skipped' | 'pending'
  readonly tone?: Tone | undefined
  readonly hint?: string | undefined
}

const STATE_RING: Record<FlowStage['state'], string> = {
  done: 'border-border-strong bg-surface-raised',
  active: 'border-brand bg-brand/5',
  skipped: 'border-dashed border-border-subtle bg-surface-sunken/50',
  pending: 'border-dashed border-border-subtle bg-surface-sunken/50',
}

export function StageFlow({ stages }: { stages: readonly FlowStage[] }) {
  return (
    <ol className="flex flex-col gap-2 sm:flex-row sm:items-stretch">
      {stages.map((stage, index) => (
        <li
          key={stage.key}
          className="flex min-w-0 flex-1 flex-col items-stretch gap-1 sm:flex-row sm:items-center sm:gap-0"
        >
          <div
            className={cn(
              'min-w-0 flex-1 rounded-lg border px-3 py-2.5 transition-colors',
              STATE_RING[stage.state],
            )}
            title={stage.hint}
          >
            <p className="truncate text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
              {stage.label}
            </p>
            <div
              className={cn(
                'mt-1 truncate text-sm font-semibold',
                stage.state === 'skipped' || stage.state === 'pending'
                  ? 'text-content-faint'
                  : toneClasses(stage.tone ?? 'neutral').text,
              )}
            >
              {stage.value}
            </div>
          </div>
          {index < stages.length - 1 ? (
            <span
              aria-hidden="true"
              className="shrink-0 self-center text-sm text-border-strong sm:px-2"
            >
              <span className="hidden sm:inline">→</span>
              <span className="sm:hidden">↓</span>
            </span>
          ) : null}
        </li>
      ))}
    </ol>
  )
}
