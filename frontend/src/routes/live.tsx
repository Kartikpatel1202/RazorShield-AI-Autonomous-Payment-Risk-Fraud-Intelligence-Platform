import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { useAuth } from '@/components/auth/context'
import { StageFlow, type FlowStage } from '@/components/ui/timeline'
import { Badge, DecisionBadge, SeverityBadge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardHeader, CardNote } from '@/components/ui/card'
import { SelectField, TextField } from '@/components/ui/field'
import { Metric } from '@/components/ui/metric'
import { PageHeader } from '@/components/ui/page-header'
import { EmptyState, ErrorState, QueryBoundary } from '@/components/ui/states'
import { DataTable, Td, Th, Tr } from '@/components/ui/table'
import { useEventStream, type StreamStatus } from '@/hooks/use-event-stream'
import { useQuery } from '@/hooks/use-query'
import { api, type RiskEvent, type SimulatorStatus } from '@/lib/api'
import { Permission } from '@/lib/auth'
import { formatAmount, formatCount, formatProbability, humanizeCode } from '@/lib/format'
import { decisionTone, severityTone, toneClasses } from '@/lib/risk'
import { cn } from '@/lib/utils'

const SCENARIOS = [
  { value: 'normal', label: 'Normal' },
  { value: 'suspicious', label: 'Suspicious' },
  { value: 'high_fraud', label: 'High fraud' },
  { value: 'coordinated_fraud', label: 'Coordinated fraud' },
  { value: 'model_disagreement', label: 'Model disagreement' },
] as const

const STATUS_LABEL: Record<StreamStatus, string> = {
  connecting: 'CONNECTING',
  live: 'LIVE',
  disconnected: 'DISCONNECTED',
}

function statusTone(status: StreamStatus) {
  if (status === 'live') return 'positive' as const
  if (status === 'connecting') return 'warning' as const
  return 'danger' as const
}

/** The connection indicator. A live dot, not a coloured word. */
function StreamStatusPill({ status, retries }: { status: StreamStatus; retries: number }) {
  const tone = statusTone(status)
  const dot =
    tone === 'positive' ? 'bg-positive' : tone === 'warning' ? 'bg-warning' : 'bg-danger'
  return (
    <span
      title={
        status === 'disconnected'
          ? `Reconnecting (attempt ${retries + 1})`
          : 'Server-sent event stream'
      }
      className={cn(
        'inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs font-semibold',
        toneClasses(tone).surface,
        toneClasses(tone).text,
        toneClasses(tone).border,
      )}
    >
      <span className="relative flex size-1.5">
        {status === 'live' ? (
          <span
            aria-hidden="true"
            className={cn('absolute inline-flex size-full animate-ping-slow rounded-full', dot)}
          />
        ) : null}
        <span aria-hidden="true" className={cn('relative inline-flex size-1.5 rounded-full', dot)} />
      </span>
      {STATUS_LABEL[status]}
    </span>
  )
}

/** A transaction's progress through the pipeline, assembled from its events. */
interface TransactionRollup {
  reference: string
  simulated: boolean
  amount: number | null
  currency: string
  fraudProbability: number | null
  anomalyScore: number | null
  anomalySeverity: string | null
  investigationStatus: 'none' | 'running' | 'complete'
  investigationRiskLevel: string | null
  findings: number | null
  evidence: number | null
  decision: string | null
  reasonCodes: string[]
  matchedRules: string[]
  requiresReview: boolean
  lastSequence: number
  failed: string | null
  /** Which pipeline stages have been observed, in arrival order. */
  stages: Set<string>
}

function read<T>(
  payload: Record<string, unknown>,
  key: string,
  guard: (v: unknown) => v is T,
): T | null {
  const value = payload[key]
  return guard(value) ? value : null
}

const isNumber = (v: unknown): v is number => typeof v === 'number'
const isString = (v: unknown): v is string => typeof v === 'string'
const isBool = (v: unknown): v is boolean => typeof v === 'boolean'

/**
 * Fold the event stream into per-transaction state.
 *
 * The stream is a log of stages, but the table wants one row per transaction
 * that fills in as its events arrive. Folding here rather than storing rows
 * keeps the events the single source of truth - a replayed backlog produces
 * exactly the same rollup as live delivery did.
 */
function rollup(events: RiskEvent[]): TransactionRollup[] {
  const byReference = new Map<string, TransactionRollup>()

  // Oldest first, so later stages overwrite earlier ones.
  for (const event of [...events].reverse()) {
    const existing = byReference.get(event.transaction_id) ?? {
      reference: event.transaction_id,
      simulated: false,
      amount: null,
      currency: 'INR',
      fraudProbability: null,
      anomalyScore: null,
      anomalySeverity: null,
      investigationStatus: 'none' as const,
      investigationRiskLevel: null,
      findings: null,
      evidence: null,
      decision: null,
      reasonCodes: [],
      matchedRules: [],
      requiresReview: false,
      lastSequence: 0,
      failed: null,
      stages: new Set<string>(),
    }
    const p = event.payload
    existing.lastSequence = Math.max(existing.lastSequence, event.sequence)
    existing.simulated = read(p, 'simulated', isBool) ?? existing.simulated
    existing.stages.add(event.event_type)

    switch (event.event_type) {
      case 'transaction_received':
        existing.amount = read(p, 'amount', isNumber)
        existing.currency = read(p, 'currency', isString) ?? 'INR'
        break
      case 'risk_scored':
        existing.fraudProbability = read(p, 'fraud_probability', isNumber)
        break
      case 'anomaly_detected':
        existing.anomalyScore = read(p, 'anomaly_score', isNumber)
        existing.anomalySeverity = read(p, 'severity', isString)
        break
      case 'investigation_started':
        existing.investigationStatus = 'running'
        break
      case 'investigation_completed':
        existing.investigationStatus = 'complete'
        existing.investigationRiskLevel = read(p, 'risk_level', isString)
        existing.findings = read(p, 'findings', isNumber)
        existing.evidence = read(p, 'evidence', isNumber)
        break
      case 'decision_created':
        existing.decision = read(p, 'decision', isString)
        existing.reasonCodes = Array.isArray(p.reason_codes) ? (p.reason_codes as string[]) : []
        existing.matchedRules = Array.isArray(p.matched_rules) ? (p.matched_rules as string[]) : []
        existing.requiresReview = read(p, 'requires_human_review', isBool) ?? false
        break
      case 'processing_failed':
        existing.failed = read(p, 'error', isString) ?? 'processing failed'
        break
    }
    byReference.set(event.transaction_id, existing)
  }

  return [...byReference.values()].sort((a, b) => b.lastSequence - a.lastSequence)
}

/**
 * The five pipeline stages, as a strip of dots.
 *
 * Small enough to sit inside a table cell, so a reader can see *how far* a
 * transaction has got without opening it - which is the question the live feed
 * exists to answer while rows are still filling in.
 */
function StageDots({ item }: { item: TransactionRollup }) {
  const stages: { key: string; label: string; done: boolean; active?: boolean }[] = [
    { key: 'received', label: 'Received', done: item.stages.has('transaction_received') },
    { key: 'scored', label: 'Risk scored', done: item.stages.has('risk_scored') },
    { key: 'anomaly', label: 'Anomaly detected', done: item.stages.has('anomaly_detected') },
    {
      key: 'investigation',
      label:
        item.investigationStatus === 'none'
          ? 'Investigation not warranted'
          : item.investigationStatus === 'running'
            ? 'Investigation running'
            : 'Investigation complete',
      done: item.investigationStatus === 'complete',
      active: item.investigationStatus === 'running',
    },
    { key: 'decision', label: 'Decision', done: item.stages.has('decision_created') },
  ]

  return (
    <span className="inline-flex items-center gap-1" aria-hidden="true">
      {stages.map((stage) => (
        <span
          key={stage.key}
          title={stage.label}
          className={cn(
            'size-1.5 rounded-full transition-colors',
            item.failed
              ? 'bg-danger/40'
              : stage.done
                ? 'bg-brand'
                : stage.active
                  ? 'animate-pulse bg-brand/60'
                  : 'bg-border-subtle',
          )}
        />
      ))}
    </span>
  )
}

/** The investigation detail for whichever transaction is selected. */
function InvestigationPanel({ item }: { item: TransactionRollup }) {
  const stages: FlowStage[] = [
    {
      key: 'transaction',
      label: 'Transaction',
      value: item.amount === null ? 'pending' : formatAmount(item.amount, item.currency),
      state: item.stages.has('transaction_received') ? 'done' : 'pending',
    },
    {
      key: 'fraud',
      label: 'Fraud model',
      value: formatProbability(item.fraudProbability),
      state: item.fraudProbability === null ? 'pending' : 'done',
      tone: (item.fraudProbability ?? 0) >= 0.5 ? 'danger' : 'neutral',
    },
    {
      key: 'anomaly',
      label: 'Anomaly',
      value: item.anomalyScore === null ? 'pending' : `${item.anomalyScore} / 100`,
      state: item.anomalyScore === null ? 'pending' : 'done',
      tone: severityTone(item.anomalySeverity),
    },
    {
      key: 'investigation',
      label: 'Investigation',
      value:
        item.investigationStatus === 'none'
          ? 'not warranted'
          : item.investigationStatus === 'running'
            ? 'running...'
            : (item.investigationRiskLevel ?? 'complete'),
      state:
        item.investigationStatus === 'none'
          ? 'skipped'
          : item.investigationStatus === 'running'
            ? 'active'
            : 'done',
      tone: severityTone(item.investigationRiskLevel),
      hint:
        item.investigationStatus === 'none'
          ? 'Neither model raised a concern the policy wants investigated.'
          : undefined,
    },
    {
      key: 'decision',
      label: 'Decision',
      value: item.decision ?? 'pending',
      state: item.decision ? 'done' : 'pending',
      tone: decisionTone(item.decision),
    },
  ]

  return (
    <Card className="animate-fade-in border-brand/25 shadow-raised">
      <CardHeader
        title="Live investigation"
        description={
          <span className="inline-flex flex-wrap items-center gap-2">
            <Link
              to={`/transactions/${encodeURIComponent(item.reference)}`}
              className="numeric text-xs font-medium text-brand hover:underline"
            >
              {item.reference}
            </Link>
            {item.simulated ? <Badge tone="warning">SIMULATED</Badge> : null}
          </span>
        }
        actions={<DecisionBadge decision={item.decision} />}
      />

      <div className="mt-4">
        <StageFlow stages={stages} />
      </div>

      {item.investigationStatus === 'complete' ? (
        <p className="mt-4 text-sm text-content-muted">
          <span className="numeric font-semibold text-content">{item.findings ?? 0}</span> finding
          {item.findings === 1 ? '' : 's'} from{' '}
          <span className="numeric font-semibold text-content">{item.evidence ?? 0}</span> evidence
          items.{' '}
          <Link
            to={`/transactions/${encodeURIComponent(item.reference)}`}
            className="font-medium text-brand hover:underline"
          >
            Open the full evidence chain →
          </Link>
        </p>
      ) : null}

      {item.matchedRules.length > 0 || item.reasonCodes.length > 0 ? (
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          {item.matchedRules.length > 0 ? (
            <div>
              <p className="text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
                Policy rules matched
              </p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {item.matchedRules.map((rule) => (
                  <Badge key={rule} tone="brand">
                    {rule}
                  </Badge>
                ))}
              </div>
            </div>
          ) : null}
          {item.reasonCodes.length > 0 ? (
            <div>
              <p className="text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
                Reason codes
              </p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {item.reasonCodes.map((code) => (
                  <Badge key={code} tone="neutral" title={code}>
                    {humanizeCode(code)}
                  </Badge>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </Card>
  )
}

/**
 * The simulator control panel.
 *
 * Rendered only for a caller holding `simulator:control`. The backend refuses
 * these endpoints to anyone else regardless - this gate exists so a viewer is
 * not shown a Start button that will 403, which is the one thing a console must
 * never do.
 */
function SimulatorControls({
  running,
  paused,
  busy,
  error,
  onStart,
  onAction,
}: {
  running: boolean
  paused: boolean
  busy: boolean
  error: Error | undefined
  onStart: (scenario: string, rate: number, count: number) => void
  onAction: (action: () => Promise<SimulatorStatus>) => void
}) {
  const [scenario, setScenario] = useState<string>('normal')
  const [rate, setRate] = useState(2)
  const [count, setCount] = useState(20)
  const locked = running || paused

  const scenarios = useQuery((signal) => api.simulatorScenarios(signal), [])

  return (
    <Card>
      <CardHeader
        title="Traffic generator"
        description="Choose a behaviour to generate. The existing pipeline decides what it means."
        actions={
          running ? (
            <Badge tone="positive" glyph="●">
              RUNNING
            </Badge>
          ) : paused ? (
            <Badge tone="warning" glyph="❚❚">
              PAUSED
            </Badge>
          ) : (
            <Badge tone="neutral">IDLE</Badge>
          )
        }
      />

      <div className="mt-4 grid items-end gap-3 sm:grid-cols-2 lg:grid-cols-[minmax(11rem,1fr)_7rem_7rem_auto]">
        <SelectField
          label="Scenario"
          value={scenario}
          placeholder={null}
          disabled={locked}
          options={SCENARIOS.map((option) => ({ value: option.value, label: option.label }))}
          onChange={(event) => setScenario(event.target.value)}
        />
        <TextField
          label="Rate /s"
          type="number"
          min={0.1}
          max={50}
          step={0.5}
          value={rate}
          disabled={locked}
          onChange={(event) => setRate(Number(event.target.value))}
        />
        <TextField
          label="Count"
          type="number"
          min={1}
          max={5000}
          value={count}
          disabled={locked}
          onChange={(event) => setCount(Number(event.target.value))}
        />

        <div className="flex flex-wrap gap-2">
          {!locked ? (
            <Button disabled={busy} onClick={() => onStart(scenario, rate, count)}>
              {busy ? 'Starting...' : 'Start'}
            </Button>
          ) : null}
          {running ? (
            <Button variant="secondary" disabled={busy} onClick={() => onAction(api.simulatorPause)}>
              Pause
            </Button>
          ) : null}
          {paused ? (
            <Button disabled={busy} onClick={() => onAction(api.simulatorResume)}>
              Resume
            </Button>
          ) : null}
          {locked ? (
            <Button variant="secondary" disabled={busy} onClick={() => onAction(api.simulatorStop)}>
              Stop
            </Button>
          ) : null}
          <Button variant="ghost" disabled={busy} onClick={() => onAction(api.simulatorReset)}>
            Reset
          </Button>
        </div>
      </div>

      {error ? (
        <div className="mt-3">
          <ErrorState error={error} title="Simulator control failed" />
        </div>
      ) : null}

      {scenarios.data ? (
        <details className="mt-4 border-t border-border-subtle pt-3">
          <summary className="cursor-pointer text-xs font-medium text-content-muted hover:text-content">
            What each scenario generates
          </summary>
          <ul className="mt-2 flex flex-col gap-2">
            {scenarios.data.scenarios.map((entry) => (
              <li key={entry.scenario} className="text-xs leading-relaxed">
                <span className="font-semibold text-content">{entry.title}</span>{' '}
                <span className="text-content-muted">{entry.behaviour}</span>{' '}
                <span className="text-content-faint">{entry.expected_signal}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <CardNote>
        Every transaction the simulator creates is prefixed <span className="numeric">SIM_</span>{' '}
        and shown as <strong className="text-content-muted">SIMULATED</strong>. None of it is
        production traffic, and the simulator never sets a fraud probability, an anomaly score or a
        decision - it only generates behaviour.
      </CardNote>
    </Card>
  )
}

export function LivePage() {
  const { can } = useAuth()
  const canControl = can(Permission.SimulatorControl)

  const stream = useEventStream(300)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | undefined>(undefined)
  const [selected, setSelected] = useState<string | null>(null)
  // Bumped by every control action, so status and metrics refetch immediately
  // after start/stop/pause rather than waiting for the next event to arrive.
  const [controlNonce, setControlNonce] = useState(0)

  // Both are keyed on the stream cursor rather than polled on a timer. Engine
  // counters only change when a transaction completes, and a completion is
  // exactly what advances the cursor - so this refetches when there is news and
  // stays quiet when there is not.
  //
  // `/simulator/status` needs `simulator:control`, so a viewer must not ask for
  // it: the request would 403 and the page would show a permanent error for a
  // panel that role never sees. The live metrics endpoint is readable by
  // anyone who can read events, and carries what a viewer needs.
  const statusQuery = useQuery(
    (signal) => (canControl ? api.simulatorStatus(signal) : Promise.resolve(null)),
    [stream.latestSequence, controlNonce, canControl],
  )
  const metrics = useQuery(
    (signal) => api.liveMetrics(signal),
    [stream.latestSequence, controlNonce],
  )
  const simulator = statusQuery.data ?? null
  const live = metrics.data ?? null

  async function control(action: () => Promise<SimulatorStatus>) {
    setBusy(true)
    setError(undefined)
    try {
      await action()
      setControlNonce((value) => value + 1)
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error(String(caught)))
    } finally {
      setBusy(false)
    }
  }

  const rows = useMemo(() => rollup(stream.events), [stream.events])
  const highRisk = useMemo(() => rows.filter((row) => row.requiresReview), [rows])
  const selectedRow = rows.find((row) => row.reference === selected) ?? highRisk[0] ?? null

  // `/simulator/status` is authoritative and is what the controls act on, so it
  // wins whenever we can read it. The live-metrics view is the fallback for a
  // role that cannot - it still wants to see that something is running, but it
  // has no buttons whose enablement could be got wrong.
  const engineState = simulator?.state ?? (canControl ? null : (live?.simulator_state ?? null))
  const running = engineState === 'running'
  const paused = engineState === 'paused'
  const activeScenario = simulator?.scenario ?? live?.scenario ?? null

  // The engine's own observed rate when we can read it; otherwise the rate the
  // live-metrics endpoint reports, which every role can see.
  const observedTps = simulator?.observed_tps ?? live?.transactions_per_second ?? 0
  const processed = simulator?.processed ?? live?.transactions_processed ?? 0
  const queueDepth = simulator?.queue_depth ?? live?.queue_depth ?? 0
  const queueCapacity = simulator?.queue_capacity ?? live?.queue_capacity ?? 0

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Real time"
        title="Live risk stream"
        description="Generated payments running through the real pipeline. The simulator chooses the behaviour; Phases 3 to 6 decide what it means."
        actions={
          <div className="flex items-center gap-2">
            <StreamStatusPill status={stream.status} retries={stream.retries} />
            {activeScenario ? (
              <Badge tone="brand" title="Scenario currently generating">
                {activeScenario.replace(/_/g, ' ').toUpperCase()}
              </Badge>
            ) : null}
          </div>
        }
      />

      {/* Simulator controls are admin-only. A viewer observes the stream. */}
      {canControl ? (
        <SimulatorControls
          running={running}
          paused={paused}
          busy={busy}
          error={error}
          onStart={(scenario, rate, count) =>
            void control(() =>
              api.simulatorStart({
                scenario,
                transactions_per_second: rate,
                max_transactions: count,
                seed: 42,
              }),
            )
          }
          onAction={(action) => void control(action)}
        />
      ) : (
        <Card className="border-dashed">
          <p className="text-sm text-content-muted">
            You are observing the live stream. Starting and stopping traffic generation requires
            the <code className="numeric text-xs text-content">simulator:control</code> permission,
            which your role does not hold.
          </p>
        </Card>
      )}

      {/* --- live metrics -------------------------------------------------- */}
      <section aria-label="Live metrics" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric
          label="Throughput"
          value={observedTps.toFixed(2)}
          scope={
            simulator?.transactions_per_second
              ? `Observed; ${simulator.transactions_per_second}/s requested`
              : 'Observed over the last 10s'
          }
          tone={observedTps > 0 ? 'brand' : 'neutral'}
          emphasis
        />
        <Metric
          label="Processed"
          value={formatCount(processed)}
          scope={
            simulator?.max_transactions
              ? `of ${formatCount(simulator.max_transactions)} this run`
              : 'This run'
          }
          emphasis
        />
        <Metric
          label="High risk"
          value={formatCount(live?.high_risk_count ?? 0)}
          scope="Simulated, routed to a human"
          tone="attention"
          emphasis
        />
        <Metric
          label="Queue depth"
          value={`${queueDepth} / ${queueCapacity}`}
          scope="Sustained depth means saturation"
          tone={queueCapacity > 0 && queueDepth >= queueCapacity * 0.75 ? 'warning' : 'neutral'}
          emphasis
        />
      </section>

      <section
        aria-label="Live decision counts"
        className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
      >
        <Metric
          label="Approved"
          value={formatCount(live?.approve_count ?? 0)}
          scope="Simulated total"
          tone="positive"
        />
        <Metric
          label="Step-up"
          value={formatCount(live?.step_up_count ?? 0)}
          scope="Simulated total"
          tone="warning"
        />
        <Metric
          label="Review"
          value={formatCount(live?.review_count ?? 0)}
          scope="Simulated total"
          tone="attention"
        />
        <Metric
          label="Blocked"
          value={formatCount(live?.block_count ?? 0)}
          scope="Simulated total"
          tone="danger"
        />
      </section>

      {/* --- live investigation -------------------------------------------- */}
      {selectedRow ? <InvestigationPanel item={selectedRow} /> : null}

      {/* --- feed ---------------------------------------------------------- */}
      <Card>
        <CardHeader
          title="Live transaction feed"
          description="Newest first, updating as events arrive. The dots show how far each transaction has got."
          actions={
            stream.events.length > 0 ? (
              <Button variant="ghost" size="sm" onClick={stream.clear}>
                Clear feed
              </Button>
            ) : null
          }
        />

        <div className="mt-4">
          {stream.status === 'disconnected' && rows.length === 0 ? (
            <ErrorState
              error={new Error('The event stream is not connected. Retrying automatically.')}
              title="Disconnected"
            />
          ) : rows.length === 0 ? (
            <EmptyState
              glyph="◉"
              title="No live transactions yet"
              description={
                canControl
                  ? 'Start a scenario above to generate traffic through the pipeline.'
                  : 'Nothing is being generated right now. Rows appear here as soon as traffic starts.'
              }
            />
          ) : (
            <DataTable>
              <thead>
                <tr>
                  <Th>Transaction</Th>
                  <Th>Progress</Th>
                  <Th numeric>Amount</Th>
                  <Th numeric>Fraud prob.</Th>
                  <Th numeric>Anomaly</Th>
                  <Th>Severity</Th>
                  <Th>Investigation</Th>
                  <Th>Decision</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <Tr
                    key={row.reference}
                    onClick={() => setSelected(row.reference)}
                    aria-selected={selectedRow?.reference === row.reference}
                    className={cn(
                      'cursor-pointer',
                      // Only the newest row animates. Animating the whole list
                      // on every event would make it unreadable.
                      index === 0 && 'animate-row-in',
                      selectedRow?.reference === row.reference && 'bg-brand/5',
                    )}
                  >
                    <Td>
                      <span className="numeric text-xs">{row.reference}</span>
                      {row.simulated ? (
                        <Badge tone="warning" className="ml-1.5">
                          SIM
                        </Badge>
                      ) : null}
                    </Td>
                    <Td>
                      <StageDots item={row} />
                    </Td>
                    <Td numeric>
                      {row.amount === null ? '--' : formatAmount(row.amount, row.currency)}
                    </Td>
                    <Td numeric>{formatProbability(row.fraudProbability)}</Td>
                    <Td numeric>{row.anomalyScore ?? '--'}</Td>
                    <Td>
                      <SeverityBadge severity={row.anomalySeverity} />
                    </Td>
                    <Td>
                      {row.failed ? (
                        <Badge tone="danger" title={row.failed}>
                          FAILED
                        </Badge>
                      ) : row.investigationStatus === 'running' ? (
                        <Badge tone="brand">RUNNING</Badge>
                      ) : row.investigationStatus === 'complete' ? (
                        <span
                          className={cn(
                            'text-xs font-semibold',
                            toneClasses(severityTone(row.investigationRiskLevel)).text,
                          )}
                        >
                          {row.investigationRiskLevel ?? 'DONE'}
                        </span>
                      ) : (
                        <span className="text-content-faint">--</span>
                      )}
                    </Td>
                    <Td>
                      {row.decision ? (
                        <DecisionBadge decision={row.decision} />
                      ) : (
                        <span className="text-xs text-content-faint">pending</span>
                      )}
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </DataTable>
          )}
        </div>
      </Card>

      {/* --- stream health -------------------------------------------------- */}
      <Card>
        <CardHeader
          title="Stream"
          description="Delivery statistics for this connection."
        />
        <div className="mt-4">
          <QueryBoundary
            loading={metrics.loading}
            error={metrics.error}
            data={metrics.data}
            onRetry={metrics.refetch}
            loadingRows={2}
          >
            {(data) => (
              <dl className="grid gap-4 sm:grid-cols-4">
                {(
                  [
                    ['Events recorded', formatCount(data.total_events), null],
                    ['Latest sequence', formatCount(data.latest_sequence), null],
                    ['Connected clients', String(data.connected_clients), null],
                    [
                      'Dropped deliveries',
                      String(data.dropped_deliveries),
                      'durable copy retained',
                    ],
                  ] as const
                ).map(([label, value, note]) => (
                  <div key={label}>
                    <dt className="text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
                      {label}
                    </dt>
                    <dd className="numeric mt-0.5 text-lg font-semibold">{value}</dd>
                    {note ? <p className="text-xs text-content-faint">{note}</p> : null}
                  </div>
                ))}
              </dl>
            )}
          </QueryBoundary>
        </div>
      </Card>
    </div>
  )
}
