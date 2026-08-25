import { Fragment } from 'react'

import type { FunnelStage } from '@/lib/api'
import { formatCount } from '@/lib/format'
import { cn } from '@/lib/utils'

/**
 * A decision funnel: how many survive each successive requirement.
 *
 * Every stage shows both its own count and the drop from the stage above,
 * because the drop is the point. A funnel that only shows totals invites the
 * reader to see attrition without seeing where it happened.
 *
 * Bar widths are proportional to the *first* stage so the narrowing is visible
 * at a glance; each bar also carries its number, so nothing depends on
 * estimating a length.
 */
export function DecisionFunnel({ stages }: { stages: FunnelStage[] }) {
  if (stages.length === 0) {
    return <p className="py-8 text-center text-sm text-content-muted">No funnel data.</p>
  }

  const top = Math.max(stages[0]?.count ?? 0, 1)

  return (
    <ol className="flex flex-col">
      {stages.map((stage, index) => {
        const previous = index > 0 ? stages[index - 1] : undefined
        const dropped = previous ? previous.count - stage.count : 0
        const width = Math.max((stage.count / top) * 100, stage.count > 0 ? 2 : 0)
        const isFinal = index === stages.length - 1

        return (
          <Fragment key={stage.stage}>
            {previous && dropped > 0 ? (
              <li
                aria-hidden="true"
                className="flex items-center gap-2 py-0.5 pl-3 text-xs text-danger"
              >
                <span className="text-content-faint">↓</span>
                <span className="numeric">
                  −{formatCount(dropped)} filtered out at this step
                </span>
              </li>
            ) : null}

            <li className="flex flex-col gap-1 py-1.5">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="numeric text-sm font-semibold tracking-tight">
                  {stage.stage.replace(/_/g, ' ')}
                </span>
                <span className="numeric text-sm font-semibold text-content">
                  {formatCount(stage.count)}
                </span>
              </div>
              <div className="h-2.5 w-full overflow-hidden rounded-full bg-surface-sunken">
                <div
                  className={cn('h-full rounded-full', isFinal ? 'bg-danger' : 'bg-brand')}
                  style={{ width: `${width}%` }}
                  role="img"
                  aria-label={`${stage.stage}: ${formatCount(stage.count)}`}
                />
              </div>
              <p className="text-xs text-content-muted">{stage.description}</p>
            </li>
          </Fragment>
        )
      })}
    </ol>
  )
}
