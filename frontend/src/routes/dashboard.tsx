import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { BarChart, type BarDatum } from '@/components/charts/bar-chart'
import { Histogram } from '@/components/charts/histogram'
import { Sparkline } from '@/components/charts/sparkline'
import { TrendChart } from '@/components/charts/trend-chart'
import { Badge, DecisionBadge, SeverityBadge } from '@/components/ui/badge'
import { Card, CardHeader, CardNote } from '@/components/ui/card'
import { SegmentedControl } from '@/components/ui/field'
import { Metric, ShareBar } from '@/components/ui/metric'
import { MetaList, PageHeader } from '@/components/ui/page-header'
import { QueryBoundary, Skeleton, TableSkeleton } from '@/components/ui/states'
import { DataTable, Td, Th, Tr } from '@/components/ui/table'
import { useQuery } from '@/hooks/use-query'
import { api } from '@/lib/api'
import {
  formatAmount,
  formatCount,
  formatDate,
  formatLatency,
  formatPercent,
  formatProbability,
  humanizeCode,
} from '@/lib/format'
import { decisionTone, healthTone, severityTone, toneClasses } from '@/lib/risk'
import { cn } from '@/lib/utils'

const TREND_WINDOWS = [
  { value: 7, label: '7d' },
  { value: 30, label: '30d' },
  { value: 90, label: '90d' },
  { value: 365, label: '1y' },
] as const

/** PSI banding, as the backend reports it. Mirrored here only for the label. */
function driftTone(status: string) {
  switch (status.toLowerCase()) {
    case 'stable':
      return 'positive' as const
    case 'moderate':
      return 'warning' as const
    case 'significant':
      return 'danger' as const
    default:
      return 'neutral' as const
  }
}

/**
 * The risk operations dashboard.
 *
 * Every figure on this page is a SQL aggregate served by /api. There are no
 * client-side computations over transaction data and no placeholder numbers -
 * if the backend has nothing to report, the panel says so rather than rendering
 * an empty chart.
 *
 * The page is ordered to answer one question in one screen: *what is happening
 * with payment risk right now?* Posture first, then where it is going, then
 * what is driving it, then the specific transactions worth opening.
 */
export function DashboardPage() {
  const [days, setDays] = useState<number>(30)

  const overview = useQuery((signal) => api.overview(signal), [])
  const distribution = useQuery((signal) => api.riskDistribution(signal), [])
  const decisions = useQuery((signal) => api.decisionAnalytics(signal), [])
  const trends = useQuery((signal) => api.trends(days, signal), [days])
  const topRisk = useQuery((signal) => api.topRisk(8, signal), [])
  const health = useQuery((signal) => api.systemHealth(signal), [])
  const models = useQuery((signal) => api.modelMonitoring(signal), [])
  const drift = useQuery((signal) => api.drift(signal), [])

  const summary = overview.data
  const decided = Math.max(summary?.decided_transactions ?? 0, 1)

  /** Daily volume, for the sparkline beside the headline count. */
  const volumeSeries = useMemo(
    () => trends.data?.points.map((point) => point.volume) ?? [],
    [trends.data],
  )
  const highRiskSeries = useMemo(
    () => trends.data?.points.map((point) => point.high_risk) ?? [],
    [trends.data],
  )

  const metrics = models.data?.metrics
  const driftFeatures = drift.data?.features ?? []
  const driftAlerts = driftFeatures.filter((feature) => feature.status.toLowerCase() !== 'stable')
  const failingComponents =
    health.data?.components.filter((component) => component.status !== 'ok') ?? []

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Operations"
        title="Risk Command Center"
        description="What the platform is doing with payment risk right now. Every figure is a database aggregate - none is hardcoded, and none is estimated in the browser."
        meta={
          summary ? (
            <MetaList
              items={[
                <span key="count">
                  <span className="numeric font-medium text-content-muted">
                    {formatCount(summary.total_transactions)}
                  </span>{' '}
                  transactions
                </span>,
                <span key="range">
                  {formatDate(summary.data_from)} – {formatDate(summary.data_to)}
                </span>,
                <span key="policy">
                  policy{' '}
                  <span className="numeric font-medium text-content-muted">
                    {summary.policy_version}
                  </span>
                </span>,
              ]}
            />
          ) : (
            <Skeleton className="h-4 w-72" />
          )
        }
        actions={
          <Link
            to="/live"
            className="inline-flex items-center gap-2 rounded-lg border border-border-subtle bg-surface-raised px-3 py-2 text-sm font-medium text-content shadow-flat transition-colors hover:border-brand/40 hover:text-brand"
          >
            <span className="relative flex size-1.5">
              <span
                aria-hidden="true"
                className="absolute inline-flex size-full animate-ping-slow rounded-full bg-positive opacity-70"
              />
              <span className="relative inline-flex size-1.5 rounded-full bg-positive" />
            </span>
            Live stream
          </Link>
        }
      />

      {/* --- posture ------------------------------------------------------- */}
      <section aria-label="Headline metrics" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric
          label="Transactions"
          value={summary ? formatCount(summary.total_transactions) : '--'}
          scope="Entire dataset"
          loading={overview.loading}
          emphasis
          visual={
            volumeSeries.length > 1 ? <Sparkline points={volumeSeries} tone="brand" /> : undefined
          }
          detail={
            summary
              ? `${formatCount(summary.decided_transactions)} decided by policy`
              : undefined
          }
        />
        <Metric
          label="Approval rate"
          value={summary ? formatPercent(summary.approved / decided, 1) : '--'}
          scope="Share of decided transactions"
          tone="positive"
          loading={overview.loading}
          emphasis
          visual={<ShareBar share={(summary?.approved ?? 0) / decided} tone="positive" />}
          detail={summary ? `${formatCount(summary.approved)} approved` : undefined}
        />
        <Metric
          label="High risk"
          value={summary ? formatCount(summary.high_risk_transactions) : '--'}
          scope={
            summary
              ? `Fraud probability ≥ ${summary.high_risk_threshold.toFixed(4)}`
              : 'Above the policy threshold'
          }
          tone="warning"
          loading={overview.loading}
          emphasis
          visual={
            highRiskSeries.length > 1 ? (
              <Sparkline points={highRiskSeries} tone="warning" />
            ) : undefined
          }
          detail={
            summary
              ? `${formatPercent(summary.high_risk_transactions / Math.max(summary.total_transactions, 1), 2)} of all traffic`
              : undefined
          }
        />
        <Metric
          label="Open reviews"
          value={summary ? formatCount(summary.open_review_cases) : '--'}
          scope="Awaiting an analyst now"
          tone="attention"
          loading={overview.loading}
          emphasis
          detail={
            summary && summary.escalated_review_cases > 0
              ? `${formatCount(summary.escalated_review_cases)} escalated`
              : undefined
          }
        />
      </section>

      {/* --- disposition --------------------------------------------------- */}
      <section
        aria-label="Decision disposition"
        className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
      >
        <Metric
          label="Step-up"
          value={summary ? formatCount(summary.step_up) : '--'}
          scope="Extra verification requested"
          tone="warning"
          loading={overview.loading}
          visual={<ShareBar share={(summary?.step_up ?? 0) / decided} tone="warning" />}
          detail={summary ? `${formatPercent(summary.step_up / decided, 1)} of decided` : undefined}
        />
        <Metric
          label="Review"
          value={summary ? formatCount(summary.review) : '--'}
          scope="Routed to a human"
          tone="attention"
          loading={overview.loading}
          visual={<ShareBar share={(summary?.review ?? 0) / decided} tone="attention" />}
          detail={summary ? `${formatPercent(summary.review / decided, 2)} of decided` : undefined}
        />
        <Metric
          label="Blocked"
          value={summary ? formatCount(summary.blocked) : '--'}
          scope="Refused outright"
          tone="danger"
          loading={overview.loading}
          visual={<ShareBar share={(summary?.blocked ?? 0) / decided} tone="danger" />}
          detail={summary ? `${formatPercent(summary.blocked / decided, 3)} of decided` : undefined}
        />
        <Metric
          label="Critical anomalies"
          value={summary ? formatCount(summary.critical_anomalies) : '--'}
          scope={
            summary
              ? `Anomaly score ≥ ${summary.critical_anomaly_threshold}`
              : 'Above the policy threshold'
          }
          tone="danger"
          loading={overview.loading}
          detail="Behavioural engine, independent of the fraud model"
        />
      </section>

      {/* --- trend -------------------------------------------------------- */}
      <Card label="Risk trend">
        <CardHeader
          title="Risk trend"
          description="Daily volume and how the policy disposed of it, over real transaction timestamps."
          actions={
            <SegmentedControl
              label="Time window"
              value={days}
              options={TREND_WINDOWS}
              onChange={setDays}
            />
          }
        />
        <div className="mt-4">
          <QueryBoundary
            loading={trends.loading}
            error={trends.error}
            data={trends.data}
            onRetry={trends.refetch}
            isEmpty={(data) => data.points.length === 0}
            emptyTitle="No transactions in this window"
            emptyDescription="Widen the time range to see activity."
            skeleton={<Skeleton className="h-56 w-full" />}
          >
            {(data) => <TrendChart points={data.points} />}
          </QueryBoundary>
        </div>
      </Card>

      {/* --- assurance strip ----------------------------------------------- */}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card label="Model performance">
          <CardHeader
            title="Model performance"
            description="Computed only over analyst-labelled transactions."
            actions={
              <Link to="/monitoring" className="text-xs font-medium text-brand hover:underline">
                Detail →
              </Link>
            }
          />
          <div className="mt-4">
            <QueryBoundary
              loading={models.loading}
              error={models.error}
              data={models.data}
              onRetry={models.refetch}
              loadingRows={3}
            >
              {(data) =>
                data.metrics.sufficient ? (
                  <div className="grid grid-cols-3 gap-3">
                    {(
                      [
                        ['Precision', data.metrics.precision],
                        ['Recall', data.metrics.recall],
                        ['F1', data.metrics.f1],
                      ] as const
                    ).map(([label, value]) => (
                      <div key={label}>
                        <p className="text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
                          {label}
                        </p>
                        <p className="numeric mt-0.5 text-xl font-semibold">
                          {value === null ? '--' : value.toFixed(3)}
                        </p>
                      </div>
                    ))}
                    <p className="col-span-3 text-xs text-content-faint">
                      {formatCount(data.metrics.labelled_samples)} labelled of{' '}
                      {formatCount(data.metrics.total_transactions)} transactions
                    </p>
                  </div>
                ) : (
                  <div className="rounded-lg border border-dashed border-border-subtle bg-surface-sunken/40 px-3 py-4">
                    <p className="text-sm font-medium text-content">Not enough labels yet</p>
                    <p className="mt-1 text-xs text-content-muted">
                      {data.metrics.message ??
                        `${formatCount(data.metrics.labelled_samples)} of ${data.metrics.minimum_required} required.`}
                    </p>
                  </div>
                )
              }
            </QueryBoundary>
          </div>
        </Card>

        <Card label="Feature drift">
          <CardHeader
            title="Feature drift"
            description="Population Stability Index against the baseline window."
            actions={
              <Link
                to="/monitoring/drift"
                className="text-xs font-medium text-brand hover:underline"
              >
                Detail →
              </Link>
            }
          />
          <div className="mt-4">
            <QueryBoundary
              loading={drift.loading}
              error={drift.error}
              data={drift.data}
              onRetry={drift.refetch}
              loadingRows={3}
            >
              {() =>
                driftFeatures.length === 0 ? (
                  <p className="text-sm text-content-muted">No comparable windows yet.</p>
                ) : (
                  <div className="flex flex-col gap-2">
                    <div className="flex items-baseline gap-2">
                      <span className="numeric text-2xl font-semibold">
                        {driftAlerts.length}
                      </span>
                      <span className="text-sm text-content-muted">
                        of {driftFeatures.length} features shifted
                      </span>
                    </div>
                    <ul className="flex flex-col gap-1.5">
                      {(driftAlerts.length > 0 ? driftAlerts : driftFeatures)
                        .slice(0, 3)
                        .map((feature) => (
                          <li
                            key={feature.feature}
                            className="flex items-center justify-between gap-2 text-xs"
                          >
                            <span className="truncate text-content-muted">
                              {humanizeCode(feature.feature)}
                            </span>
                            <span className="flex shrink-0 items-center gap-1.5">
                              <span className="numeric text-content">
                                {feature.psi === null ? '--' : feature.psi.toFixed(3)}
                              </span>
                              <Badge tone={driftTone(feature.status)}>
                                {feature.status.toUpperCase()}
                              </Badge>
                            </span>
                          </li>
                        ))}
                    </ul>
                  </div>
                )
              }
            </QueryBoundary>
          </div>
          <CardNote>Drift is a change in the input distribution, not evidence of fraud.</CardNote>
        </Card>

        <Card label="Subsystems">
          <CardHeader title="Subsystems" description="Reported by the services themselves." />
          <div className="mt-4">
            <QueryBoundary
              loading={health.loading}
              error={health.error}
              data={health.data}
              onRetry={health.refetch}
              loadingRows={4}
            >
              {(data) => (
                <ul className="flex flex-col gap-2">
                  {data.components.map((component) => (
                    <li
                      key={component.name}
                      className="flex items-center justify-between gap-2 text-sm"
                    >
                      <span className="min-w-0 truncate text-content-muted">
                        {humanizeCode(component.name)}
                        {component.version ? (
                          <span className="numeric ml-1.5 text-[0.7rem] text-content-faint">
                            {component.version}
                          </span>
                        ) : null}
                      </span>
                      {/* toneClasses returns a literal class name; an
                          interpolated `text-${tone}` would be invisible to
                          Tailwind's static extraction and never emitted. */}
                      <span
                        className={cn(
                          'shrink-0 text-xs font-semibold',
                          toneClasses(healthTone(component.status)).text,
                        )}
                      >
                        {component.status.toUpperCase()}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </QueryBoundary>
          </div>
          {failingComponents.length > 0 ? (
            <CardNote>
              A degraded subsystem does not silently approve anything: the pipeline stops and the
              transaction stays undecided.
            </CardNote>
          ) : null}
        </Card>
      </div>

      {/* --- distributions ------------------------------------------------ */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card label="Decision distribution">
          <CardHeader
            title="Decision distribution"
            description="The current disposition of every decided transaction."
          />
          <div className="mt-4">
            <QueryBoundary
              loading={distribution.loading}
              error={distribution.error}
              data={distribution.data}
              onRetry={distribution.refetch}
              isEmpty={(data) => data.decisions.every((bucket) => bucket.count === 0)}
              emptyTitle="No decisions recorded"
              emptyDescription="Run the decision engine to populate this chart."
            >
              {(data) => (
                <BarChart
                  data={data.decisions.map<BarDatum>((bucket) => ({
                    label: bucket.label,
                    count: bucket.count,
                    tone: decisionTone(bucket.label),
                  }))}
                />
              )}
            </QueryBoundary>
          </div>
        </Card>

        <Card label="Anomaly severity">
          <CardHeader
            title="Anomaly severity"
            description="Phase 4 behavioural bands. Unusual is not the same as fraudulent."
          />
          <div className="mt-4">
            <QueryBoundary
              loading={distribution.loading}
              error={distribution.error}
              data={distribution.data}
              onRetry={distribution.refetch}
              isEmpty={(data) => data.anomaly_severity.every((bucket) => bucket.count === 0)}
              emptyTitle="No anomaly scores stored"
            >
              {(data) => (
                <BarChart
                  data={data.anomaly_severity.map<BarDatum>((bucket) => ({
                    label: bucket.label,
                    count: bucket.count,
                    tone: severityTone(bucket.label),
                  }))}
                />
              )}
            </QueryBoundary>
          </div>
        </Card>

        <Card label="Risk level">
          <CardHeader
            title="Risk level"
            description="Banded by the active policy's own supervised thresholds."
          />
          <div className="mt-4">
            <QueryBoundary
              loading={distribution.loading}
              error={distribution.error}
              data={distribution.data}
              onRetry={distribution.refetch}
              isEmpty={(data) => data.risk_level.every((bucket) => bucket.count === 0)}
              emptyTitle="No predictions stored"
            >
              {(data) => (
                <BarChart
                  data={data.risk_level.map<BarDatum>((bucket) => ({
                    label: bucket.label,
                    count: bucket.count,
                    tone: severityTone(bucket.label),
                    hint:
                      bucket.lower !== null && bucket.upper !== null
                        ? `probability ${bucket.lower.toFixed(3)} – ${bucket.upper.toFixed(3)}`
                        : undefined,
                  }))}
                />
              )}
            </QueryBoundary>
          </div>
        </Card>

        <Card label="Fraud probability distribution">
          <CardHeader
            title="Fraud probability distribution"
            description="Every stored XGBoost prediction, in ten buckets."
          />
          <div className="mt-4">
            <QueryBoundary
              loading={distribution.loading}
              error={distribution.error}
              data={distribution.data}
              onRetry={distribution.refetch}
              isEmpty={(data) => data.fraud_probability.every((bucket) => bucket.count === 0)}
              emptyTitle="No predictions stored"
            >
              {(data) => <Histogram buckets={data.fraud_probability} />}
            </QueryBoundary>
          </div>
        </Card>
      </div>

      {/* --- drivers and throughput --------------------------------------- */}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card label="What is driving decisions" className="lg:col-span-2">
          <CardHeader
            title="What is driving decisions"
            description="Reason codes across current decisions. Each is emitted by a specific rule, never generated from prose."
          />
          <div className="mt-4">
            <QueryBoundary
              loading={decisions.loading}
              error={decisions.error}
              data={decisions.data}
              onRetry={decisions.refetch}
              isEmpty={(data) => data.reason_codes.length === 0}
              emptyTitle="No decisions recorded"
            >
              {(data) => (
                <BarChart
                  showPercentage={false}
                  data={data.reason_codes.slice(0, 10).map<BarDatum>((bucket) => ({
                    label: humanizeCode(bucket.label),
                    count: bucket.count,
                    tone: 'brand',
                  }))}
                />
              )}
            </QueryBoundary>
          </div>
        </Card>

        <div className="flex flex-col gap-3">
          <Metric
            label="Investigations"
            value={summary ? formatCount(summary.completed_investigations) : '--'}
            scope="Completed agent reports"
            tone="brand"
            loading={overview.loading}
            detail={
              summary && summary.completed_investigations > 0
                ? `${formatPercent(summary.completed_investigations / Math.max(summary.high_risk_transactions, 1), 1)} of high-risk traffic`
                : 'Run when a model raises a concern'
            }
          />
          <Metric
            label="Decision latency"
            value={summary ? formatLatency(summary.avg_decision_latency_ms) : '--'}
            scope={
              summary
                ? `Mean over ${formatCount(summary.latency_sample_size)} policy evaluations`
                : 'Policy evaluation time'
            }
            loading={overview.loading}
            detail={
              summary && summary.max_decision_latency_ms !== null
                ? `max ${formatLatency(summary.max_decision_latency_ms)}`
                : undefined
            }
          />
          <Metric
            label="Labelled outcomes"
            value={metrics ? formatCount(metrics.labelled_samples) : '--'}
            scope="Analyst-confirmed ground truth"
            tone="attention"
            loading={models.loading}
            detail={
              metrics
                ? `${formatCount(metrics.unlabelled_transactions)} unlabelled - not counted as legitimate`
                : undefined
            }
          />
        </div>
      </div>

      {/* --- top risk ------------------------------------------------------ */}
      <Card label="Highest risk transactions">
        <CardHeader
          title="Highest risk transactions"
          description="Ranked by stored fraud probability. Open one to see the full evidence chain."
          actions={
            <Link to="/transactions" className="text-sm font-medium text-brand hover:underline">
              Open explorer →
            </Link>
          }
        />
        <div className="mt-4">
          <QueryBoundary
            loading={topRisk.loading}
            error={topRisk.error}
            data={topRisk.data}
            onRetry={topRisk.refetch}
            isEmpty={(data) => data.items.length === 0}
            emptyTitle="No scored transactions"
            skeleton={<TableSkeleton rows={6} columns={7} />}
          >
            {(data) => (
              <DataTable>
                <thead>
                  <tr>
                    <Th>Transaction</Th>
                    <Th>Merchant</Th>
                    <Th numeric>Amount</Th>
                    <Th numeric>Fraud probability</Th>
                    <Th numeric>Anomaly</Th>
                    <Th>Severity</Th>
                    <Th>Decision</Th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((item) => (
                    <Tr key={item.transaction_id}>
                      <Td>
                        <Link
                          to={`/transactions/${encodeURIComponent(item.transaction_id)}`}
                          className="numeric text-xs font-medium text-brand hover:underline"
                        >
                          {item.transaction_id}
                        </Link>
                      </Td>
                      <Td className="text-content-muted">{item.merchant_name}</Td>
                      <Td numeric>{formatAmount(item.amount, item.currency)}</Td>
                      <Td numeric className="font-semibold">
                        {formatProbability(item.fraud_probability)}
                      </Td>
                      <Td numeric>{item.anomaly_score ?? '--'}</Td>
                      <Td>
                        <SeverityBadge severity={item.anomaly_severity} />
                      </Td>
                      <Td>
                        <DecisionBadge decision={item.decision} />
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </DataTable>
            )}
          </QueryBoundary>
        </div>
      </Card>
    </div>
  )
}
