/**
 * Application shell and routing.
 *
 * These assertions moved with the product: in Phase 1 `/dashboard` was a
 * placeholder and `/rules` was a 404. Both are now real pages, so the tests
 * check the shell itself - branding, navigation, routing and the genuine
 * not-found path - rather than page content, which the route tests cover.
 */
import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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
  mockApi.overview.mockResolvedValue(fixtures.overview)
  mockApi.riskDistribution.mockResolvedValue(fixtures.riskDistribution)
  mockApi.decisionAnalytics.mockResolvedValue(fixtures.decisionAnalytics)
  mockApi.trends.mockResolvedValue(fixtures.trends)
  mockApi.topRisk.mockResolvedValue(fixtures.topRisk)
  mockApi.audit.mockResolvedValue(fixtures.auditPage)
  mockApi.auditSummary.mockResolvedValue(fixtures.auditSummary)
  mockApi.policy.mockResolvedValue(fixtures.policy)
  mockApi.systemHealth.mockResolvedValue(fixtures.systemHealth)
})

describe('App', () => {
  it('renders the dashboard heading', async () => {
    renderWithRouter(<App />, '/dashboard')

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Risk Command Center' }),
    ).toBeInTheDocument()
  })

  it('carries the product name and the simulation disclaimer in the shell', () => {
    renderWithRouter(<App />, '/dashboard')

    // The wordmark is two nodes so "AI" can carry the accent colour, so
    // this matches on the composed text rather than a single element.
    expect(
      screen.getAllByText((_, element) => element?.textContent === 'RazorShield AI').length,
    ).toBeGreaterThan(0)
    expect(
      screen.getByText(/not real Razorpay infrastructure or transaction data/i),
    ).toBeInTheDocument()
  })

  it('redirects the root path to the dashboard', async () => {
    renderWithRouter(<App />, '/')

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Risk Command Center' }),
    ).toBeInTheDocument()
  })

  it('exposes primary navigation with the dashboard link', () => {
    renderWithRouter(<App />, '/dashboard')

    const nav = screen.getByRole('navigation', { name: 'Primary' })
    expect(within(nav).getByRole('link', { name: 'Dashboard' })).toHaveAttribute(
      'href',
      '/dashboard',
    )
  })

  it('routes /rules to the policy viewer, which is no longer a placeholder', async () => {
    renderWithRouter(<App />, '/rules')

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Decision policy' }),
    ).toBeInTheDocument()
  })

  it('shows a not-found page for a route that does not exist', () => {
    renderWithRouter(<App />, '/nowhere-at-all')

    expect(screen.getByRole('heading', { level: 1, name: 'Page not found' })).toBeInTheDocument()
  })

  it('keeps the Phase 1 /audit-log path working', async () => {
    renderWithRouter(<App />, '/audit-log')

    expect(await screen.findByRole('heading', { level: 1, name: 'Audit log' })).toBeInTheDocument()
  })
})
