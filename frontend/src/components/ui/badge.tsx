import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'
import {
  decisionGlyph,
  decisionLabel,
  decisionTone,
  resolutionTone,
  reviewStatusTone,
  severityTone,
  toneClasses,
  type Tone,
} from '@/lib/risk'

interface BadgeProps {
  tone?: Tone | undefined
  glyph?: string | undefined
  children: ReactNode
  className?: string | undefined
  title?: string | undefined
}

/**
 * A labelled status chip.
 *
 * `children` is always rendered, so the meaning survives without colour. The
 * optional glyph adds a third, non-colour channel.
 */
export function Badge({ tone = 'neutral', glyph, children, className, title }: BadgeProps) {
  const classes = toneClasses(tone)
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center gap-1 rounded-md border px-2 py-0.5',
        'text-xs font-semibold whitespace-nowrap',
        classes.surface,
        classes.text,
        classes.border,
        className,
      )}
    >
      {glyph ? (
        <span aria-hidden="true" className="text-[0.7rem] leading-none">
          {glyph}
        </span>
      ) : null}
      {children}
    </span>
  )
}

/** The machine decision. Never used for a human resolution. */
export function DecisionBadge({
  decision,
  className,
}: {
  decision: string | null | undefined
  className?: string | undefined
}) {
  return (
    <Badge
      tone={decisionTone(decision)}
      glyph={decisionGlyph(decision)}
      className={className}
      title={decision ? `Machine decision: ${decisionLabel(decision)}` : 'Not yet decided'}
    >
      {decisionLabel(decision)}
    </Badge>
  )
}

export function SeverityBadge({ severity }: { severity: string | null | undefined }) {
  if (!severity) return <span className="text-content-faint">--</span>
  return <Badge tone={severityTone(severity)}>{severity.toUpperCase()}</Badge>
}

export function ReviewStatusBadge({ status }: { status: string }) {
  return <Badge tone={reviewStatusTone(status)}>{status.replace('_', ' ').toUpperCase()}</Badge>
}

/** A human analyst's resolution. Visually distinct from a DecisionBadge. */
export function ResolutionBadge({ resolution }: { resolution: string | null | undefined }) {
  if (!resolution) return <span className="text-content-faint">Unresolved</span>
  return (
    <Badge
      tone={resolutionTone(resolution)}
      glyph="◆"
      title={`Analyst resolution: ${resolution}`}
    >
      {resolution.toUpperCase()}
    </Badge>
  )
}
