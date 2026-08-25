import type { ConfusionMatrix as Matrix } from '@/lib/api'
import { formatCount } from '@/lib/format'
import { cn } from '@/lib/utils'

interface Quadrant {
  readonly key: keyof Pick<
    Matrix,
    'true_positive' | 'false_positive' | 'false_negative' | 'true_negative'
  >
  readonly label: string
  readonly meaning: string
  /** True when this quadrant represents the system getting it right. */
  readonly correct: boolean
}

const QUADRANTS: readonly Quadrant[] = [
  {
    key: 'true_positive',
    label: 'True positive',
    meaning: 'Flagged, and the analyst confirmed fraud',
    correct: true,
  },
  {
    key: 'false_positive',
    label: 'False positive',
    meaning: 'Flagged, but the analyst found it legitimate',
    correct: false,
  },
  {
    key: 'false_negative',
    label: 'False negative',
    meaning: 'Approved, but the analyst found fraud',
    correct: false,
  },
  {
    key: 'true_negative',
    label: 'True negative',
    meaning: 'Approved, and the analyst confirmed it was legitimate',
    correct: true,
  },
]

/**
 * Machine decision against analyst ground truth.
 *
 * "Flagged" means anything other than a clean approval - STEP_UP, REVIEW and
 * BLOCK all express suspicion, so all three count as the machine saying yes.
 *
 * Only ground-truth outcomes appear here. INSUFFICIENT_EVIDENCE and ESCALATED
 * leave the question open, and placing them in a quadrant would invent a label
 * the analyst deliberately withheld; they are reported as excluded instead.
 * Unlabelled transactions do not appear at all - absence of a label is not a
 * negative example.
 */
export function ConfusionMatrixGrid({ matrix }: { matrix: Matrix }) {
  const total = matrix.labelled_included

  if (total === 0) {
    return (
      <p className="py-8 text-center text-sm text-content-muted">
        No ground-truth labels yet. Resolve a review case with structured feedback to populate
        this matrix.
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-2">
        {QUADRANTS.map((quadrant) => {
          const count = matrix[quadrant.key]
          const share = total > 0 ? count / total : 0
          return (
            <div
              key={quadrant.key}
              className={cn(
                'rounded-lg border p-3',
                quadrant.correct
                  ? 'border-positive/30 bg-positive-surface'
                  : 'border-danger/30 bg-danger-surface',
              )}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span
                  className={cn(
                    'text-xs font-semibold',
                    quadrant.correct ? 'text-positive' : 'text-danger',
                  )}
                >
                  {quadrant.label}
                </span>
                <span className="numeric text-lg font-semibold text-content">
                  {formatCount(count)}
                </span>
              </div>
              <p className="mt-1 text-xs text-content-muted">{quadrant.meaning}</p>
              <p className="numeric mt-1 text-xs text-content-faint">
                {(share * 100).toFixed(1)}% of labelled
              </p>
            </div>
          )
        })}
      </div>

      <p className="text-xs text-content-faint">
        {formatCount(total)} ground-truth label{total === 1 ? '' : 's'} included.
        {matrix.excluded_open_outcomes > 0 ? (
          <>
            {' '}
            {formatCount(matrix.excluded_open_outcomes)} excluded because the analyst recorded an
            open outcome (insufficient evidence or escalated) rather than a verdict.
          </>
        ) : null}{' '}
        Unlabelled transactions are not shown and are never counted as negatives.
      </p>
    </div>
  )
}
