import { useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'

import { DriftCard } from '@/components/charts/drift-card'
import { DecisionFunnel } from '@/components/charts/funnel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { PageHeader } from '@/components/ui/page-header'
import { EmptyState, QueryBoundary } from '@/components/ui/states'
import { Stat } from '@/components/ui/stat'
import { DataTable, Td, Th, Tr } from '@/components/ui/table'
import { useQuery } from '@/hooks/use-query'
import { api } from '@/lib/api'
import { formatCount, formatDate, formatPercent, humanizeCode } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { Tone } from '@/lib/risk'

const TABS = [
  { to: '/monitoring/models', label: 'Models' },
  { to: '/monitoring/drift', label: 'Drift' },
  { to: '/monitoring/policy', label: 'Policy effectiveness' },
] as const

function MonitoringTabs() {
  return (
    <nav
      aria-label="Monitoring sections"
      className="inline-flex flex-wrap gap-0.5 rounded-lg border border-border-subtle bg-surface-sunken p-0.5"
    >
      {TABS.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          className={({ isActive }) =>
            cn(
              'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
              isActive
                ? 'bg-surface-raised text-content shadow-flat'
                : 'text-content-muted hover:text-content',
            )
          }
        >
          {tab.label}
        </NavLink>
      ))}
    </nav>
  )
}

/** Severity of a recommendation, mapped to the shared tone vocabulary. */
function severityTone(severity: string): Tone {
  switch (severity) {
    case 'high':
      return 'danger'
    case 'medium':
      return 'warning'
    default:
      return 'neutral'
  }
}

function Recommendations() {
  const query = useQuery((signal) => api.recommendations(signal), [])

  return (
    <Card>
      <CardTitle>Recommendations</CardTitle>
      <CardDescription>
        Analytical only. Nothing here is executed - no model is retrained, no threshold moved, no
        policy edited and no decision revised.
      </CardDescription>
      <div className="mt-4">
        <QueryBoundary
          loading={query.loading}
          error={query.error}
          data={query.data}
          onRetry={query.refetch}
          isEmpty={(data) => data.recommendations.length === 0}
          emptyTitle="Nothing to flag"
          emptyDescription="No metric currently crosses a threshold that warrants attention."
        >
          {(data) => (
            <ul className="flex flex-col gap-3">
              {data.recommendations.map((item) => (
                <li key={item.id} className="rounded-lg border border-border-subtle p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={severityTone(item.severity)}>{item.severity.toUpperCase()}</Badge>
                    <span className="text-sm font-medium">{item.title}</span>
                  </div>
                  <p className="mt-1.5 text-sm text-content-muted">{item.detail}</p>
                  <p className="numeric mt-1.5 text-xs text-content-faint">
                    Source: {item.metric_source} · Requires: {humanizeCode(item.action_required)}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </QueryBoundary>
      </div>
    </Card>
  )
}

/**
 * The risk learning assistant.
 *
 * A closed set of questions answered from structured metrics. No language model
 * is involved, and every answer names the endpoints its figures came from.
 */
function Assistant() {
  const [topic, setTopic] = useState<string | null>(null)
  const questions = useQuery((signal) => api.assistantQuestions(signal), [])
  const answer = useQuery(
    (signal) => (topic ? api.assistantAnswer(topic, signal) : Promise.resolve(null)),
    [topic],
  )

  return (
    <Card>
      <CardTitle>Risk learning assistant</CardTitle>
      <CardDescription>
        Answers built from backend metrics. No language model is involved and no figure is
        generated - every number comes from a query, and every answer names its sources.
      </CardDescription>

      <div className="mt-4 flex flex-wrap gap-2">
        {questions.data?.questions.map((item) => (
          <Button
            key={item.topic}
            variant={topic === item.topic ? 'primary' : 'secondary'}
            onClick={() => setTopic(item.topic)}
          >
            {item.question}
          </Button>
        ))}
      </div>

      {topic ? (
        <div className="mt-4">
          <QueryBoundary
            loading={answer.loading}
            error={answer.error}
            data={answer.data ?? undefined}
            onRetry={answer.refetch}
          >
            {(data) => (
              <div
                className={cn(
                  'rounded-lg border p-3',
                  data.sufficient
                    ? 'border-border-subtle bg-surface-sunken/50'
                    : 'border-warning/30 bg-warning-surface',
                )}
              >
                <p className="text-sm font-medium">{data.question}</p>
                <p className="mt-1.5 text-sm text-content-muted">{data.answer}</p>
                <dl className="mt-3 grid gap-1 border-t border-border-subtle pt-2 text-xs">
                  <div className="flex gap-2">
                    <dt className="text-content-faint">Sources</dt>
                    <dd className="numeric text-content-muted">
                      {data.metric_sources.join(', ')}
                    </dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="text-content-faint">Window</dt>
                    <dd className="text-content-muted">{data.time_window}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="text-content-faint">Data</dt>
                    <dd className="text-content-muted">{data.data_availability}</dd>
                  </div>
                </dl>
              </div>
            )}
          </QueryBoundary>
        </div>
      ) : (
        <p className="mt-4 text-sm text-content-faint">
          Choose a question. The assistant will answer it from measured data, or say that the data
          cannot.
        </p>
      )}
    </Card>
  )
}

// --------------------------------------------------------------------------
// Models
// --------------------------------------------------------------------------
function ModelsPage() {
  const models = useQuery((signal) => api.modelMonitoring(signal), [])
  const scores = useQuery((signal) => api.scoreWindows(signal), [])
  const funnel = useQuery((signal) => api.highRiskFunnel(signal), [])

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardTitle>Model performance</CardTitle>
        <CardDescription>
          Computed from analyst-labelled transactions only. Unlabelled transactions are excluded,
          never counted as legitimate.
        </CardDescription>
        <div className="mt-4">
          <QueryBoundary
            loading={models.loading}
            error={models.error}
            data={models.data}
            onRetry={models.refetch}
          >
            {(data) =>
              data.metrics.sufficient ? (
                <div className="flex flex-col gap-4">
                  <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-5">
                    <Stat
                      label="Precision"
                      value={formatPercent(data.metrics.precision)}
                      scope={`${formatCount(data.metrics.labelled_samples)} labelled`}
                    />
                    <Stat
                      label="Recall"
                      value={formatPercent(data.metrics.recall)}
                      scope="Of confirmed fraud, share caught"
                    />
                    <Stat
                      label="F1"
                      value={data.metrics.f1 === null ? '--' : data.metrics.f1.toFixed(3)}
                      scope="Harmonic mean"
                    />
                    <Stat
                      label="False positive rate"
                      value={formatPercent(data.metrics.false_positive_rate)}
                      scope="Of confirmed legitimate"
                      tone="warning"
                    />
                    <Stat
                      label="False negative rate"
                      value={formatPercent(data.metrics.false_negative_rate)}
                      scope="Of confirmed fraud"
                      tone="danger"
                    />
                  </div>

                  {data.metrics.selection_bias_note ? (
                    <p className="rounded-lg border border-warning/30 bg-warning-surface px-3 py-2 text-sm text-content-muted">
                      <span className="font-semibold text-warning">Sampling caveat.</span>{' '}
                      {data.metrics.selection_bias_note}
                    </p>
                  ) : null}

                  <DataTable className="min-w-[26rem]">
                    <thead>
                      <tr>
                        <Th>Quadrant</Th>
                        <Th numeric>Count</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {(
                        [
                          ['True positive', data.metrics.true_positive],
                          ['False positive', data.metrics.false_positive],
                          ['True negative', data.metrics.true_negative],
                          ['False negative', data.metrics.false_negative],
                        ] as const
                      ).map(([label, count]) => (
                        <Tr key={label}>
                          <Td>{label}</Td>
                          <Td numeric>{count === null ? '--' : formatCount(count)}</Td>
                        </Tr>
                      ))}
                    </tbody>
                  </DataTable>
                </div>
              ) : (
                <EmptyState
                  title="Insufficient labeled data"
                  description={data.metrics.message ?? undefined}
                />
              )
            }
          </QueryBoundary>
        </div>
      </Card>

      <Card>
        <CardTitle>Label coverage</CardTitle>
        <CardDescription>
          The three categories are kept apart on purpose - only analyst-confirmed labels are used
          as ground truth.
        </CardDescription>
        <div className="mt-4">
          <QueryBoundary
            loading={models.loading}
            error={models.error}
            data={models.data}
            onRetry={models.refetch}
          >
            {(data) => (
              <div className="flex flex-col gap-3">
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <Stat
                    label="Confirmed labels"
                    value={formatCount(data.coverage.confirmed_labels)}
                    scope="Analyst ground truth"
                    tone="positive"
                  />
                  <Stat
                    label="Open outcomes"
                    value={formatCount(data.coverage.open_outcome_labels)}
                    scope="Question left open"
                  />
                  <Stat
                    label="Unlabelled"
                    value={formatCount(data.coverage.unlabelled)}
                    scope="Excluded from every metric"
                  />
                  <Stat
                    label="Simulated flags"
                    value={formatCount(data.coverage.simulated_fraud_flags)}
                    scope="Dataset property, not ground truth"
                    tone="warning"
                  />
                </div>
                <p className="text-xs text-content-faint">{data.coverage.simulated_label_note}</p>
              </div>
            )}
          </QueryBoundary>
        </div>
      </Card>

      <Card>
        <CardTitle>Score distributions</CardTitle>
        <CardDescription>
          Baseline against current, over stored transaction timestamps.
        </CardDescription>
        <div className="mt-4">
          <QueryBoundary
            loading={scores.loading}
            error={scores.error}
            data={scores.data}
            onRetry={scores.refetch}
            isEmpty={(data) => data.baseline === null || data.current === null}
            emptyTitle="Not enough history"
            emptyDescription="Two windows of transaction data are needed to compare."
          >
            {(data) => (
              <DataTable className="min-w-[40rem]">
                <thead>
                  <tr>
                    <Th>Measure</Th>
                    <Th numeric>Baseline</Th>
                    <Th numeric>Current</Th>
                  </tr>
                </thead>
                <tbody>
                  <Tr>
                    <Td className="text-content-muted">Window</Td>
                    <Td numeric className="text-xs">
                      {formatDate(data.baseline?.from ?? null)} –{' '}
                      {formatDate(data.baseline?.to ?? null)}
                    </Td>
                    <Td numeric className="text-xs">
                      {formatDate(data.current?.from ?? null)} –{' '}
                      {formatDate(data.current?.to ?? null)}
                    </Td>
                  </Tr>
                  <Tr>
                    <Td className="text-content-muted">Scored transactions</Td>
                    <Td numeric>{formatCount(data.baseline?.scored_transactions ?? 0)}</Td>
                    <Td numeric>{formatCount(data.current?.scored_transactions ?? 0)}</Td>
                  </Tr>
                  <Tr>
                    <Td className="text-content-muted">Mean fraud probability</Td>
                    <Td numeric>
                      {data.baseline?.mean_fraud_probability?.toFixed(4) ?? '--'}
                    </Td>
                    <Td numeric>{data.current?.mean_fraud_probability?.toFixed(4) ?? '--'}</Td>
                  </Tr>
                  <Tr>
                    <Td className="text-content-muted">High risk</Td>
                    <Td numeric>
                      {data.baseline?.high_risk_percent?.toFixed(2) ?? '--'}%
                    </Td>
                    <Td numeric>{data.current?.high_risk_percent?.toFixed(2) ?? '--'}%</Td>
                  </Tr>
                  <Tr>
                    <Td className="text-content-muted">Mean anomaly score</Td>
                    <Td numeric>{data.baseline?.mean_anomaly_score?.toFixed(2) ?? '--'}</Td>
                    <Td numeric>{data.current?.mean_anomaly_score?.toFixed(2) ?? '--'}</Td>
                  </Tr>
                  <Tr>
                    <Td className="text-content-muted">Critical anomalies</Td>
                    <Td numeric>
                      {data.baseline?.critical_anomaly_percent?.toFixed(2) ?? '--'}%
                    </Td>
                    <Td numeric>
                      {data.current?.critical_anomaly_percent?.toFixed(2) ?? '--'}%
                    </Td>
                  </Tr>
                </tbody>
              </DataTable>
            )}
          </QueryBoundary>
        </div>
      </Card>

      <Card>
        <CardTitle>High-risk decision funnel</CardTitle>
        <CardDescription>
          Why a high model score does not automatically become a block.
        </CardDescription>
        <div className="mt-4">
          <QueryBoundary
            loading={funnel.loading}
            error={funnel.error}
            data={funnel.data}
            onRetry={funnel.refetch}
            isEmpty={(data) => data.stages.length === 0}
            emptyTitle="No high-risk transactions"
          >
            {(data) => (
              <div className="flex flex-col gap-4">
                <DecisionFunnel stages={data.stages} />
                <p className="rounded-lg border border-border-subtle bg-surface-sunken/50 px-3 py-2 text-sm text-content-muted">
                  {data.explanation}
                </p>
                <p className="numeric text-xs text-content-faint">
                  Block threshold {data.block_threshold} · minimum{' '}
                  {data.min_independent_sources} independent evidence sources ·{' '}
                  {formatCount(data.withheld_pending_investigation)} withheld pending
                  investigation · policy {data.policy_version}
                </p>
              </div>
            )}
          </QueryBoundary>
        </div>
      </Card>

      <Recommendations />
      <Assistant />
    </div>
  )
}

// --------------------------------------------------------------------------
// Drift
// --------------------------------------------------------------------------
function DriftPage() {
  const query = useQuery((signal) => api.drift(signal), [])

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader
          title="Distribution drift"
          description="Population Stability Index per feature, comparing a baseline window against the current one. PSI measures how far a feature's distribution has moved, not whether the movement is bad."
        />
        <div className="mt-4">
          <QueryBoundary
            loading={query.loading}
            error={query.error}
            data={query.data}
            onRetry={query.refetch}
            isEmpty={(data) => data.features.length === 0}
            emptyTitle="No data to compare"
            emptyDescription="Two windows of transaction history are needed."
            loadingRows={6}
          >
            {(data) => (
              <div className="flex flex-col gap-4">
                <p className="numeric text-xs text-content-faint">
                  Baseline {formatDate(data.baseline_from)} – {formatDate(data.baseline_to)} ·
                  current {formatDate(data.current_from)} – {formatDate(data.current_to)} · WATCH
                  at PSI {data.thresholds.psi_watch}, DRIFT at {data.thresholds.psi_drift}
                </p>

                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {data.features.map((feature) => (
                    <DriftCard key={feature.feature} feature={feature} />
                  ))}
                </div>

                <div className="rounded-lg border border-warning/30 bg-warning-surface px-3.5 py-3">
                  <p className="text-sm font-semibold text-warning">
                    Drift is not necessarily fraud
                  </p>
                  <p className="mt-1 text-sm leading-relaxed text-content-muted">{data.note}</p>
                </div>
              </div>
            )}
          </QueryBoundary>
        </div>
      </Card>
    </div>
  )
}

// --------------------------------------------------------------------------
// Policy effectiveness
// --------------------------------------------------------------------------
function PolicyEffectivenessPage() {
  const query = useQuery((signal) => api.policyEffectiveness(signal), [])

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardTitle>Policy rule performance</CardTitle>
        <CardDescription>
          How often each rule fires, what it decides, and how often an analyst reaches a different
          outcome. Rules are never changed automatically.
        </CardDescription>
        <div className="mt-4">
          <QueryBoundary
            loading={query.loading}
            error={query.error}
            data={query.data}
            onRetry={query.refetch}
            isEmpty={(data) => data.rules.length === 0}
            emptyTitle="No rule activity"
            loadingRows={8}
          >
            {(data) => (
              <div className="flex flex-col gap-3">
                <DataTable>
                  <thead>
                    <tr>
                      <Th>Rule</Th>
                      <Th numeric>Triggers</Th>
                      <Th numeric>Approve</Th>
                      <Th numeric>Step-up</Th>
                      <Th numeric>Review</Th>
                      <Th numeric>Block</Th>
                      <Th numeric>Resolved</Th>
                      <Th numeric>Overrides</Th>
                      <Th numeric>Override rate</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.rules.map((rule) => (
                      <Tr key={rule.rule_id}>
                        <Td>
                          <span className="numeric text-xs font-semibold">{rule.rule_id}</span>
                          {rule.flagged_high_override ? (
                            <Badge tone="danger" className="ml-2">
                              HIGH OVERRIDE
                            </Badge>
                          ) : null}
                        </Td>
                        <Td numeric>{formatCount(rule.triggers)}</Td>
                        <Td numeric>{formatCount(rule.approve_count)}</Td>
                        <Td numeric>{formatCount(rule.step_up_count)}</Td>
                        <Td numeric>{formatCount(rule.review_count)}</Td>
                        <Td numeric>{formatCount(rule.block_count)}</Td>
                        <Td numeric>{formatCount(rule.resolved_count)}</Td>
                        <Td numeric>{formatCount(rule.override_count)}</Td>
                        <Td numeric>
                          {rule.override_rate === null ? (
                            <span
                              className="text-content-faint"
                              title={`Fewer than ${data.min_rule_triggers} resolved cases`}
                            >
                              n/a
                            </span>
                          ) : (
                            formatPercent(rule.override_rate, 0)
                          )}
                        </Td>
                      </Tr>
                    ))}
                  </tbody>
                </DataTable>
                <p className="rounded-lg border border-border-subtle bg-surface-sunken/50 px-3 py-2 text-sm text-content-muted">
                  {data.override_note}
                </p>
                <p className="text-xs text-content-faint">
                  Override rate is withheld below {data.min_rule_triggers} resolved cases - one
                  override out of two is not a 50% rate in any useful sense. A rule is flagged
                  above {(data.high_override_threshold * 100).toFixed(0)}%. Policy{' '}
                  {data.policy_version}.
                </p>
              </div>
            )}
          </QueryBoundary>
        </div>
      </Card>
    </div>
  )
}

export function MonitoringPage() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Assurance"
        title="Monitoring"
        description="Where the risk system is succeeding and where it is failing. Everything here is analytical - nothing retrains, re-thresholds or re-decides."
        actions={<MonitoringTabs />}
      />

      <Routes>
        <Route index element={<Navigate to="/monitoring/models" replace />} />
        <Route path="models" element={<ModelsPage />} />
        <Route path="drift" element={<DriftPage />} />
        <Route path="policy" element={<PolicyEffectivenessPage />} />
      </Routes>
    </div>
  )
}
