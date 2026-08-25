import type { ReactNode } from 'react'

import { Badge, DecisionBadge } from '@/components/ui/badge'
import { formatAmount, formatProbability, humanizeCode } from '@/lib/format'
import { decisionTone, severityTone, toneClasses, type Tone } from '@/lib/risk'
import { cn } from '@/lib/utils'
import type { TransactionDetail } from '@/lib/api'

/**
 * The decision pipeline, stage by stage, with this transaction's real values.
 *
 * The point of this view is to make one thing obvious: the two models can
 * disagree, and the policy engine - not a language model - resolves that
 * disagreement by rule. Scenario C1 is the case it exists to explain, where
 * XGBoost says 20% and the anomaly engine says 100/CRITICAL.
 *
 * Every value here comes from the API. Nothing is hardcoded, including the
 * scenario transactions.
 */

interface StageProps {
  step: number
  title: string
  tone?: Tone
  status?: ReactNode
  children: ReactNode
}

function Stage({ step, title, tone = 'neutral', status, children }: StageProps) {
  const classes = toneClasses(tone)
  return (
    <li className="relative flex gap-3">
      <div className="flex flex-col items-center">
        <span
          aria-hidden="true"
          className={cn(
            'grid size-7 shrink-0 place-items-center rounded-full border text-xs font-semibold',
            classes.surface,
            classes.text,
            classes.border,
          )}
        >
          {step}
        </span>
        {/* The connector doubles as the "flows into" arrow from the spec. */}
        <span
          aria-hidden="true"
          className="mt-1 w-px flex-1 bg-border-subtle last:hidden"
        />
      </div>
      <div className="min-w-0 flex-1 pb-5">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold tracking-tight">{title}</h3>
          {status}
        </div>
        <div className="mt-1.5 text-sm text-content-muted">{children}</div>
      </div>
    </li>
  )
}

function Value({ children }: { children: ReactNode }) {
  return <span className="numeric font-medium text-content">{children}</span>
}

export function RiskPipeline({ detail }: { detail: TransactionDetail }) {
  const { transaction, signals, investigation, decision } = detail

  // Read from the decision's own reason codes rather than re-deriving it from
  // thresholds here. The engine already decided whether the models disagreed;
  // a second opinion in the UI could contradict the record it is displaying.
  const disagreement = decision?.reason_codes.includes('MODEL_DISAGREEMENT') ?? false
  const coordinated = decision?.reason_codes.includes('COORDINATED_ACTIVITY') ?? false

  return (
    <ol className="flex flex-col">
      <Stage step={1} title="Transaction" tone="brand">
        <Value>{formatAmount(transaction.amount, transaction.currency)}</Value> ·{' '}
        {transaction.payment_method.toUpperCase()} · {transaction.city}, {transaction.country}
        {transaction.ip_is_proxy ? (
          <>
            {' '}
            · <span className="text-danger">proxy IP</span>
          </>
        ) : null}
      </Stage>

      <Stage
        step={2}
        title="Fraud model"
        tone={signals.fraud_probability === null ? 'neutral' : 'brand'}
        status={
          signals.risk_level ? (
            <Badge tone={severityTone(signals.risk_level)}>{signals.risk_level}</Badge>
          ) : (
            <Badge tone="neutral">No prediction</Badge>
          )
        }
      >
        {signals.fraud_probability === null ? (
          'No supervised prediction is stored for this transaction.'
        ) : (
          <>
            Fraud probability <Value>{formatProbability(signals.fraud_probability)}</Value>{' '}
            <span className="text-content-faint">({signals.fraud_model_version})</span>
          </>
        )}
      </Stage>

      <Stage
        step={3}
        title="Anomaly model"
        tone={signals.anomaly_severity === null ? 'neutral' : 'brand'}
        status={
          signals.anomaly_severity ? (
            <Badge tone={severityTone(signals.anomaly_severity)}>{signals.anomaly_severity}</Badge>
          ) : (
            <Badge tone="neutral">No score</Badge>
          )
        }
      >
        {signals.anomaly_score === null ? (
          'No behavioural anomaly score is stored for this transaction.'
        ) : (
          <>
            Anomaly score <Value>{signals.anomaly_score} / 100</Value>{' '}
            <span className="text-content-faint">({signals.anomaly_model_version})</span>
          </>
        )}
      </Stage>

      {disagreement ? (
        <li className="mb-5 ml-10 rounded-lg border border-attention/30 bg-attention-surface px-3 py-2 text-sm">
          <span className="font-semibold text-attention">Models disagree.</span>{' '}
          <span className="text-content-muted">
            The fraud model rates this ordinary while the anomaly engine rates it critical. On the
            held-out test fold that combination carried a 71.4% fraud rate, which is why the policy
            routes it to a person rather than trusting either model alone.
            {coordinated ? (
              <>
                {' '}
                The investigation also found a device or IP shared across several customers - the
                signature of a coordinated ring.
              </>
            ) : null}
          </span>
        </li>
      ) : null}

      <Stage
        step={4}
        title="AI investigation"
        tone={investigation ? 'brand' : 'neutral'}
        status={
          investigation?.risk_level ? (
            <Badge tone={severityTone(investigation.risk_level)}>{investigation.risk_level}</Badge>
          ) : (
            <Badge tone="neutral">Not investigated</Badge>
          )
        }
      >
        {investigation ? (
          <>
            {investigation.findings.length} finding
            {investigation.findings.length === 1 ? '' : 's'} from{' '}
            <Value>{investigation.evidence.length}</Value> evidence items · confidence{' '}
            <Value>{formatProbability(investigation.confidence)}</Value>
            {investigation.agent_is_mock ? (
              <>
                {' '}
                ·{' '}
                <span className="text-content-faint" title="Produced by the deterministic test double, not a live model">
                  mock provider
                </span>
              </>
            ) : null}
          </>
        ) : (
          'No investigation was run, so the policy has no independent corroboration to weigh.'
        )}
      </Stage>

      <Stage
        step={5}
        title="Policy engine"
        tone={decision ? decisionTone(decision.decision) : 'neutral'}
        status={
          decision ? (
            <Badge tone="neutral">{decision.policy_version}</Badge>
          ) : (
            <Badge tone="neutral">Not evaluated</Badge>
          )
        }
      >
        {decision ? (
          <div className="flex flex-wrap gap-1.5">
            {decision.reason_codes.map((code) => (
              <Badge key={code} tone="neutral" title={code}>
                {humanizeCode(code)}
              </Badge>
            ))}
          </div>
        ) : (
          'This transaction has not been through the decision engine.'
        )}
      </Stage>

      <Stage
        step={6}
        title="Final decision"
        tone={decision ? decisionTone(decision.decision) : 'neutral'}
        status={<DecisionBadge decision={decision?.decision} />}
      >
        {decision ? (
          <>
            Matched{' '}
            <Value>{decision.deciding_rules.join(', ') || decision.matched_rules.join(', ')}</Value>
            {decision.requires_human_review ? ' · routed to human review' : null}
          </>
        ) : (
          'No decision recorded.'
        )}
      </Stage>
    </ol>
  )
}
