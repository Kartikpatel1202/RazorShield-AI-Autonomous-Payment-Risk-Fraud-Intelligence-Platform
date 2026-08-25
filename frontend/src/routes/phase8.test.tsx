/**
 * Feedback and monitoring pages.
 *
 * The assertions concentrate on the claims these pages must never make: that a
 * metric exists when the data cannot support it, that unlabelled transactions
 * are negatives, or that drift means fraud.
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

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.systemHealth.mockResolvedValue(fixtures.systemHealth)
  mockApi.feedback.mockResolvedValue(fixtures.feedbackPage)
  mockApi.feedbackSummary.mockResolvedValue(fixtures.feedbackSummary)
  mockApi.modelMonitoring.mockResolvedValue(fixtures.modelMonitoring)
  mockApi.scoreWindows.mockResolvedValue(fixtures.scoreWindows)
  mockApi.drift.mockResolvedValue(fixtures.driftReport)
  mockApi.policyEffectiveness.mockResolvedValue(fixtures.policyEffectiveness)
  mockApi.highRiskFunnel.mockResolvedValue(fixtures.highRiskFunnel)
  mockApi.recommendations.mockResolvedValue(fixtures.recommendations)
  mockApi.assistantQuestions.mockResolvedValue(fixtures.assistantQuestions)
  mockApi.assistantAnswer.mockResolvedValue(fixtures.assistantAnswer)
  mockApi.reviews.mockResolvedValue(fixtures.reviewPage)
})

// --------------------------------------------------------------------------
// Navigation
// --------------------------------------------------------------------------
describe('navigation', () => {
  it('exposes Feedback and Monitoring', () => {
    renderWithRouter(<App />, '/feedback')
    const nav = screen.getByRole('navigation', { name: 'Primary' })

    expect(within(nav).getByRole('link', { name: 'Feedback' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Monitoring' })).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// Feedback page
// --------------------------------------------------------------------------
describe('feedback page', () => {
  it('renders every outcome counter', async () => {
    renderWithRouter(<App />, '/feedback')

    await screen.findByText('Analyst feedback')
    expect(screen.getAllByText('63').length).toBeGreaterThan(0) // confirmed fraud
    expect(screen.getAllByText('74').length).toBeGreaterThan(0) // legitimate
    expect(screen.getAllByText('57').length).toBeGreaterThan(0) // false positives
    expect(screen.getAllByText('6').length).toBeGreaterThan(0) // false negatives
  })

  it('reports labelling coverage next to the counts', async () => {
    renderWithRouter(<App />, '/feedback')

    expect(await screen.findByText(/1.00% of 20,000 transactions/)).toBeInTheDocument()
  })

  it('renders the confusion matrix with all four quadrants', async () => {
    renderWithRouter(<App />, '/feedback')

    await screen.findByText('Machine vs human')
    expect(screen.getByText('True positive')).toBeInTheDocument()
    expect(screen.getByText('False positive')).toBeInTheDocument()
    expect(screen.getByText('True negative')).toBeInTheDocument()
    expect(screen.getByText('False negative')).toBeInTheDocument()
  })

  it('states that unlabelled transactions are not negatives', async () => {
    renderWithRouter(<App />, '/feedback')

    expect(
      await screen.findByText(/never counted as negatives/i),
    ).toBeInTheDocument()
  })

  it('lists feedback records with both the machine decision and the outcome', async () => {
    renderWithRouter(<App />, '/feedback')

    await screen.findByText('TXN_SCENARIO_C_CURRENT_1')
    // The outcome appears both as a filter chip and as a row badge, so scope the
    // assertion to the table rather than the whole page.
    const table = screen.getByRole('table')
    expect(within(table).getAllByText('REVIEW').length).toBeGreaterThan(0)
    expect(within(table).getByText('CONFIRMED FRAUD')).toBeInTheDocument()
    expect(within(table).getByText('Coordinated activity')).toBeInTheDocument()
  })

  it('filters by outcome', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/feedback')
    await screen.findByText('TXN_SCENARIO_C_CURRENT_1')

    await user.click(screen.getByRole('button', { name: 'legitimate' }))

    await waitFor(() =>
      expect(mockApi.feedback).toHaveBeenLastCalledWith(
        expect.objectContaining({ outcome: 'legitimate' }),
        expect.anything(),
      ),
    )
  })

  it('shows an empty state when nothing is labelled', async () => {
    mockApi.feedbackSummary.mockResolvedValue(fixtures.emptyFeedbackSummary)
    mockApi.feedback.mockResolvedValue(fixtures.emptyFeedbackPage)
    renderWithRouter(<App />, '/feedback')

    expect(await screen.findByText(/No ground-truth labels yet/)).toBeInTheDocument()
    expect(screen.getByText('No feedback matches this filter')).toBeInTheDocument()
  })

  it('shows an error state when the summary fails', async () => {
    mockApi.feedbackSummary.mockRejectedValue(new ApiError('database unavailable', 503))
    renderWithRouter(<App />, '/feedback')

    expect(await screen.findAllByText('database unavailable')).toBeTruthy()
  })
})

// --------------------------------------------------------------------------
// Monitoring: models
// --------------------------------------------------------------------------
describe('monitoring - models', () => {
  it('renders precision, recall and F1', async () => {
    renderWithRouter(<App />, '/monitoring/models')

    await screen.findByText('Model performance')
    expect(screen.getByText('52.5%')).toBeInTheDocument()
    expect(screen.getByText('91.3%')).toBeInTheDocument()
    expect(screen.getByText('0.667')).toBeInTheDocument()
  })

  it('says "insufficient labeled data" instead of showing a number', async () => {
    mockApi.modelMonitoring.mockResolvedValue(fixtures.insufficientModelMonitoring)
    renderWithRouter(<App />, '/monitoring/models')

    expect(await screen.findByText('Insufficient labeled data')).toBeInTheDocument()
    expect(screen.queryByText('52.5%')).not.toBeInTheDocument()
  })

  it('surfaces a sampling caveat when the labelled set is biased', async () => {
    mockApi.modelMonitoring.mockResolvedValue(fixtures.biasedModelMonitoring)
    renderWithRouter(<App />, '/monitoring/models')

    expect(await screen.findByText('Sampling caveat.')).toBeInTheDocument()
    expect(screen.getByText(/by construction rather than by measurement/)).toBeInTheDocument()
  })

  it('separates confirmed labels from the simulated dataset flag', async () => {
    renderWithRouter(<App />, '/monitoring/models')

    await screen.findByText('Label coverage')
    expect(screen.getByText('Confirmed labels')).toBeInTheDocument()
    expect(screen.getByText('Simulated flags')).toBeInTheDocument()
    expect(screen.getByText(/never used as ground truth/)).toBeInTheDocument()
  })

  it('renders the baseline and current score windows', async () => {
    renderWithRouter(<App />, '/monitoring/models')

    await screen.findByText('Score distributions')
    expect(screen.getByText('13,273')).toBeInTheDocument()
    expect(screen.getByText('6,725')).toBeInTheDocument()
  })

  it('renders the high-risk funnel with every stage', async () => {
    renderWithRouter(<App />, '/monitoring/models')

    await screen.findByText('High-risk decision funnel')
    expect(screen.getByText('HIGH FRAUD SCORE')).toBeInTheDocument()
    expect(screen.getByText('INVESTIGATION AVAILABLE')).toBeInTheDocument()
    expect(screen.getByText('FINAL DECISION BLOCK')).toBeInTheDocument()
    expect(screen.getByText(/−257 filtered out at this step/)).toBeInTheDocument()
  })

  it('explains why high scores do not become blocks', async () => {
    renderWithRouter(<App />, '/monitoring/models')

    expect(
      await screen.findByText(/necessary but not sufficient for a block/),
    ).toBeInTheDocument()
  })

  it('renders recommendations and states nothing is executed', async () => {
    renderWithRouter(<App />, '/monitoring/models')

    await screen.findByText('Recommendations')
    expect(
      screen.getByText(/no model is retrained, no threshold moved/i),
    ).toBeInTheDocument()
    expect(
      screen.getByText('MODEL_DISAGREEMENT_HIGH_ANOMALY has a high analyst override rate'),
    ).toBeInTheDocument()
  })

  it('shows an empty state when nothing needs flagging', async () => {
    mockApi.recommendations.mockResolvedValue({ ...fixtures.recommendations, recommendations: [] })
    renderWithRouter(<App />, '/monitoring/models')

    expect(await screen.findByText('Nothing to flag')).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// Assistant
// --------------------------------------------------------------------------
describe('risk learning assistant', () => {
  it('offers the closed question set', async () => {
    renderWithRouter(<App />, '/monitoring/models')

    expect(
      await screen.findByRole('button', { name: 'Why were high-risk transactions not blocked?' }),
    ).toBeInTheDocument()
  })

  it('answers a question and names its sources', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/monitoring/models')

    await user.click(
      await screen.findByRole('button', { name: 'Why were high-risk transactions not blocked?' }),
    )

    expect(await screen.findByText(/258 transactions scored at or above/)).toBeInTheDocument()
    expect(screen.getByText(/\/api\/monitoring\/high-risk-funnel/)).toBeInTheDocument()
    expect(screen.getByText('258 decided high-score transactions')).toBeInTheDocument()
  })

  it('says when the data cannot answer', async () => {
    const user = userEvent.setup()
    mockApi.assistantAnswer.mockResolvedValue(fixtures.insufficientAssistantAnswer)
    renderWithRouter(<App />, '/monitoring/models')

    await user.click(
      await screen.findByRole('button', { name: 'Why were high-risk transactions not blocked?' }),
    )

    expect(await screen.findByText(/Insufficient labeled data/)).toBeInTheDocument()
  })

  it('states that no language model is involved', async () => {
    renderWithRouter(<App />, '/monitoring/models')

    expect(await screen.findByText(/No language model is involved/i)).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// Monitoring: drift
// --------------------------------------------------------------------------
describe('monitoring - drift', () => {
  it('renders a card per feature with its PSI and status', async () => {
    renderWithRouter(<App />, '/monitoring/drift')

    await screen.findByText('Distribution drift')
    expect(screen.getByText('Amount')).toBeInTheDocument()
    expect(screen.getByText('Anomaly score')).toBeInTheDocument()
    expect(screen.getByText('DRIFT DETECTED')).toBeInTheDocument()
    expect(screen.getByText('0.371')).toBeInTheDocument()
  })

  it('marks a feature with too little data rather than guessing', async () => {
    renderWithRouter(<App />, '/monitoring/drift')

    await screen.findByText('Distribution drift')
    expect(screen.getByText('INSUFFICIENT DATA')).toBeInTheDocument()
    expect(
      screen.getByText(/Too few rows in one window to compute a meaningful PSI/),
    ).toBeInTheDocument()
  })

  it('states that drift is not evidence of fraud', async () => {
    renderWithRouter(<App />, '/monitoring/drift')

    expect(await screen.findByText(/not evidence of fraud/)).toBeInTheDocument()
  })

  it('shows the thresholds that produced the bands', async () => {
    renderWithRouter(<App />, '/monitoring/drift')

    expect(await screen.findByText(/WATCH at PSI 0.1, DRIFT at 0.25/)).toBeInTheDocument()
  })

  it('shows an empty state without data', async () => {
    mockApi.drift.mockResolvedValue(fixtures.emptyDriftReport)
    renderWithRouter(<App />, '/monitoring/drift')

    expect(await screen.findByText('No data to compare')).toBeInTheDocument()
  })

  it('shows an error state when drift cannot be computed', async () => {
    mockApi.drift.mockRejectedValue(new ApiError('monitoring configuration is invalid', 503))
    renderWithRouter(<App />, '/monitoring/drift')

    expect(await screen.findByText('monitoring configuration is invalid')).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// Monitoring: policy effectiveness
// --------------------------------------------------------------------------
describe('monitoring - policy effectiveness', () => {
  it('renders a row per rule with its decision mix', async () => {
    renderWithRouter(<App />, '/monitoring/policy')

    await screen.findByText('Policy rule performance')
    expect(screen.getByText('MODEL_DISAGREEMENT_HIGH_ANOMALY')).toBeInTheDocument()
    expect(screen.getByText('LOW_RISK')).toBeInTheDocument()
    // 18,038 appears in both the triggers and approve columns of the same row.
    expect(screen.getAllByText('18,038').length).toBe(2)
  })

  it('flags a rule with a high override rate', async () => {
    renderWithRouter(<App />, '/monitoring/policy')

    await screen.findByText('Policy rule performance')
    expect(screen.getByText('HIGH OVERRIDE')).toBeInTheDocument()
    expect(screen.getByText('44%')).toBeInTheDocument()
  })

  it('withholds an override rate below the reporting floor', async () => {
    renderWithRouter(<App />, '/monitoring/policy')

    await screen.findByText('Policy rule performance')
    expect(screen.getByText('n/a')).toBeInTheDocument()
  })

  it('explains what an override does and does not mean', async () => {
    renderWithRouter(<App />, '/monitoring/policy')

    expect(
      await screen.findByText(/REVIEW cases are never overrides/),
    ).toBeInTheDocument()
  })

  it('shows an error state when the metrics cannot be read', async () => {
    mockApi.policyEffectiveness.mockRejectedValue(new ApiError('database unavailable', 503))
    renderWithRouter(<App />, '/monitoring/policy')

    expect(await screen.findByText('database unavailable')).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// Review integration
// --------------------------------------------------------------------------
describe('review feedback capture', () => {
  it('offers an outcome and reason when resolving', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/reviews')
    await screen.findByText('TXN_SCENARIO_B_CURRENT')
    await user.click(screen.getByRole('button', { name: 'Review' }))

    expect(await screen.findByLabelText('Outcome')).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Outcome'), 'confirmed_fraud')
    expect(await screen.findByLabelText('Reason')).toBeInTheDocument()
  })

  it('only offers reasons valid for the chosen outcome', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/reviews')
    await screen.findByText('TXN_SCENARIO_B_CURRENT')
    await user.click(screen.getByRole('button', { name: 'Review' }))
    await user.selectOptions(await screen.findByLabelText('Outcome'), 'legitimate')

    const reason = await screen.findByLabelText('Reason')
    expect(within(reason).getByRole('option', { name: 'trusted merchant' })).toBeInTheDocument()
    expect(
      within(reason).queryByRole('option', { name: 'coordinated activity' }),
    ).not.toBeInTheDocument()
  })

  it('blocks submission until the pair is complete', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/reviews')
    await screen.findByText('TXN_SCENARIO_B_CURRENT')
    await user.click(screen.getByRole('button', { name: 'Review' }))
    await user.selectOptions(await screen.findByLabelText('Outcome'), 'confirmed_fraud')

    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()
    expect(await screen.findByText(/Choose a reason to record this outcome/)).toBeInTheDocument()
  })

  it('submits the resolution with structured feedback', async () => {
    const user = userEvent.setup()
    mockApi.resolveReview.mockResolvedValue({
      review_case_id: 825,
      transaction_id: 'TXN_SCENARIO_B_CURRENT',
      status: 'resolved',
      resolution: 'rejected',
      resolution_reason: 'Confirmed ring.',
      resolved_at: '2026-08-23T10:00:00Z',
      machine_decision: 'BLOCK',
      machine_decision_id: 'DEC-ff401dd1e3f04e25',
      overrides_machine_decision: false,
    })
    renderWithRouter(<App />, '/reviews')
    await screen.findByText('TXN_SCENARIO_B_CURRENT')
    await user.click(screen.getByRole('button', { name: 'Review' }))

    await user.selectOptions(await screen.findByLabelText('Outcome'), 'confirmed_fraud')
    await user.selectOptions(await screen.findByLabelText('Reason'), 'coordinated_activity')
    await user.type(
      screen.getByPlaceholderText('Why are you reaching this outcome?'),
      'Confirmed ring.',
    )
    await user.click(screen.getByRole('button', { name: 'Reject' }))

    await waitFor(() =>
      expect(mockApi.resolveReview).toHaveBeenCalledWith(825, {
        resolution: 'rejected',
        reason: 'Confirmed ring.',
        feedback_outcome: 'confirmed_fraud',
        feedback_reason: 'coordinated_activity',
        feedback_notes: 'Confirmed ring.',
      }),
    )
  })

  it('still allows resolving without feedback', async () => {
    const user = userEvent.setup()
    mockApi.resolveReview.mockResolvedValue({
      review_case_id: 825,
      transaction_id: 'TXN_SCENARIO_B_CURRENT',
      status: 'resolved',
      resolution: 'approved',
      resolution_reason: null,
      resolved_at: '2026-08-23T10:00:00Z',
      machine_decision: 'BLOCK',
      machine_decision_id: 'DEC-ff401dd1e3f04e25',
      overrides_machine_decision: true,
    })
    renderWithRouter(<App />, '/reviews')
    await screen.findByText('TXN_SCENARIO_B_CURRENT')
    await user.click(screen.getByRole('button', { name: 'Review' }))
    await user.click(await screen.findByRole('button', { name: 'Approve' }))

    await waitFor(() =>
      expect(mockApi.resolveReview).toHaveBeenCalledWith(825, {
        resolution: 'approved',
        reason: undefined,
      }),
    )
  })
})
