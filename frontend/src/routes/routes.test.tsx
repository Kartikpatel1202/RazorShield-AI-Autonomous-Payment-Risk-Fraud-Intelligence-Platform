/**
 * Page-level rendering, including the states a risk console must never get
 * wrong: loading, empty, and error.
 *
 * The API module is mocked so each test states exactly what the server
 * returned. That makes "renders 18,038 approvals" an assertion about the
 * component, not about whatever the database happened to hold.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/lib/api'
import * as fixtures from '@/test/fixtures'
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

const { App } = await import('@/App')

function resolveAll() {
  mockApi.overview.mockResolvedValue(fixtures.overview)
  mockApi.riskDistribution.mockResolvedValue(fixtures.riskDistribution)
  mockApi.decisionAnalytics.mockResolvedValue(fixtures.decisionAnalytics)
  mockApi.trends.mockResolvedValue(fixtures.trends)
  mockApi.topRisk.mockResolvedValue(fixtures.topRisk)
  mockApi.explorer.mockResolvedValue(fixtures.explorerPage)
  mockApi.transactionDetail.mockResolvedValue(fixtures.transactionDetail)
  mockApi.reviews.mockResolvedValue(fixtures.reviewPage)
  mockApi.audit.mockResolvedValue(fixtures.auditPage)
  mockApi.auditSummary.mockResolvedValue(fixtures.auditSummary)
  mockApi.policy.mockResolvedValue(fixtures.policy)
  mockApi.systemHealth.mockResolvedValue(fixtures.systemHealth)
  mockApi.modelMonitoring.mockResolvedValue(fixtures.modelMonitoring)
  mockApi.drift.mockResolvedValue(fixtures.driftReport)
}

beforeEach(() => {
  vi.clearAllMocks()
  resolveAll()
})

// --------------------------------------------------------------------------
// Navigation
// --------------------------------------------------------------------------
describe('navigation', () => {
  it('exposes every sidebar destination with no placeholders', async () => {
    renderWithRouter(<App />, '/dashboard')
    const nav = screen.getByRole('navigation', { name: 'Primary' })

    for (const label of [
      'Dashboard',
      'Transactions',
      'Investigations',
      'Reviews',
      'Policy',
      'Audit Log',
    ]) {
      expect(within(nav).getByRole('link', { name: label })).toBeInTheDocument()
    }
    expect(within(nav).queryByText(/soon/i)).not.toBeInTheDocument()
  })

  it('redirects the root path to the dashboard', async () => {
    renderWithRouter(<App />, '/')
    expect(
      await screen.findByRole('heading', { name: /Risk Command Center/i }),
    ).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// Dashboard
// --------------------------------------------------------------------------
describe('dashboard', () => {
  it('renders headline metrics from the API', async () => {
    renderWithRouter(<App />, '/dashboard')

    // These figures deliberately appear more than once - the stat card and the
    // distribution chart both report them - so assert presence, not uniqueness.
    expect((await screen.findAllByText('18,038')).length).toBeGreaterThan(0) // approved
    expect(screen.getAllByText('1,551').length).toBeGreaterThan(0) // step-up
    expect(screen.getAllByText('411').length).toBeGreaterThan(0) // open reviews
    expect(screen.getAllByText('20,000').length).toBeGreaterThan(0)
  })

  it('states the scope of every metric', async () => {
    renderWithRouter(<App />, '/dashboard')

    expect(await screen.findByText('Entire dataset')).toBeInTheDocument()
    expect(screen.getByText(/Fraud probability ≥ 0.5332/)).toBeInTheDocument()
    expect(screen.getByText(/Anomaly score ≥ 99.4/)).toBeInTheDocument()
  })

  it('reports the measured decision latency with its sample size', async () => {
    renderWithRouter(<App />, '/dashboard')

    expect(await screen.findByText('0.179 ms')).toBeInTheDocument()
    expect(screen.getByText(/Mean over 20,000 policy evaluations/)).toBeInTheDocument()
  })

  it('admits when latency was never measured instead of showing zero', async () => {
    mockApi.overview.mockResolvedValue({
      ...fixtures.overview,
      avg_decision_latency_ms: null,
      latency_sample_size: 0,
    })
    renderWithRouter(<App />, '/dashboard')

    expect(await screen.findByText('not measured')).toBeInTheDocument()
  })

  it('renders the decision distribution with counts', async () => {
    renderWithRouter(<App />, '/dashboard')

    // Each dashboard panel is a labelled region, so this scopes by name
    // rather than by walking the DOM - which broke the moment the card
    // header gained a wrapper.
    const card = await screen.findByRole('region', { name: 'Decision distribution' })
    expect(within(card).getByText('APPROVE')).toBeInTheDocument()
    expect(within(card).getByText('BLOCK')).toBeInTheDocument()
  })

  it('renders system health from the health endpoint', async () => {
    renderWithRouter(<App />, '/dashboard')

    expect(await screen.findByText('All systems operational')).toBeInTheDocument()
    expect(screen.getByText('xgboost-v1')).toBeInTheDocument()
    expect(screen.getByText('isolation-forest-v1')).toBeInTheDocument()
  })

  it('surfaces a degraded subsystem rather than hiding it', async () => {
    mockApi.systemHealth.mockResolvedValue(fixtures.degradedHealth)
    renderWithRouter(<App />, '/dashboard')

    expect(await screen.findByText(/1 subsystem degraded/)).toBeInTheDocument()
  })

  it('changes the trend window on request', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/dashboard')

    await screen.findByText('Risk trend')
    await user.click(screen.getByRole('button', { name: '7d' }))

    await waitFor(() => expect(mockApi.trends).toHaveBeenCalledWith(7, expect.anything()))
  })

  it('shows an empty state rather than a broken chart', async () => {
    mockApi.trends.mockResolvedValue(fixtures.emptyTrends)
    renderWithRouter(<App />, '/dashboard')

    expect(await screen.findByText('No transactions in this window')).toBeInTheDocument()
  })

  it('shows an error state with the server message and a retry', async () => {
    mockApi.trends.mockRejectedValue(new ApiError('days must be at least 1', 422))
    renderWithRouter(<App />, '/dashboard')

    expect(await screen.findByText('days must be at least 1')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Try again' }).length).toBeGreaterThan(0)
  })

  it('retries the failed query when asked', async () => {
    const user = userEvent.setup()
    mockApi.trends.mockRejectedValueOnce(new ApiError('temporary failure', 500))
    renderWithRouter(<App />, '/dashboard')

    await screen.findByText('temporary failure')
    mockApi.trends.mockResolvedValue(fixtures.trends)
    await user.click(screen.getAllByRole('button', { name: 'Try again' })[0]!)

    await waitFor(() => expect(mockApi.trends).toHaveBeenCalledTimes(2))
  })

  it('shows a loading state before data arrives', () => {
    mockApi.overview.mockReturnValue(new Promise(() => {}))
    mockApi.trends.mockReturnValue(new Promise(() => {}))
    renderWithRouter(<App />, '/dashboard')

    expect(screen.getAllByRole('status').length).toBeGreaterThan(0)
  })
})

// --------------------------------------------------------------------------
// Transaction explorer
// --------------------------------------------------------------------------
describe('transaction explorer', () => {
  it('renders every required column', async () => {
    renderWithRouter(<App />, '/transactions')

    await screen.findByText('TXN_SCENARIO_C_CURRENT_1')
    for (const header of [
      'Transaction',
      'Timestamp',
      'Amount',
      'Customer',
      'Merchant',
      'Fraud prob.',
      'Anomaly',
      'Severity',
      'Risk level',
      'Decision',
    ]) {
      expect(screen.getByRole('columnheader', { name: new RegExp(header, 'i') })).toBeInTheDocument()
    }
  })

  it('requests one server-side page, not the whole table', async () => {
    renderWithRouter(<App />, '/transactions')

    await screen.findByText('TXN_SCENARIO_C_CURRENT_1')
    expect(mockApi.explorer).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, page_size: 25 }),
      expect.anything(),
    )
    // Split across elements by the pagination markup, so read the combined
    // text of the paragraph that reports the total.
    const totals = screen
      .getAllByText((_, element) => /20,000\s*transactions/.test(element?.textContent ?? ''))
      .filter((element) => element.tagName === 'P')
    expect(totals.length).toBeGreaterThan(0)
  })

  it('sends a decision filter to the server', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/transactions')
    await screen.findByText('TXN_SCENARIO_C_CURRENT_1')

    await user.selectOptions(screen.getByLabelText('Decision'), 'block')

    await waitFor(() =>
      expect(mockApi.explorer).toHaveBeenLastCalledWith(
        expect.objectContaining({ decision: 'block' }),
        expect.anything(),
      ),
    )
  })

  it('sends a severity filter to the server', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/transactions')
    await screen.findByText('TXN_SCENARIO_C_CURRENT_1')

    await user.selectOptions(screen.getByLabelText('Anomaly severity'), 'CRITICAL')

    await waitFor(() =>
      expect(mockApi.explorer).toHaveBeenLastCalledWith(
        expect.objectContaining({ anomaly_severity: 'CRITICAL' }),
        expect.anything(),
      ),
    )
  })

  it('sorts server-side when a column header is used', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/transactions')
    await screen.findByText('TXN_SCENARIO_C_CURRENT_1')

    await user.click(screen.getByRole('button', { name: /Amount/i }))

    await waitFor(() =>
      expect(mockApi.explorer).toHaveBeenLastCalledWith(
        expect.objectContaining({ sort_by: 'amount', descending: true }),
        expect.anything(),
      ),
    )
  })

  it('advances the page without refetching everything', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/transactions')
    await screen.findByText('TXN_SCENARIO_C_CURRENT_1')

    await user.click(screen.getByRole('button', { name: 'Next' }))

    await waitFor(() =>
      expect(mockApi.explorer).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 2 }),
        expect.anything(),
      ),
    )
  })

  it('shows an empty state when no transaction matches', async () => {
    mockApi.explorer.mockResolvedValue(fixtures.emptyExplorerPage)
    renderWithRouter(<App />, '/transactions')

    expect(await screen.findByText('No transactions match these filters')).toBeInTheDocument()
  })

  it('shows an error state when the query is rejected', async () => {
    mockApi.explorer.mockRejectedValue(new ApiError('invalid sort key', 422))
    renderWithRouter(<App />, '/transactions')

    expect(await screen.findByText('invalid sort key')).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// Transaction detail
// --------------------------------------------------------------------------
describe('transaction detail', () => {
  it('renders the full pipeline with real values', async () => {
    renderWithRouter(<App />, '/transactions/TXN_SCENARIO_C_CURRENT_1')

    await screen.findByText('Decision pipeline')
    expect(screen.getAllByText('20.03%').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/100 \/ 100/).length).toBeGreaterThan(0)
    // Stage titles repeat as field labels further down the page.
    for (const stage of ['Fraud model', 'Anomaly model', 'AI investigation']) {
      expect(screen.getAllByText(stage).length).toBeGreaterThan(0)
    }
    expect(screen.getByText('Policy engine')).toBeInTheDocument()
    expect(screen.getByText('Final decision')).toBeInTheDocument()
  })

  it('explains the model disagreement when the decision recorded one', async () => {
    renderWithRouter(<App />, '/transactions/TXN_SCENARIO_C_CURRENT_1')

    expect(await screen.findByText(/Models disagree/)).toBeInTheDocument()
    expect(screen.getByText(/signature of a coordinated ring/)).toBeInTheDocument()
  })

  it('renders findings and evidence exactly as returned', async () => {
    renderWithRouter(<App />, '/transactions/TXN_SCENARIO_C_CURRENT_1')

    await screen.findByText('F-001')
    expect(screen.getByText('Elevated risk indicators observed')).toBeInTheDocument()
    expect(screen.getAllByText('EV-004').length).toBeGreaterThan(0)
    expect(screen.getByText('get_device_history')).toBeInTheDocument()
    expect(
      screen.getByText('Device is shared across 3 distinct customers before this transaction'),
    ).toBeInTheDocument()
  })

  it('shows the audit fields needed to reconstruct the decision', async () => {
    renderWithRouter(<App />, '/transactions/TXN_SCENARIO_C_CURRENT_1')

    await screen.findByText('Provenance')
    expect(screen.getAllByText('DEC-abc123def456').length).toBeGreaterThan(0)
    expect(screen.getAllByText('INV-4A929E29E226').length).toBeGreaterThan(0)
    expect(screen.getByText(/857da42d58e4/)).toBeInTheDocument()
  })

  it('marks the agent recommendation as advisory, not a policy input', async () => {
    renderWithRouter(<App />, '/transactions/TXN_SCENARIO_C_CURRENT_1')

    expect(await screen.findByText(/advisory - not a policy input/)).toBeInTheDocument()
  })

  it('flags a mock-produced investigation', async () => {
    renderWithRouter(<App />, '/transactions/TXN_SCENARIO_C_CURRENT_1')

    expect(await screen.findByText('MOCK PROVIDER')).toBeInTheDocument()
  })

  it('handles a transaction with no signals, investigation or decision', async () => {
    mockApi.transactionDetail.mockResolvedValue(fixtures.bareTransactionDetail)
    renderWithRouter(<App />, '/transactions/TXN_BARE')

    expect(await screen.findByText(/No investigation has been run/)).toBeInTheDocument()
    expect(
      screen.getAllByText(/has not been through the decision engine/).length,
    ).toBeGreaterThan(0)
  })

  it('shows an error state for an unknown transaction', async () => {
    mockApi.transactionDetail.mockRejectedValue(new ApiError('No such transaction', 404))
    renderWithRouter(<App />, '/transactions/TXN_NOPE')

    expect(await screen.findByText('No such transaction')).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// Review queue
// --------------------------------------------------------------------------
describe('review queue', () => {
  it('lists open cases with the machine decision', async () => {
    renderWithRouter(<App />, '/reviews')

    await screen.findByText('TXN_SCENARIO_B_CURRENT')
    expect(screen.getAllByText('BLOCK').length).toBeGreaterThan(0)
    // The investigation id moved off the queue row: what an analyst triages
    // on is the score, not the identifier. The full chain is one click away.
    expect(screen.getByText('99.96%')).toBeInTheDocument()
  })

  it('separates the machine decision from the human resolution', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/reviews')
    await screen.findByText('TXN_SCENARIO_B_CURRENT')

    await user.click(screen.getByRole('button', { name: 'Review' }))

    expect((await screen.findAllByText('Machine decision')).length).toBeGreaterThan(0)
    expect(screen.getByText('Human resolution')).toBeInTheDocument()
    expect(screen.getByText(/This record is immutable/)).toBeInTheDocument()
    expect(screen.getByText(/never over it/)).toBeInTheDocument()
  })

  it('offers approve, reject and escalate', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/reviews')
    await screen.findByText('TXN_SCENARIO_B_CURRENT')
    await user.click(screen.getByRole('button', { name: 'Review' }))

    expect(await screen.findByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Escalate' })).toBeInTheDocument()
  })

  it('submits a resolution with its reason', async () => {
    const user = userEvent.setup()
    mockApi.resolveReview.mockResolvedValue({
      review_case_id: 825,
      transaction_id: 'TXN_SCENARIO_B_CURRENT',
      status: 'resolved',
      resolution: 'approved',
      resolution_reason: 'Verified with the cardholder',
      resolved_at: '2026-08-23T10:00:00Z',
      machine_decision: 'BLOCK',
      machine_decision_id: 'DEC-ff401dd1e3f04e25',
      overrides_machine_decision: true,
    })
    renderWithRouter(<App />, '/reviews')
    await screen.findByText('TXN_SCENARIO_B_CURRENT')
    await user.click(screen.getByRole('button', { name: 'Review' }))

    await user.type(
      await screen.findByPlaceholderText('Why are you reaching this outcome?'),
      'Verified with the cardholder',
    )
    await user.click(screen.getByRole('button', { name: 'Approve' }))

    await waitFor(() =>
      expect(mockApi.resolveReview).toHaveBeenCalledWith(825, {
        resolution: 'approved',
        reason: 'Verified with the cardholder',
      }),
    )
  })

  it('reports a failed resolution instead of pretending it worked', async () => {
    const user = userEvent.setup()
    mockApi.resolveReview.mockRejectedValue(
      new ApiError('review case 825 is already resolved', 409),
    )
    renderWithRouter(<App />, '/reviews')
    await screen.findByText('TXN_SCENARIO_B_CURRENT')
    await user.click(screen.getByRole('button', { name: 'Review' }))
    await user.click(await screen.findByRole('button', { name: 'Approve' }))

    expect(await screen.findByText('review case 825 is already resolved')).toBeInTheDocument()
  })

  it('shows an empty state for an empty queue', async () => {
    mockApi.reviews.mockResolvedValue(fixtures.emptyReviewPage)
    renderWithRouter(<App />, '/reviews')

    expect(await screen.findByText('Nothing in this queue')).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// Policy viewer
// --------------------------------------------------------------------------
describe('policy viewer', () => {
  it('renders the active policy read-only', async () => {
    renderWithRouter(<App />, '/rules')

    expect(await screen.findByText('policy-v1')).toBeInTheDocument()
    expect(screen.getByText('READ-ONLY')).toBeInTheDocument()
    expect(screen.getByText(/Editing is deliberately not available/)).toBeInTheDocument()
  })

  it('shows thresholds, precedence, fail-safes and rules', async () => {
    renderWithRouter(<App />, '/rules')

    await screen.findByText('Thresholds')
    expect(screen.getByText('fraud_block')).toBeInTheDocument()
    expect(screen.getByText('0.533209')).toBeInTheDocument()
    expect(screen.getByText('Action precedence')).toBeInTheDocument()
    expect(screen.getByText('Fail-safe behaviour')).toBeInTheDocument()
    expect(screen.getByText('CRITICAL_SUPERVISED_RISK')).toBeInTheDocument()
    expect(screen.getByText('WITHHELD')).toBeInTheDocument()
  })

  it('offers no way to edit the policy', async () => {
    renderWithRouter(<App />, '/rules')
    await screen.findByText('Thresholds')

    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /save|edit|apply/i })).not.toBeInTheDocument()
  })

  it('shows an error state when the policy is invalid', async () => {
    mockApi.policy.mockRejectedValue(
      new ApiError('The risk policy configuration is invalid.', 503),
    )
    renderWithRouter(<App />, '/rules')

    expect(
      await screen.findByText('The risk policy configuration is invalid.'),
    ).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// Audit and investigations
// --------------------------------------------------------------------------
describe('audit log', () => {
  it('lists events with the fields needed to explain a decision', async () => {
    renderWithRouter(<App />, '/audit')

    // Appears as a filter chip and as a row value.
    expect((await screen.findAllByText('risk.decision')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('TXN_SCENARIO_C_CURRENT_1').length).toBeGreaterThan(0)
    expect(screen.getAllByText('policy-v1').length).toBeGreaterThan(0)
  })

  it('expands an event to show the full document', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/audit')
    await screen.findAllByText('risk.decision')

    await user.click(screen.getByRole('button', { name: 'Details' }))

    expect(await screen.findByText('Full event document')).toBeInTheDocument()
    expect(screen.getByText('MODEL_DISAGREEMENT')).toBeInTheDocument()
  })

  it('filters by event type', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/audit')
    await screen.findAllByText('risk.decision')

    await user.click(screen.getByRole('button', { name: /investigation.completed/ }))

    await waitFor(() =>
      expect(mockApi.audit).toHaveBeenLastCalledWith(
        expect.objectContaining({ event_type: 'investigation.completed' }),
        expect.anything(),
      ),
    )
  })

  it('shows an error state when the trail cannot be read', async () => {
    mockApi.audit.mockRejectedValue(new ApiError('database unavailable', 503))
    renderWithRouter(<App />, '/audit')

    expect(await screen.findByText('database unavailable')).toBeInTheDocument()
  })
})

describe('investigations', () => {
  it('lists completed investigations from the audit trail', async () => {
    mockApi.audit.mockResolvedValue({
      ...fixtures.auditPage,
      items: [
        {
          ...fixtures.auditPage.items[0]!,
          event_type: 'investigation.completed',
          event_data: {
            investigation_id: 'INV-4A929E29E226',
            risk_level: 'HIGH',
            confidence: 0.925,
            evidence_count: 11,
            finding_count: 2,
            llm_is_mock: true,
            llm_provider: 'mock',
          },
        },
      ],
    })
    renderWithRouter(<App />, '/investigations')

    await screen.findByText('INV-4A929E29E226')
    expect(screen.getByText('HIGH')).toBeInTheDocument()
    expect(screen.getByText('92.5%')).toBeInTheDocument()
    expect(screen.getByText('MOCK')).toBeInTheDocument()
  })

  it('shows an empty state when none exist', async () => {
    mockApi.audit.mockResolvedValue({ ...fixtures.auditPage, items: [] })
    renderWithRouter(<App />, '/investigations')

    expect(await screen.findByText('No investigations yet')).toBeInTheDocument()
  })
})
