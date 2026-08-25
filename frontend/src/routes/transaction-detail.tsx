import type { ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'

import { RiskGauge } from '@/components/risk/risk-gauge'
import { RiskPipeline } from '@/components/risk/risk-pipeline'
import {
  Badge,
  DecisionBadge,
  ResolutionBadge,
  ReviewStatusBadge,
  SeverityBadge,
} from '@/components/ui/badge'
import { Card, CardHeader, CardNote } from '@/components/ui/card'
import { PageHeader } from '@/components/ui/page-header'
import { QueryBoundary, Skeleton } from '@/components/ui/states'
import { StageFlow, Timeline, type FlowStage, type TimelineEntry } from '@/components/ui/timeline'
import { useQuery } from '@/hooks/use-query'
import { api, type Evidence, type Finding, type TransactionDetail } from '@/lib/api'
import {
  formatAmount,
  formatDateTime,
  formatLatency,
  formatProbability,
  humanizeCode,
} from '@/lib/format'
import { decisionTone, severityTone } from '@/lib/risk'

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
        {label}
      </dt>
      <dd className="text-sm text-content">{children}</dd>
    </div>
  )
}

/**
 * One entity the transaction touched.
 *
 * Customer, device, IP, merchant and location shown as a row of chips rather
 * than as eight more rows of a definition list. A ring is recognised by the
 * *shared* entity, so the thing worth making scannable is the identifier
 * itself.
 */
function EntityChip({
  kind,
  value,
  note,
  flag,
}: {
  kind: string
  value: string | null
  note?: string | null | undefined
  flag?: ReactNode | undefined
}) {
  return (
    <div className="min-w-0 rounded-lg border border-border-subtle bg-surface-sunken/50 px-3 py-2">
      <p className="text-[0.6rem] font-semibold tracking-[0.1em] text-content-faint uppercase">
        {kind}
      </p>
      <p className="numeric mt-0.5 truncate text-xs font-medium text-content" title={value ?? ''}>
        {value ?? 'none recorded'}
      </p>
      {note || flag ? (
        <p className="mt-1 flex items-center gap-1.5 text-[0.65rem] text-content-muted">
          {note}
          {flag}
        </p>
      ) : null}
    </div>
  )
}

/**
 * Evidence, rendered exactly as the API returned it.
 *
 * Nothing here is invented, reworded or inferred by the frontend: each item
 * shows the id, the tool that produced it, the claim that tool wrote, the
 * measured value and the severity. That is the whole grounding chain, visible.
 */
function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  if (evidence.length === 0) {
    return <p className="text-sm text-content-muted">No evidence was recorded.</p>
  }
  return (
    <ul className="grid gap-2 md:grid-cols-2">
      {evidence.map((item) => (
        <li
          key={item.evidence_id}
          className="flex min-w-0 flex-col gap-1.5 rounded-lg border border-border-subtle bg-surface-raised p-3 shadow-flat"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="numeric rounded bg-surface-sunken px-1.5 py-0.5 text-[0.65rem] font-semibold text-content">
              {item.evidence_id}
            </span>
            <SeverityBadge severity={item.severity} />
            <span className="numeric ml-auto text-[0.65rem] text-content-faint">
              {item.source_tool}
            </span>
          </div>
          <p className="text-sm leading-relaxed text-content-muted">{item.claim}</p>
          {item.value !== null || Object.keys(item.details).length > 0 ? (
            <p className="numeric border-t border-border-subtle pt-1.5 text-[0.65rem] break-words text-content-faint">
              {item.value !== null ? `value ${item.value}` : null}
              {item.value !== null && Object.keys(item.details).length > 0 ? ' · ' : null}
              {Object.entries(item.details)
                .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
                .join(' · ')}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  )
}

function FindingList({ findings }: { findings: Finding[] }) {
  if (findings.length === 0) {
    return (
      <p className="text-sm text-content-muted">
        No findings were produced. An investigation without findings is reported as such rather
        than filled in.
      </p>
    )
  }
  return (
    <ul className="flex flex-col gap-3">
      {findings.map((finding) => (
        <li
          key={finding.finding_id}
          className="rounded-lg border border-border-subtle bg-surface-sunken/40 p-3.5"
        >
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={finding.severity} />
            <span className="text-sm font-semibold text-content">{finding.title}</span>
            <span className="numeric ml-auto text-[0.65rem] text-content-faint">
              {finding.finding_id}
            </span>
          </div>
          <p className="mt-1.5 text-sm leading-relaxed text-content-muted">{finding.explanation}</p>
          <p className="mt-2 flex flex-wrap items-center gap-1">
            <span className="text-[0.65rem] text-content-faint">cites</span>
            {finding.evidence_ids.map((id) => (
              <span
                key={id}
                className="numeric rounded border border-border-subtle bg-surface-raised px-1.5 py-0.5 text-[0.65rem] text-content-muted"
              >
                {id}
              </span>
            ))}
          </p>
        </li>
      ))}
    </ul>
  )
}

function DecisionPanel({ detail }: { detail: TransactionDetail }) {
  const decision = detail.decision
  if (!decision) {
    return (
      <p className="text-sm text-content-muted">
        This transaction has not been through the decision engine.
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <dl className="grid gap-3 sm:grid-cols-2">
        <Field label="Matched rules">
          <span className="numeric text-xs">{decision.matched_rules.join(', ') || '--'}</span>
        </Field>
        <Field label="Deciding rules">
          <span className="numeric text-xs">{decision.deciding_rules.join(', ') || '--'}</span>
        </Field>
      </dl>

      <div>
        <p className="mb-1.5 text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
          Reason codes
        </p>
        <div className="flex flex-wrap gap-1.5">
          {decision.reason_codes.map((code) => (
            <Badge key={code} tone="neutral" title={code}>
              {humanizeCode(code)}
            </Badge>
          ))}
        </div>
      </div>

      {decision.rule_matches.length > 0 ? (
        <div>
          <p className="mb-1.5 text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
            Conditions evaluated
          </p>
          <ul className="flex flex-col gap-2">
            {decision.rule_matches.map((match) => (
              <li
                key={match.rule_id}
                className="rounded-lg border border-border-subtle bg-surface-sunken/40 p-2.5"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="numeric text-xs font-semibold">{match.rule_id}</span>
                  <DecisionBadge decision={match.action} />
                </div>
                <ul className="mt-1.5 flex flex-col gap-0.5">
                  {match.conditions.map((condition) => (
                    <li key={condition} className="numeric text-xs text-content-muted">
                      {condition}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <details className="rounded-lg border border-border-subtle bg-surface-sunken/40 p-3">
        <summary className="cursor-pointer text-sm font-medium">
          Full explanation (deterministic, not model-generated)
        </summary>
        <pre className="mt-2 overflow-x-auto text-xs leading-relaxed whitespace-pre-wrap text-content-muted">
          {decision.explanation}
        </pre>
      </details>
    </div>
  )
}

/** The five-stage story, driven by what this transaction actually has. */
function storyStages(detail: TransactionDetail): FlowStage[] {
  const { signals, investigation, decision } = detail
  return [
    {
      key: 'transaction',
      label: 'Transaction',
      value: formatAmount(detail.transaction.amount, detail.transaction.currency),
      state: 'done',
    },
    {
      key: 'fraud',
      label: 'Fraud model',
      value: formatProbability(signals.fraud_probability),
      state: signals.fraud_probability === null ? 'skipped' : 'done',
      tone: severityTone(signals.risk_level),
      hint: signals.fraud_model_version ?? undefined,
    },
    {
      key: 'anomaly',
      label: 'Anomaly',
      value: signals.anomaly_score === null ? 'not scored' : `${signals.anomaly_score} / 100`,
      state: signals.anomaly_score === null ? 'skipped' : 'done',
      tone: severityTone(signals.anomaly_severity),
      hint: signals.anomaly_model_version ?? undefined,
    },
    {
      key: 'investigation',
      label: 'Investigation',
      value: investigation ? (investigation.risk_level ?? investigation.status) : 'not run',
      state: investigation ? 'done' : 'skipped',
      tone: severityTone(investigation?.risk_level),
      hint: investigation
        ? `${investigation.findings.length} findings from ${investigation.evidence.length} evidence items`
        : 'Neither model raised a concern the policy wants investigated.',
    },
    {
      key: 'evidence',
      label: 'Evidence',
      value: investigation ? `${investigation.evidence.length} items` : 'none',
      state: investigation && investigation.evidence.length > 0 ? 'done' : 'skipped',
      tone: 'brand',
    },
    {
      key: 'decision',
      label: 'Decision',
      value: decision?.decision ?? 'not decided',
      state: decision ? 'done' : 'pending',
      tone: decisionTone(decision?.decision),
      hint: decision ? `policy ${decision.policy_version}` : undefined,
    },
  ]
}

export function TransactionDetailPage() {
  const { transactionId = '' } = useParams<{ transactionId: string }>()
  const query = useQuery((signal) => api.transactionDetail(transactionId, signal), [transactionId])
  // The policy's own thresholds, so the gauges can mark the lines this
  // transaction fell on either side of. Never hardcoded here.
  const policy = useQuery((signal) => api.policy(signal), [])

  const thresholds = policy.data?.thresholds
  const detail = query.data

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={
          <Link to="/transactions" className="hover:underline">
            ← Transactions
          </Link>
        }
        title={transactionId}
        description={
          detail ? (
            <span className="inline-flex flex-wrap items-center gap-2">
              <span className="numeric text-base font-semibold text-content">
                {formatAmount(detail.transaction.amount, detail.transaction.currency)}
              </span>
              <span className="text-content-faint">·</span>
              <span>{formatDateTime(detail.transaction.timestamp)}</span>
              <span className="text-content-faint">·</span>
              <span>{detail.transaction.merchant_name}</span>
            </span>
          ) : undefined
        }
        actions={
          detail ? (
            <div className="flex flex-wrap items-center gap-2">
              {detail.transaction.is_fraud ? (
                <Badge tone="danger" glyph="!" title="Dataset ground-truth label">
                  LABELLED FRAUD
                </Badge>
              ) : null}
              {detail.decision?.requires_human_review ? (
                <Badge tone="attention">HUMAN REVIEW</Badge>
              ) : null}
              <DecisionBadge decision={detail.decision?.decision} className="px-3 py-1 text-sm" />
            </div>
          ) : (
            <Skeleton className="h-7 w-28" />
          )
        }
        className="[&_h1]:numeric [&_h1]:text-lg [&_h1]:break-all sm:[&_h1]:text-xl"
      />

      <QueryBoundary
        loading={query.loading}
        error={query.error}
        data={query.data}
        onRetry={query.refetch}
        skeleton={
          <div className="flex flex-col gap-4">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-64 w-full" />
          </div>
        }
      >
        {(data) => (
          <div className="flex flex-col gap-5">
            {/* --- the story, in one strip -------------------------------- */}
            <Card>
              <CardHeader
                title="How this decision was reached"
                description="Every stage with this transaction's real values. The policy engine decides - no language model participates."
              />
              <div className="mt-4">
                <StageFlow stages={storyStages(data)} />
              </div>

              <div className="mt-5 grid gap-5 border-t border-border-subtle pt-4 sm:grid-cols-2">
                <RiskGauge
                  label="Fraud probability"
                  value={data.signals.fraud_probability}
                  max={1}
                  valueLabel={formatProbability(data.signals.fraud_probability)}
                  tone={severityTone(data.signals.risk_level)}
                  markers={
                    thresholds
                      ? [
                          { at: thresholds.fraud_medium, label: 'medium' },
                          { at: thresholds.fraud_high, label: 'high' },
                          { at: thresholds.fraud_block, label: 'block' },
                        ]
                      : []
                  }
                />
                <RiskGauge
                  label="Anomaly score"
                  value={data.signals.anomaly_score}
                  max={100}
                  valueLabel={`${data.signals.anomaly_score ?? '--'} / 100`}
                  tone={severityTone(data.signals.anomaly_severity)}
                  markers={
                    thresholds
                      ? [
                          { at: thresholds.anomaly_medium, label: 'medium' },
                          { at: thresholds.anomaly_high, label: 'high' },
                          { at: thresholds.anomaly_critical, label: 'critical' },
                        ]
                      : []
                  }
                />
              </div>
              <CardNote>
                The two engines are independent. When they disagree, the policy resolves it by
                rule - the higher signal does not simply win.
              </CardNote>
            </Card>

            {/* --- entities ------------------------------------------------ */}
            <Card>
              <CardHeader
                title="Entities"
                description="What this payment touched. A coordinated ring is recognised by the entity two transactions share."
              />
              <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
                <EntityChip
                  kind="Customer"
                  value={data.transaction.customer_id}
                  note={data.transaction.customer_country}
                  flag={
                    data.transaction.customer_historical_risk_level ? (
                      <SeverityBadge severity={data.transaction.customer_historical_risk_level} />
                    ) : undefined
                  }
                />
                <EntityChip
                  kind="Merchant"
                  value={data.transaction.merchant_id}
                  note={data.transaction.merchant_category}
                />
                <EntityChip
                  kind="Device"
                  value={data.transaction.device_id}
                  note={data.transaction.device_type}
                />
                <EntityChip
                  kind="IP address"
                  value={data.transaction.ip_address}
                  note={data.transaction.ip_country}
                  flag={
                    data.transaction.ip_is_proxy ? <Badge tone="danger">PROXY</Badge> : undefined
                  }
                />
                <EntityChip
                  kind="Location"
                  value={`${data.transaction.city}, ${data.transaction.country}`}
                  note={
                    data.transaction.country !== data.transaction.customer_country
                      ? 'cross-border'
                      : 'domestic'
                  }
                />
              </div>
            </Card>

            <div className="grid gap-4 lg:grid-cols-2">
              {/* --- transaction ------------------------------------------ */}
              <Card>
                <CardHeader title="Transaction" />
                <dl className="mt-4 grid gap-3 sm:grid-cols-2">
                  <Field label="Amount">
                    <span className="numeric font-semibold">
                      {formatAmount(data.transaction.amount, data.transaction.currency)}
                    </span>
                  </Field>
                  <Field label="Timestamp">
                    <span className="numeric text-xs">
                      {formatDateTime(data.transaction.timestamp)}
                    </span>
                  </Field>
                  <Field label="Status">{data.transaction.status}</Field>
                  <Field label="Payment method">{data.transaction.payment_method}</Field>
                  <Field label="Customer since">
                    <span className="numeric text-xs">
                      {formatDateTime(data.transaction.customer_since)}
                    </span>
                  </Field>
                  <Field label="Failed attempts">
                    <span className="numeric">{data.transaction.failed_attempts}</span>
                  </Field>
                </dl>
              </Card>

              {/* --- ML signals -------------------------------------------- */}
              <Card>
                <CardHeader
                  title="Model signals"
                  description="Two independent assessments. Neither overrides the other."
                />
                <dl className="mt-4 grid gap-3 sm:grid-cols-2">
                  <Field label="Fraud probability">
                    <span className="numeric text-lg font-semibold">
                      {formatProbability(data.signals.fraud_probability)}
                    </span>
                  </Field>
                  <Field label="Risk score">
                    <span className="numeric">{data.signals.risk_score ?? '--'} / 100</span>
                  </Field>
                  <Field label="Risk level">
                    <SeverityBadge severity={data.signals.risk_level} />
                  </Field>
                  <Field label="Fraud model">
                    <span className="numeric text-xs">
                      {data.signals.fraud_model_version ?? '--'}
                    </span>
                  </Field>
                  <Field label="Anomaly score">
                    <span className="numeric text-lg font-semibold">
                      {data.signals.anomaly_score ?? '--'} / 100
                    </span>
                  </Field>
                  <Field label="Customer deviation">
                    <span className="numeric">
                      {data.signals.customer_deviation_score ?? '--'}
                    </span>
                  </Field>
                  <Field label="Anomaly severity">
                    <SeverityBadge severity={data.signals.anomaly_severity} />
                  </Field>
                  <Field label="Anomaly model">
                    <span className="numeric text-xs">
                      {data.signals.anomaly_model_version ?? '--'}
                    </span>
                  </Field>
                </dl>
              </Card>
            </div>

            {/* --- the full pipeline breakdown ---------------------------- */}
            <Card>
              <CardHeader
                title="Decision pipeline"
                description="The same story, expanded: what each stage measured and what the policy did with it."
              />
              <div className="mt-4">
                <RiskPipeline detail={data} />
              </div>
            </Card>

            {/* --- investigation ------------------------------------------- */}
            <Card>
              <CardHeader
                title="AI investigation"
                description="Findings cite evidence a tool actually produced. The agent investigates and explains; it does not decide."
                actions={
                  data.investigation ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <SeverityBadge severity={data.investigation.risk_level} />
                      <Badge tone="neutral">{data.investigation.status}</Badge>
                      {data.investigation.agent_is_mock ? (
                        <Badge tone="warning" title="Produced by the deterministic test double">
                          MOCK PROVIDER
                        </Badge>
                      ) : null}
                    </div>
                  ) : null
                }
              />

              {data.investigation ? (
                <div className="mt-4 flex flex-col gap-5">
                  <dl className="grid gap-3 rounded-lg border border-border-subtle bg-surface-sunken/40 p-3 sm:grid-cols-3">
                    <Field label="Investigation ID">
                      <span className="numeric text-xs break-all">
                        {data.investigation.investigation_id}
                      </span>
                    </Field>
                    <Field label="Confidence">
                      <span className="numeric">
                        {formatProbability(data.investigation.confidence)}
                      </span>
                      <span className="ml-1.5 text-xs text-content-faint">
                        application-computed
                      </span>
                    </Field>
                    <Field label="Agent recommendation">
                      <span className="numeric text-xs">
                        {data.investigation.recommended_action ?? '--'}
                      </span>
                      <span className="ml-1.5 text-xs text-content-faint">
                        advisory - not a policy input
                      </span>
                    </Field>
                  </dl>

                  {data.investigation.summary ? (
                    <div>
                      <p className="mb-1 text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
                        Summary
                      </p>
                      <p className="text-sm leading-relaxed text-content-muted">
                        {data.investigation.summary}
                      </p>
                    </div>
                  ) : null}

                  <div>
                    <p className="mb-2 text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
                      Findings ({data.investigation.findings.length})
                    </p>
                    <FindingList findings={data.investigation.findings} />
                  </div>

                  <div>
                    <p className="mb-2 text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
                      Evidence ({data.investigation.evidence.length})
                    </p>
                    <EvidenceList evidence={data.investigation.evidence} />
                  </div>
                </div>
              ) : (
                <p className="mt-4 text-sm leading-relaxed text-content-muted">
                  No investigation has been run for this transaction. Without one the policy has no
                  independent corroboration, which is why a high-probability transaction can still
                  fall short of a block.
                </p>
              )}
            </Card>

            {/* --- decision + audit ---------------------------------------- */}
            <div className="grid gap-4 lg:grid-cols-3">
              <Card className="lg:col-span-2">
                <CardHeader
                  title="Policy decision"
                  description="Produced by deterministic rules over measured values."
                  actions={<DecisionBadge decision={data.decision?.decision} />}
                />
                <div className="mt-4">
                  <DecisionPanel detail={data} />
                </div>
              </Card>

              <Card>
                <CardHeader
                  title="Provenance"
                  description="Everything needed to reconstruct this decision."
                />
                <dl className="mt-4 flex flex-col gap-3">
                  <Field label="Decision ID">
                    <span className="numeric text-xs break-all">
                      {data.decision?.decision_id ?? '--'}
                    </span>
                  </Field>
                  <Field label="Investigation ID">
                    <span className="numeric text-xs break-all">
                      {data.investigation?.investigation_id ?? '--'}
                    </span>
                  </Field>
                  <Field label="Decided at">
                    <span className="numeric text-xs">
                      {formatDateTime(data.decision?.decided_at)}
                    </span>
                  </Field>
                  <Field label="Policy version">
                    <span className="numeric text-xs">{data.decision?.policy_version ?? '--'}</span>
                  </Field>
                  <Field label="Model versions">
                    <span className="numeric text-xs break-all">
                      {data.signals.fraud_model_version ?? '--'} ·{' '}
                      {data.signals.anomaly_model_version ?? '--'}
                    </span>
                  </Field>
                  <Field label="Decision digest">
                    <span className="numeric text-xs break-all">
                      {data.decision?.input_digest ?? '--'}
                    </span>
                  </Field>
                  <Field label="Evaluation time">
                    {formatLatency(data.decision?.evaluation_ms)}
                  </Field>
                  <Field label="Decisions recorded">
                    <span className="numeric">{data.decision?.history_count ?? 0}</span>
                    <span className="ml-1.5 text-xs text-content-faint">append-only</span>
                  </Field>
                </dl>

                {data.review ? (
                  <div className="mt-4 border-t border-border-subtle pt-4">
                    <p className="mb-2 text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
                      Human review
                    </p>
                    <div className="flex flex-wrap items-center gap-2">
                      <ReviewStatusBadge status={data.review.status} />
                      <ResolutionBadge resolution={data.review.resolution} />
                    </div>
                    {data.review.resolution_reason ? (
                      <p className="mt-2 text-sm text-content-muted">
                        {data.review.resolution_reason}
                      </p>
                    ) : null}
                    <Link
                      to="/reviews"
                      className="mt-2 inline-block text-sm font-medium text-brand hover:underline"
                    >
                      Open review queue →
                    </Link>
                  </div>
                ) : null}
              </Card>
            </div>

            {/* --- audit trail ---------------------------------------------- */}
            <Card>
              <CardHeader
                title="Audit trail"
                description="Every recorded event for this transaction, oldest first."
              />
              <div className="mt-4">
                {data.audit.length === 0 ? (
                  <p className="text-sm text-content-muted">No events recorded.</p>
                ) : (
                  <Timeline
                    entries={[...data.audit]
                      .sort(
                        (a, b) =>
                          new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
                      )
                      .map<TimelineEntry>((entry) => ({
                        id: String(entry.audit_id),
                        title: humanizeCode(entry.event_type),
                        timestamp: formatDateTime(entry.created_at),
                        tone: entry.decision
                          ? decisionTone(entry.decision)
                          : entry.resolution
                            ? 'brand'
                            : 'neutral',
                        body: (
                          <span className="inline-flex flex-wrap items-center gap-2">
                            <span className="text-xs">
                              {entry.actor_type}
                              {entry.actor_id ? (
                                <span className="numeric ml-1 text-content-faint">
                                  {entry.actor_id}
                                </span>
                              ) : null}
                            </span>
                            {entry.policy_version ? (
                              <span className="numeric text-xs text-content-faint">
                                {entry.policy_version}
                              </span>
                            ) : null}
                          </span>
                        ),
                        meta:
                          entry.decision || entry.resolution ? (
                            <span className="flex flex-wrap gap-2">
                              {entry.decision ? (
                                <DecisionBadge decision={entry.decision} />
                              ) : null}
                              {entry.resolution ? (
                                <ResolutionBadge resolution={entry.resolution} />
                              ) : null}
                            </span>
                          ) : undefined,
                      }))}
                  />
                )}
              </div>
            </Card>
          </div>
        )}
      </QueryBoundary>
    </div>
  )
}
