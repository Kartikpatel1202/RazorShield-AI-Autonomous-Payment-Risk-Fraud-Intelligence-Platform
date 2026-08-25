/**
 * Console authentication: the login gate, role-aware navigation, and what
 * happens when a token stops working.
 *
 * A standing caveat these tests deliberately do not contradict: none of this is
 * a security control. Hiding a nav link and refusing a route are conveniences;
 * the server checks every request independently. What is worth asserting here
 * is that the console does not *lie* - it must not show an analyst a button
 * that will 403, and it must not show a signed-out visitor a page frame full of
 * failing panels instead of a login form.
 */
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/lib/api'
import { loadSession } from '@/lib/auth'
import * as fixtures from '@/test/fixtures'
import { renderWithRouter } from '@/test/render'
import { signInAs, signOut } from '@/test/session'

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

/** Every call the console makes on load, so no panel throws while a test is
    asserting about the chrome around it. */
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
// The gate
// --------------------------------------------------------------------------
describe('signed out', () => {
  beforeEach(() => {
    signOut()
  })

  it('shows the login form instead of the console', async () => {
    renderWithRouter(<App />, '/dashboard')

    expect(await screen.findByLabelText('Email')).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Primary' })).not.toBeInTheDocument()
  })

  it('shows the login form on every route, not just the root', async () => {
    // One branch rather than a per-route redirect, so there is no URL that
    // renders console chrome before its data requests fail.
    for (const path of ['/', '/transactions', '/audit', '/monitoring', '/nonsense']) {
      const { unmount } = renderWithRouter(<App />, path)
      expect(await screen.findByLabelText('Email')).toBeInTheDocument()
      unmount()
    }
  })

  it('requests no data before anyone has signed in', async () => {
    renderWithRouter(<App />, '/dashboard')
    await screen.findByLabelText('Email')

    expect(mockApi.overview).not.toHaveBeenCalled()
    expect(mockApi.systemHealth).not.toHaveBeenCalled()
  })

  it('signs in and reveals the console', async () => {
    mockApi.login.mockResolvedValue({
      access_token: 'issued-token',
      token_type: 'bearer',
      expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      user: {
        id: 4,
        email: 'analyst@example.com',
        full_name: 'Priya Analyst',
        role: 'risk_analyst',
        is_active: true,
      },
      role: 'risk_analyst',
      permissions: ['dashboard:read', 'transactions:read', 'reviews:read', 'reviews:resolve'],
    })

    const user = userEvent.setup()
    renderWithRouter(<App />, '/dashboard')

    await user.type(await screen.findByLabelText('Email'), 'analyst@example.com')
    await user.type(screen.getByLabelText('Password'), 'a-real-password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('navigation', { name: 'Primary' })).toBeInTheDocument()
    expect(mockApi.login).toHaveBeenCalledWith('analyst@example.com', 'a-real-password')
  })

  it('shows the server message on a rejected credential, unchanged', async () => {
    // Unchanged on purpose. The API returns one message for every failure -
    // unknown address, wrong password, disabled account - so that this form
    // cannot be used to find out who has an account. A friendlier, more
    // specific client-side message would undo that in a single line.
    mockApi.login.mockRejectedValue(new ApiError('Invalid email or password', 401))

    const user = userEvent.setup()
    renderWithRouter(<App />, '/dashboard')

    await user.type(await screen.findByLabelText('Email'), 'someone@example.com')
    await user.type(screen.getByLabelText('Password'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password')
    expect(screen.getByLabelText('Email')).toBeInTheDocument()
  })

  it('explains a rate-limited login rather than repeating "invalid"', async () => {
    mockApi.login.mockRejectedValue(new ApiError('Rate limit exceeded. Retry later.', 429))

    const user = userEvent.setup()
    renderWithRouter(<App />, '/dashboard')

    await user.type(await screen.findByLabelText('Email'), 'someone@example.com')
    await user.type(screen.getByLabelText('Password'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/too many attempts/i)
  })

  it('stores no credential when the login fails', async () => {
    mockApi.login.mockRejectedValue(new ApiError('Invalid email or password', 401))

    const user = userEvent.setup()
    renderWithRouter(<App />, '/dashboard')

    await user.type(await screen.findByLabelText('Email'), 'someone@example.com')
    await user.type(screen.getByLabelText('Password'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    await screen.findByRole('alert')
    expect(loadSession()).toBeNull()
  })

  it('never renders the typed password back into the document', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/dashboard')

    const password = screen.getByLabelText('Password')
    await user.type(password, 'super-secret-value')

    expect(password).toHaveAttribute('type', 'password')
    // Not in any rendered text: an "unmask" affordance or a debug echo would
    // put the credential on screen and into a screenshot.
    expect(document.body.textContent).not.toContain('super-secret-value')
  })
})

// --------------------------------------------------------------------------
// Role-aware chrome
// --------------------------------------------------------------------------
describe('navigation by role', () => {
  it('shows an administrator everything', async () => {
    signInAs('admin')
    renderWithRouter(<App />, '/dashboard')

    const nav = await screen.findByRole('navigation', { name: 'Primary' })
    for (const label of ['Dashboard', 'Live', 'Transactions', 'Reviews', 'Monitoring', 'Audit Log']) {
      expect(within(nav).getByRole('link', { name: label })).toBeInTheDocument()
    }
  })

  it('shows a viewer the read-only pages', async () => {
    signInAs('viewer')
    renderWithRouter(<App />, '/dashboard')

    const nav = await screen.findByRole('navigation', { name: 'Primary' })
    expect(within(nav).getByRole('link', { name: 'Dashboard' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Audit Log' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Monitoring' })).toBeInTheDocument()
  })

  it('hides nothing from a viewer that a viewer may in fact read', async () => {
    // A viewer holds every read permission the console's pages need, so the
    // sidebar is identical to an administrator's. That is the correct outcome
    // and worth pinning: the difference between the roles is what they can
    // *do*, which lives on the pages, not in the navigation.
    signInAs('viewer')
    renderWithRouter(<App />, '/dashboard')
    const viewerLinks = within(await screen.findByRole('navigation', { name: 'Primary' }))
      .getAllByRole('link')
      .map((link) => link.textContent)

    signOut()
    signInAs('admin')
    const { container } = renderWithRouter(<App />, '/dashboard')
    const adminNav = within(container).getAllByRole('navigation')[0]
    const adminLinks = within(adminNav!)
      .getAllByRole('link')
      .map((link) => link.textContent)

    expect(viewerLinks).toEqual(adminLinks)
  })

  it('shows a merchant account no navigation at all', async () => {
    // A merchant is a party described by this platform, not an operator of it.
    // The account authenticates and then holds no console permission.
    signInAs('merchant')
    renderWithRouter(<App />, '/dashboard')

    const nav = await screen.findByRole('navigation', { name: 'Primary' })
    expect(within(nav).queryAllByRole('link')).toHaveLength(0)
  })

  it('refuses a route the role cannot use, with a message rather than a broken page', async () => {
    signInAs('merchant')
    renderWithRouter(<App />, '/audit')

    expect(await screen.findByText('Not available for your role')).toBeInTheDocument()
    expect(screen.getByText(/audit:read/)).toBeInTheDocument()
    // And nothing was requested on that page's behalf.
    expect(mockApi.audit).not.toHaveBeenCalled()
  })

  it('names the signed-in operator and their role', async () => {
    signInAs('risk_analyst')
    renderWithRouter(<App />, '/dashboard')

    expect(await screen.findByText('Test risk_analyst')).toBeInTheDocument()
    // `risk_analyst` is the stored value; "Analyst" is what a person reads.
    expect(screen.getByText('Analyst')).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// Ending a session
// --------------------------------------------------------------------------
describe('signing out', () => {
  it('clears the stored session and returns to the login form', async () => {
    signInAs('admin')
    mockApi.logout.mockResolvedValue({ status: 'ok', detail: 'Discard the access token.' })

    const user = userEvent.setup()
    renderWithRouter(<App />, '/dashboard')

    // Sign out lives in the account menu now, so it takes two steps: open the
    // menu, then choose it.
    await user.click(await screen.findByRole('button', { name: /Test admin/i }))
    await user.click(await screen.findByRole('menuitem', { name: 'Sign out' }))

    expect(await screen.findByLabelText('Email')).toBeInTheDocument()
    expect(loadSession()).toBeNull()
  })

  it('clears the session even when the server cannot be reached', async () => {
    // The meaningful half of signing out is discarding the token locally. If
    // that depended on a successful round trip, a network failure would leave a
    // working credential in storage on a shared machine.
    signInAs('admin')
    mockApi.logout.mockRejectedValue(new Error('network down'))

    const user = userEvent.setup()
    renderWithRouter(<App />, '/dashboard')

    // Sign out lives in the account menu now, so it takes two steps: open the
    // menu, then choose it.
    await user.click(await screen.findByRole('button', { name: /Test admin/i }))
    await user.click(await screen.findByRole('menuitem', { name: 'Sign out' }))

    expect(await screen.findByLabelText('Email')).toBeInTheDocument()
    expect(loadSession()).toBeNull()
  })
})

// --------------------------------------------------------------------------
// Expiry
// --------------------------------------------------------------------------
describe('expired credentials', () => {
  it('treats an already-expired stored token as signed out', async () => {
    window.sessionStorage.setItem(
      'razorshield.session',
      JSON.stringify({
        access_token: 'stale',
        expires_at: new Date(Date.now() - 1000).toISOString(),
        user: { id: 1, email: 'a@b.example', full_name: null, role: 'admin', is_active: true },
        role: 'admin',
        permissions: ['dashboard:read'],
      }),
    )

    renderWithRouter(<App />, '/dashboard')

    expect(await screen.findByLabelText('Email')).toBeInTheDocument()
    // Dropped on read, so the next request does not carry a token that cannot
    // work.
    expect(window.sessionStorage.getItem('razorshield.session')).toBeNull()
  })

  it('ignores a corrupted stored session rather than crashing at first render', async () => {
    window.sessionStorage.setItem('razorshield.session', '{not json')
    renderWithRouter(<App />, '/dashboard')
    expect(await screen.findByLabelText('Email')).toBeInTheDocument()
  })

  it('ignores a stored value of the wrong shape', async () => {
    window.sessionStorage.setItem('razorshield.session', JSON.stringify({ hello: 'world' }))
    renderWithRouter(<App />, '/dashboard')
    expect(await screen.findByLabelText('Email')).toBeInTheDocument()
  })

})
