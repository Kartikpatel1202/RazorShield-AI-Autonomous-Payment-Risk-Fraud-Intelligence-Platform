/**
 * The live page: connection state, the event feed, metrics and controls.
 *
 * The stream is read with `fetch` rather than `EventSource`, because Phase 10
 * put the endpoint behind a bearer token and `EventSource` cannot set headers.
 * The fake below therefore stubs `fetch` and hands back a reader the test
 * drives frame by frame.
 *
 * It stays faithful about the thing that used to break: the server writes real
 * SSE frames with `id:`, `event:` and `data:` lines, and `emit` produces
 * exactly that text. A fake that handed over pre-parsed objects would pass
 * while the real page showed an empty feed - which is the bug this suite exists
 * to catch.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, type RiskEvent } from '@/lib/api'
import { renderWithRouter } from '@/test/render'

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    api: {
      login: vi.fn().mockResolvedValue(undefined),
      session: vi.fn().mockResolvedValue(undefined),
      logout: vi.fn().mockResolvedValue(undefined),
      overview: vi.fn().mockResolvedValue(undefined),
      riskDistribution: vi.fn().mockResolvedValue(undefined),
      decisionAnalytics: vi.fn().mockResolvedValue(undefined),
      trends: vi.fn().mockResolvedValue(undefined),
      topRisk: vi.fn().mockResolvedValue(undefined),
      explorer: vi.fn().mockResolvedValue(undefined),
      transactionDetail: vi.fn().mockResolvedValue(undefined),
      reviews: vi.fn().mockResolvedValue(undefined),
      resolveReview: vi.fn().mockResolvedValue(undefined),
      audit: vi.fn().mockResolvedValue(undefined),
      auditSummary: vi.fn().mockResolvedValue(undefined),
      policy: vi.fn().mockResolvedValue(undefined),
      systemHealth: vi.fn().mockResolvedValue(undefined),
      feedback: vi.fn().mockResolvedValue(undefined),
      feedbackSummary: vi.fn().mockResolvedValue(undefined),
      createFeedback: vi.fn().mockResolvedValue(undefined),
      modelMonitoring: vi.fn().mockResolvedValue(undefined),
      scoreWindows: vi.fn().mockResolvedValue(undefined),
      drift: vi.fn().mockResolvedValue(undefined),
      policyEffectiveness: vi.fn().mockResolvedValue(undefined),
      highRiskFunnel: vi.fn().mockResolvedValue(undefined),
      recommendations: vi.fn().mockResolvedValue(undefined),
      assistantQuestions: vi.fn().mockResolvedValue(undefined),
      assistantAnswer: vi.fn().mockResolvedValue(undefined),
      liveMetrics: vi.fn().mockResolvedValue(undefined),
      events: vi.fn().mockResolvedValue(undefined),
      simulatorStatus: vi.fn().mockResolvedValue(undefined),
      simulatorScenarios: vi.fn().mockResolvedValue(undefined),
      simulatorStart: vi.fn().mockResolvedValue(undefined),
      simulatorStop: vi.fn().mockResolvedValue(undefined),
      simulatorPause: vi.fn().mockResolvedValue(undefined),
      simulatorResume: vi.fn().mockResolvedValue(undefined),
      simulatorReset: vi.fn().mockResolvedValue(undefined),
    },
  }
})

const { api } = await import('@/lib/api')
const mockApi = vi.mocked(api)
const { LivePage } = await import('@/routes/live')

// --------------------------------------------------------------------------
// A faithful SSE-over-fetch stand-in
// --------------------------------------------------------------------------

/** A `ReadableStreamDefaultReader` the test pushes bytes into. */
class FakeReader {
  private readonly pending: Uint8Array[] = []
  private waiter: ((result: ReadableStreamReadResult<Uint8Array>) => void) | null = null
  private rejector: ((reason: Error) => void) | null = null
  private finished = false
  /** How many times the hook has asked for more. Used to assert it stopped. */
  reads = 0

  push(text: string) {
    const bytes = new TextEncoder().encode(text)
    const waiter = this.waiter
    if (waiter) {
      this.waiter = null
      this.rejector = null
      waiter({ done: false, value: bytes })
    } else {
      this.pending.push(bytes)
    }
  }

  /** End the stream as a network error would: the in-flight read rejects. */
  breakConnection() {
    this.finished = true
    const rejector = this.rejector
    if (rejector) {
      this.waiter = null
      this.rejector = null
      rejector(new Error('stream interrupted'))
    }
  }

  read(): Promise<ReadableStreamReadResult<Uint8Array>> {
    this.reads += 1
    const next = this.pending.shift()
    if (next) return Promise.resolve({ done: false, value: next })
    if (this.finished) return Promise.reject(new Error('stream interrupted'))
    return new Promise((resolve, reject) => {
      this.waiter = resolve
      this.rejector = reject
    })
  }
}

class FakeStream {
  static instances: FakeStream[] = []

  readonly url: string
  readonly headers: Record<string, string>
  readonly signal: AbortSignal | undefined
  readonly reader = new FakeReader()
  closed = false

  private resolveResponse: ((response: Response) => void) | null = null
  private rejectResponse: ((reason: Error) => void) | null = null

  constructor(url: string, init?: RequestInit) {
    this.url = url
    this.headers = { ...((init?.headers as Record<string, string>) ?? {}) }
    this.signal = init?.signal ?? undefined
    // The hook aborts on unmount; recording it is how the cleanup test knows.
    this.signal?.addEventListener('abort', () => {
      this.closed = true
      this.reader.breakConnection()
    })
    FakeStream.instances.push(this)
  }

  /** The promise the hook awaits. Stays pending until `open` or `fail`. */
  response(): Promise<Response> {
    return new Promise((resolve, reject) => {
      this.resolveResponse = resolve
      this.rejectResponse = reject
    })
  }

  /** Complete the request with a 200 whose body is the reader above. */
  open() {
    this.resolveResponse?.({
      ok: true,
      status: 200,
      body: { getReader: () => this.reader },
    } as unknown as Response)
  }

  /** Fail: before `open` the request errors; after it, the stream breaks. */
  fail() {
    this.closed = true
    if (this.rejectResponse && !this.reader.reads) {
      const reject = this.rejectResponse
      this.rejectResponse = null
      this.resolveResponse = null
      reject(new Error('connection refused'))
      return
    }
    this.reader.breakConnection()
  }

  /** Write one real SSE frame, exactly as the server formats it. */
  emit(event: RiskEvent) {
    this.reader.push(
      `id: ${event.sequence}\n` +
        `event: ${event.event_type}\n` +
        `data: ${JSON.stringify(event)}\n\n`,
    )
  }
}

function riskEvent(overrides: Partial<RiskEvent> & { sequence: number }): RiskEvent {
  return {
    event_id: `EVT-${overrides.sequence}`,
    transaction_id: 'SIM_run_000001',
    event_type: 'transaction_received',
    transaction_sequence: 1,
    timestamp: '2026-08-24T12:00:00Z',
    payload: { simulated: true },
    ...overrides,
  }
}

function latest(): FakeStream {
  const source = FakeStream.instances.at(-1)
  if (!source) throw new Error('no stream request was made')
  return source
}

const idleStatus = {
  state: 'idle',
  run_id: null,
  scenario: null,
  transactions_per_second: null,
  max_transactions: null,
  seed: null,
  generated: 0,
  processed: 0,
  duplicates: 0,
  failed: 0,
  queue_depth: 0,
  queue_capacity: 32,
  observed_tps: 0,
  latency_p50_ms: null,
  latency_p95_ms: null,
  started_at: null,
  stopped_at: null,
  uptime_seconds: null,
  decisions: { approve: 0, step_up: 0, review: 0, block: 0 },
  investigations: 0,
  recent: [],
}

const runningStatus = {
  ...idleStatus,
  state: 'running',
  run_id: 'abc123',
  scenario: 'coordinated_fraud',
  transactions_per_second: 2,
  max_transactions: 20,
  seed: 42,
  generated: 6,
  processed: 5,
  observed_tps: 1.94,
  queue_depth: 1,
  decisions: { approve: 0, step_up: 0, review: 4, block: 1 },
  investigations: 5,
}

const metrics = {
  transactions_processed: 5,
  transactions_per_second: 1.94,
  high_risk_count: 5,
  review_count: 4,
  block_count: 1,
  approve_count: 0,
  step_up_count: 0,
  active_investigations: 5,
  queue_depth: 1,
  queue_capacity: 32,
  uptime_seconds: 12,
  simulator_state: 'running',
  scenario: 'coordinated_fraud',
  connected_clients: 1,
  dropped_deliveries: 0,
  total_events: 30,
  latest_sequence: 30,
}

const scenarios = {
  scenarios: [
    {
      scenario: 'coordinated_fraud',
      title: 'Coordinated ring',
      behaviour: 'Three customers sharing one device and one proxy IP.',
      expected_signal: 'Entity sharing no single transaction reveals.',
    },
  ],
  note: 'Scenarios control transaction characteristics only.',
}

beforeEach(() => {
  vi.clearAllMocks()
  FakeStream.instances = []
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
    new FakeStream(String(input), init).response(),
  )

  mockApi.simulatorScenarios.mockResolvedValue(scenarios)
  mockApi.simulatorStatus.mockResolvedValue(idleStatus)
  mockApi.liveMetrics.mockResolvedValue(metrics)
  mockApi.simulatorStart.mockResolvedValue(runningStatus)
  mockApi.simulatorStop.mockResolvedValue(idleStatus)
  mockApi.simulatorPause.mockResolvedValue({ ...runningStatus, state: 'paused' })
  mockApi.simulatorResume.mockResolvedValue(runningStatus)
  mockApi.simulatorReset.mockResolvedValue(idleStatus)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

// --------------------------------------------------------------------------
// Connection state
// --------------------------------------------------------------------------
describe('connection status', () => {
  it('starts as connecting and becomes live once the stream opens', async () => {
    renderWithRouter(<LivePage />, '/live')

    expect(await screen.findByText('CONNECTING')).toBeInTheDocument()
    latest().open()
    expect(await screen.findByText('LIVE')).toBeInTheDocument()
  })

  it('reports DISCONNECTED rather than a stale LIVE', async () => {
    renderWithRouter(<LivePage />, '/live')
    latest().open()
    await screen.findByText('LIVE')

    latest().fail()
    expect(await screen.findByText('DISCONNECTED')).toBeInTheDocument()
  })

  it('abandons the failed stream instead of reconnecting onto it', async () => {
    vi.useFakeTimers()
    try {
      renderWithRouter(<LivePage />, '/live')
      const failed = latest()
      failed.open()
      await vi.advanceTimersByTimeAsync(0)

      failed.fail()
      await vi.advanceTimersByTimeAsync(2500)

      // A new request, not a second reader on the dead one.
      expect(latest()).not.toBe(failed)
      expect(FakeStream.instances).toHaveLength(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('aborts the request when the page unmounts', async () => {
    const { unmount } = renderWithRouter(<LivePage />, '/live')
    const stream = latest()
    stream.open()
    await screen.findByText('LIVE')

    unmount()
    // Without this the stream stays open after navigation, and every visit to
    // the page leaves another one behind until the browser's connection limit
    // is reached.
    expect(stream.signal?.aborted).toBe(true)
  })

  it('sends the bearer token, which is why EventSource could not be used', async () => {
    renderWithRouter(<LivePage />, '/live')
    const stream = latest()

    expect(stream.headers['authorization']).toBe('Bearer test-token-admin')
    expect(stream.headers['accept']).toBe('text/event-stream')
    // The credential must never be in the URL: that lands it in access logs,
    // proxy logs and browser history.
    expect(stream.url).not.toContain('test-token-admin')
  })

  it('sends the cursor when reconnecting, so the server can resume', async () => {
    vi.useFakeTimers()
    try {
      renderWithRouter(<LivePage />, '/live')
      latest().open()
      // Let the hook take the response and start reading before the frame is
      // written; otherwise it never sees sequence 42 and has no cursor to send.
      await vi.advanceTimersByTimeAsync(0)
      latest().emit(riskEvent({ sequence: 42 }))
      await vi.advanceTimersByTimeAsync(0)

      latest().fail()
      await vi.advanceTimersByTimeAsync(2500)

      expect(latest().url).toContain('last_event_id=42')
    } finally {
      vi.useRealTimers()
    }
  })
})

// --------------------------------------------------------------------------
// The event feed
// --------------------------------------------------------------------------
describe('event feed', () => {
  it('shows an empty state before anything arrives', async () => {
    renderWithRouter(<LivePage />, '/live')
    latest().open()

    expect(await screen.findByText('No live transactions yet')).toBeInTheDocument()
  })

  it('renders a transaction as its stages arrive', async () => {
    renderWithRouter(<LivePage />, '/live')
    latest().open()

    latest().emit(
      riskEvent({
        sequence: 1,
        event_type: 'transaction_received',
        transaction_sequence: 1,
        payload: { simulated: true, amount: 24500, currency: 'INR' },
      }),
    )
    expect(await screen.findByText('SIM_run_000001')).toBeInTheDocument()

    latest().emit(
      riskEvent({
        sequence: 2,
        event_type: 'risk_scored',
        transaction_sequence: 2,
        payload: { simulated: true, fraud_probability: 0.20029 },
      }),
    )
    expect(await screen.findByText('20.03%')).toBeInTheDocument()

    latest().emit(
      riskEvent({
        sequence: 3,
        event_type: 'anomaly_detected',
        transaction_sequence: 3,
        payload: { simulated: true, anomaly_score: 100, severity: 'CRITICAL' },
      }),
    )
    expect(await screen.findAllByText('CRITICAL')).not.toHaveLength(0)

    latest().emit(
      riskEvent({
        sequence: 4,
        event_type: 'decision_created',
        transaction_sequence: 4,
        payload: {
          simulated: true,
          decision: 'REVIEW',
          reason_codes: ['MODEL_DISAGREEMENT'],
          matched_rules: ['MODEL_DISAGREEMENT_HIGH_ANOMALY'],
          requires_human_review: true,
        },
      }),
    )
    expect(await screen.findAllByText('REVIEW')).not.toHaveLength(0)
  })

  it('labels every simulated transaction', async () => {
    renderWithRouter(<LivePage />, '/live')
    latest().open()
    latest().emit(riskEvent({ sequence: 1, payload: { simulated: true, amount: 100 } }))

    expect(await screen.findByText('SIM')).toBeInTheDocument()
  })

  it('does not render an event twice after a replay', async () => {
    renderWithRouter(<LivePage />, '/live')
    latest().open()

    const event = riskEvent({ sequence: 5, payload: { simulated: true, amount: 900 } })
    latest().emit(event)
    await screen.findByText('SIM_run_000001')

    // The same event again, as a reconnect backlog would deliver it.
    latest().emit(event)
    latest().emit(riskEvent({ sequence: 3, transaction_id: 'SIM_older' }))

    await waitFor(() => {
      expect(screen.getAllByText('SIM_run_000001')).toHaveLength(1)
    })
    // An older sequence is behind the cursor and must be ignored entirely.
    expect(screen.queryByText('SIM_older')).not.toBeInTheDocument()
  })

  it('keeps one row per transaction as its stages accumulate', async () => {
    renderWithRouter(<LivePage />, '/live')
    latest().open()

    for (const [index, type] of [
      'transaction_received',
      'risk_scored',
      'anomaly_detected',
      'decision_created',
    ].entries()) {
      latest().emit(
        riskEvent({
          sequence: index + 1,
          event_type: type,
          transaction_sequence: index + 1,
          payload: { simulated: true, amount: 100, decision: 'APPROVE' },
        }),
      )
    }

    await waitFor(() => {
      expect(screen.getAllByText('SIM_run_000001')).toHaveLength(1)
    })
  })
})

// --------------------------------------------------------------------------
// The investigation panel
// --------------------------------------------------------------------------
describe('investigation panel', () => {
  async function emitHighRisk() {
    latest().open()
    latest().emit(
      riskEvent({
        sequence: 1,
        event_type: 'transaction_received',
        payload: { simulated: true, amount: 24500, currency: 'INR' },
      }),
    )
    latest().emit(
      riskEvent({
        sequence: 2,
        event_type: 'investigation_started',
        transaction_sequence: 4,
        payload: { simulated: true },
      }),
    )
  }

  it('shows the investigation as running before it completes', async () => {
    renderWithRouter(<LivePage />, '/live')
    await emitHighRisk()

    latest().emit(
      riskEvent({
        sequence: 3,
        event_type: 'decision_created',
        transaction_sequence: 6,
        payload: { simulated: true, decision: 'REVIEW', requires_human_review: true },
      }),
    )

    expect(await screen.findByText('Live investigation')).toBeInTheDocument()
    expect(await screen.findAllByText('RUNNING')).not.toHaveLength(0)
  })

  it('shows findings, rules and reason codes once complete', async () => {
    renderWithRouter(<LivePage />, '/live')
    await emitHighRisk()

    latest().emit(
      riskEvent({
        sequence: 3,
        event_type: 'investigation_completed',
        transaction_sequence: 5,
        payload: { simulated: true, risk_level: 'HIGH', findings: 2, evidence: 11 },
      }),
    )
    latest().emit(
      riskEvent({
        sequence: 4,
        event_type: 'decision_created',
        transaction_sequence: 6,
        payload: {
          simulated: true,
          decision: 'REVIEW',
          requires_human_review: true,
          matched_rules: ['MODEL_DISAGREEMENT_HIGH_ANOMALY'],
          reason_codes: ['MODEL_DISAGREEMENT', 'COORDINATED_ACTIVITY'],
        },
      }),
    )

    await screen.findByText('Live investigation')
    // The sentence is assembled from several elements, so match the combined
    // text of the paragraph rather than a bare number.
    // Ancestors contain the same text, so match only the <p> that carries it.
    const summary = screen
      .getAllByText((_, element) =>
        /2\s*findings? from\s*11\s*evidence items/.test(element?.textContent ?? ''),
      )
      .filter((element) => element.tagName === 'P')
    expect(summary.length).toBeGreaterThan(0)
    expect(await screen.findByText('MODEL_DISAGREEMENT_HIGH_ANOMALY')).toBeInTheDocument()

    // Scoped to the panel: "Model disagreement" is also the label of an option
    // in the scenario dropdown, so an unscoped query matches two elements.
    const panel = summary[0]!.closest('.rounded-card') as HTMLElement
    expect(within(panel).getByText('Model disagreement')).toBeInTheDocument()
    expect(within(panel).getByText('Coordinated activity')).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// Metrics and controls
// --------------------------------------------------------------------------
describe('metrics', () => {
  it('renders live counters from the API', async () => {
    mockApi.simulatorStatus.mockResolvedValue(runningStatus)
    renderWithRouter(<LivePage />, '/live')

    expect(await screen.findByText('1.94')).toBeInTheDocument()
    expect(await screen.findByText('1 / 32')).toBeInTheDocument()
    expect(await screen.findByText(/Observed; 2\/s requested/)).toBeInTheDocument()
  })

  it('shows stream delivery statistics', async () => {
    renderWithRouter(<LivePage />, '/live')

    expect(await screen.findByText('Events recorded')).toBeInTheDocument()
    // Both `total_events` and `latest_sequence` are 30 in the fixture.
    expect((await screen.findAllByText('30')).length).toBeGreaterThan(0)
    expect(await screen.findByText('Connected clients')).toBeInTheDocument()
    expect(await screen.findByText(/durable copy retained/)).toBeInTheDocument()
  })

  it('shows an error state when metrics cannot be loaded', async () => {
    mockApi.liveMetrics.mockRejectedValue(new ApiError('database unavailable', 503))
    renderWithRouter(<LivePage />, '/live')

    expect(await screen.findByText('database unavailable')).toBeInTheDocument()
  })
})

describe('simulator controls', () => {
  it('starts the selected scenario with the chosen settings', async () => {
    const user = userEvent.setup()
    renderWithRouter(<LivePage />, '/live')
    await screen.findByRole('button', { name: 'Start' })

    await user.selectOptions(screen.getByLabelText('Scenario'), 'coordinated_fraud')
    await user.click(screen.getByRole('button', { name: 'Start' }))

    await waitFor(() =>
      expect(mockApi.simulatorStart).toHaveBeenCalledWith(
        expect.objectContaining({ scenario: 'coordinated_fraud' }),
      ),
    )
  })

  it('offers pause and stop while running', async () => {
    mockApi.simulatorStatus.mockResolvedValue(runningStatus)
    renderWithRouter(<LivePage />, '/live')

    expect(await screen.findByRole('button', { name: 'Pause' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start' })).not.toBeInTheDocument()
  })

  it('offers resume while paused', async () => {
    mockApi.simulatorStatus.mockResolvedValue({ ...runningStatus, state: 'paused' })
    renderWithRouter(<LivePage />, '/live')

    expect(await screen.findByRole('button', { name: 'Resume' })).toBeInTheDocument()
  })

  it('surfaces a rejected control action', async () => {
    const user = userEvent.setup()
    mockApi.simulatorStart.mockRejectedValue(
      new ApiError('simulator is already running; stop it first', 409),
    )
    renderWithRouter(<LivePage />, '/live')
    await user.click(await screen.findByRole('button', { name: 'Start' }))

    expect(
      await screen.findByText('simulator is already running; stop it first'),
    ).toBeInTheDocument()
  })

  it('shows the active scenario', async () => {
    mockApi.simulatorStatus.mockResolvedValue(runningStatus)
    renderWithRouter(<LivePage />, '/live')

    expect(await screen.findByText('COORDINATED FRAUD')).toBeInTheDocument()
  })

  it('says simulated traffic is not production traffic', async () => {
    renderWithRouter(<LivePage />, '/live')

    expect(await screen.findByText(/None of it is production traffic/)).toBeInTheDocument()
    expect(
      screen.getByText(/never sets a fraud probability, an anomaly score or a decision/),
    ).toBeInTheDocument()
  })

  it('documents what each scenario generates', async () => {
    renderWithRouter(<LivePage />, '/live')

    expect(await screen.findByText('Coordinated ring')).toBeInTheDocument()
    expect(
      screen.getByText(/Three customers sharing one device and one proxy IP/),
    ).toBeInTheDocument()
  })
})
