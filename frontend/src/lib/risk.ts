/**
 * Risk semantics: how a decision or severity is named and coloured.
 *
 * Colour is never the only channel. Every helper returns a label alongside its
 * tone, and every component that uses a tone also renders that label - a
 * reader who cannot distinguish the hues loses nothing.
 */

export type Tone = 'positive' | 'warning' | 'attention' | 'danger' | 'neutral' | 'brand'

export interface ToneClasses {
  readonly text: string
  readonly surface: string
  readonly border: string
  readonly fill: string
}

const TONES: Record<Tone, ToneClasses> = {
  positive: {
    text: 'text-positive',
    surface: 'bg-positive-surface',
    border: 'border-positive/30',
    fill: 'fill-positive',
  },
  warning: {
    text: 'text-warning',
    surface: 'bg-warning-surface',
    border: 'border-warning/30',
    fill: 'fill-warning',
  },
  attention: {
    text: 'text-attention',
    surface: 'bg-attention-surface',
    border: 'border-attention/30',
    fill: 'fill-attention',
  },
  danger: {
    text: 'text-danger',
    surface: 'bg-danger-surface',
    border: 'border-danger/30',
    fill: 'fill-danger',
  },
  neutral: {
    text: 'text-neutral',
    surface: 'bg-neutral-surface',
    border: 'border-border-subtle',
    fill: 'fill-neutral',
  },
  brand: {
    text: 'text-brand',
    surface: 'bg-brand/10',
    border: 'border-brand/30',
    fill: 'fill-brand',
  },
}

export function toneClasses(tone: Tone): ToneClasses {
  return TONES[tone]
}

/** APPROVE positive, STEP_UP warning, REVIEW attention, BLOCK danger. */
export function decisionTone(decision: string | null | undefined): Tone {
  switch (decision?.toUpperCase()) {
    case 'APPROVE':
      return 'positive'
    case 'STEP_UP':
      return 'warning'
    case 'REVIEW':
      return 'attention'
    case 'BLOCK':
      return 'danger'
    default:
      return 'neutral'
  }
}

/** A short glyph so the state is legible without colour. */
export function decisionGlyph(decision: string | null | undefined): string {
  switch (decision?.toUpperCase()) {
    case 'APPROVE':
      return '✓'
    case 'STEP_UP':
      return '↑'
    case 'REVIEW':
      return '⚑'
    case 'BLOCK':
      return '✕'
    default:
      return '–'
  }
}

export function decisionLabel(decision: string | null | undefined): string {
  if (!decision) return 'Not decided'
  return decision.toUpperCase().replace('_', '-')
}

export function severityTone(severity: string | null | undefined): Tone {
  switch (severity?.toUpperCase()) {
    case 'CRITICAL':
      return 'danger'
    case 'HIGH':
      return 'warning'
    case 'MEDIUM':
      return 'attention'
    case 'LOW':
      return 'positive'
    case 'INFO':
      return 'neutral'
    default:
      return 'neutral'
  }
}

export function riskLevelTone(level: string | null | undefined): Tone {
  return severityTone(level)
}

export function healthTone(status: string): Tone {
  switch (status) {
    case 'ok':
      return 'positive'
    case 'degraded':
      return 'warning'
    default:
      return 'danger'
  }
}

/** Review-queue status, which is not a decision and must not look like one. */
export function reviewStatusTone(status: string | null | undefined): Tone {
  switch (status?.toLowerCase()) {
    case 'open':
      return 'attention'
    case 'in_review':
      return 'brand'
    case 'escalated':
      return 'warning'
    case 'resolved':
      return 'neutral'
    default:
      return 'neutral'
  }
}

export function resolutionTone(resolution: string | null | undefined): Tone {
  switch (resolution?.toLowerCase()) {
    case 'approved':
      return 'positive'
    case 'rejected':
      return 'danger'
    case 'escalated':
      return 'warning'
    default:
      return 'neutral'
  }
}
