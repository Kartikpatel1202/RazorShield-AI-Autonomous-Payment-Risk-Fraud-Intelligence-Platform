/**
 * Registration, password reset, and the paths between them.
 *
 * The rules these lock down are the ones a redesign is most likely to soften:
 * the signup form must never offer a role, the reset acknowledgement must read
 * the same whatever the address turns out to be, and a token must never be
 * rendered anywhere a screenshot would capture it.
 */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/lib/api'
import { renderWithRouter } from '@/test/render'
import { signOut } from '@/test/session'

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    api: {
      login: vi.fn().mockResolvedValue(undefined),
      session: vi.fn().mockResolvedValue(undefined),
      logout: vi.fn().mockResolvedValue(undefined),
      signup: vi.fn().mockResolvedValue(undefined),
      forgotPassword: vi.fn().mockResolvedValue(undefined),
      resetPassword: vi.fn().mockResolvedValue(undefined),
      passwordPolicy: vi.fn().mockResolvedValue(undefined),
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

const STRONG = 'harbour-lantern-42'

beforeEach(() => {
  vi.clearAllMocks()
  signOut()
})

// --------------------------------------------------------------------------
// Getting between the pages
// --------------------------------------------------------------------------
describe('the unauthenticated routes', () => {
  it('offers sign up and forgot password from the sign-in form', async () => {
    renderWithRouter(<App />, '/login')

    expect(await screen.findByRole('link', { name: 'Sign up' })).toHaveAttribute(
      'href',
      '/signup',
    )
    expect(screen.getByRole('link', { name: 'Forgot password?' })).toHaveAttribute(
      'href',
      '/forgot-password',
    )
  })

  it('renders the signup form at /signup', async () => {
    renderWithRouter(<App />, '/signup')

    expect(await screen.findByLabelText('Full name')).toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toBeInTheDocument()
    expect(screen.getByLabelText('Confirm password')).toBeInTheDocument()
  })

  it('renders the forgot-password form at /forgot-password', async () => {
    renderWithRouter(<App />, '/forgot-password')
    expect(await screen.findByLabelText('Email')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Send reset instructions' }),
    ).toBeInTheDocument()
  })

  it('falls back to sign-in for an unknown path rather than a 404', async () => {
    // For someone who is not signed in, "sign in" is the honest answer to any
    // console URL, and a 404 chrome-less page would be a dead end.
    renderWithRouter(<App />, '/dashboard')
    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('names the product and what it does', async () => {
    renderWithRouter(<App />, '/login')
    expect(
      await screen.findAllByText(/Autonomous Payment Risk & Fraud Management/i),
    ).not.toHaveLength(0)
  })
})

// --------------------------------------------------------------------------
// Signing up
// --------------------------------------------------------------------------
describe('signup', () => {
  it('never offers a role', async () => {
    // The property that matters most on this page. The API refuses a role key
    // regardless - this asserts the form does not invite one.
    renderWithRouter(<App />, '/signup')
    await screen.findByLabelText('Full name')

    expect(screen.queryByLabelText(/role/i)).not.toBeInTheDocument()
    // No select, no radio group, no option - the three ways a form offers a
    // choice. The page mentions administrators in prose ("an administrator can
    // grant more"), which is the opposite of offering the role.
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryAllByRole('radio')).toHaveLength(0)
    expect(document.querySelectorAll('select, option')).toHaveLength(0)
  })

  it('says plainly that a new account is read-only', async () => {
    renderWithRouter(<App />, '/signup')
    expect(await screen.findByText('viewer')).toBeInTheDocument()
  })

  it('submits the account and lands on sign-in with a confirmation', async () => {
    mockApi.signup.mockResolvedValue({
      status: 'created',
      detail: 'Account created successfully. Please sign in.',
      user: {
        id: 9,
        email: 'newcomer@example.com',
        full_name: 'New Comer',
        role: 'viewer',
        is_active: true,
      },
    })

    const user = userEvent.setup()
    renderWithRouter(<App />, '/signup')

    await user.type(await screen.findByLabelText('Full name'), 'New Comer')
    await user.type(screen.getByLabelText('Email'), 'newcomer@example.com')
    await user.type(screen.getByLabelText('Password'), STRONG)
    await user.type(screen.getByLabelText('Confirm password'), STRONG)
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(mockApi.signup).toHaveBeenCalledWith({
      full_name: 'New Comer',
      email: 'newcomer@example.com',
      password: STRONG,
    })
    expect(
      await screen.findByText('Account created successfully. Please sign in.'),
    ).toBeInTheDocument()
    // And on the sign-in form, with the address carried over.
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toHaveValue('newcomer@example.com')
  })

  it('refuses to submit when the passwords differ', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/signup')

    await user.type(await screen.findByLabelText('Full name'), 'New Comer')
    await user.type(screen.getByLabelText('Email'), 'newcomer@example.com')
    await user.type(screen.getByLabelText('Password'), STRONG)
    await user.type(screen.getByLabelText('Confirm password'), 'something-else-99')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('Passwords do not match.')).toBeInTheDocument()
    expect(mockApi.signup).not.toHaveBeenCalled()
  })

  it('refuses a password the server would reject anyway', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/signup')

    await user.type(await screen.findByLabelText('Full name'), 'New Comer')
    await user.type(screen.getByLabelText('Email'), 'newcomer@example.com')
    await user.type(screen.getByLabelText('Password'), 'short')
    await user.type(screen.getByLabelText('Confirm password'), 'short')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText(/at least 12 characters/i)).toBeInTheDocument()
    // The round trip is saved, but the server applies the same rule regardless.
    expect(mockApi.signup).not.toHaveBeenCalled()
  })

  it('shows the server message for a duplicate address', async () => {
    mockApi.signup.mockRejectedValue(
      new ApiError('An account with this email already exists. Try signing in instead.', 409),
    )

    const user = userEvent.setup()
    renderWithRouter(<App />, '/signup')

    await user.type(await screen.findByLabelText('Full name'), 'New Comer')
    await user.type(screen.getByLabelText('Email'), 'taken@example.com')
    await user.type(screen.getByLabelText('Password'), STRONG)
    await user.type(screen.getByLabelText('Confirm password'), STRONG)
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/already exists/i)
  })

  it('explains a rate limit rather than repeating a validation error', async () => {
    mockApi.signup.mockRejectedValue(new ApiError('Rate limit exceeded. Retry later.', 429))

    const user = userEvent.setup()
    renderWithRouter(<App />, '/signup')

    await user.type(await screen.findByLabelText('Full name'), 'New Comer')
    await user.type(screen.getByLabelText('Email'), 'flood@example.com')
    await user.type(screen.getByLabelText('Password'), STRONG)
    await user.type(screen.getByLabelText('Confirm password'), STRONG)
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/too many sign-up attempts/i)
  })

  it('never renders the typed password back into the document', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/signup')

    const password = await screen.findByLabelText('Password')
    await user.type(password, STRONG)

    expect(password).toHaveAttribute('type', 'password')
    expect(document.body.textContent).not.toContain(STRONG)
  })
})

// --------------------------------------------------------------------------
// Requesting a reset
// --------------------------------------------------------------------------
describe('forgot password', () => {
  const ACK =
    'If an account exists for this email, you will receive instructions to reset your password.'

  it('shows the conditional acknowledgement, not a claim about the address', async () => {
    mockApi.forgotPassword.mockResolvedValue({
      status: 'ok',
      detail: ACK,
      dev_reset_url: null,
      dev_expires_at: null,
    })

    const user = userEvent.setup()
    renderWithRouter(<App />, '/forgot-password')

    await user.type(await screen.findByLabelText('Email'), 'someone@example.com')
    await user.click(screen.getByRole('button', { name: 'Send reset instructions' }))

    expect(await screen.findByText(ACK)).toBeInTheDocument()
  })

  it('renders the same acknowledgement whether or not an account exists', async () => {
    // The server sends one sentence for both cases; this asserts the console
    // does not add a distinguishing detail of its own.
    mockApi.forgotPassword.mockResolvedValue({
      status: 'ok',
      detail: ACK,
      dev_reset_url: null,
      dev_expires_at: null,
    })

    const user = userEvent.setup()
    const { unmount } = renderWithRouter(<App />, '/forgot-password')
    await user.type(await screen.findByLabelText('Email'), 'known@example.com')
    await user.click(screen.getByRole('button', { name: 'Send reset instructions' }))
    const known = (await screen.findByText(ACK)).textContent
    unmount()

    renderWithRouter(<App />, '/forgot-password')
    await user.type(await screen.findByLabelText('Email'), 'unknown@example.com')
    await user.click(screen.getByRole('button', { name: 'Send reset instructions' }))
    const unknown = (await screen.findByText(ACK)).textContent

    expect(known).toEqual(unknown)
  })

  it('surfaces the development link when the backend supplies one', async () => {
    mockApi.forgotPassword.mockResolvedValue({
      status: 'ok',
      detail: ACK,
      dev_reset_url: 'http://localhost:3000/reset-password?token=abc123',
      dev_expires_at: new Date(Date.now() + 1_800_000).toISOString(),
    })

    const user = userEvent.setup()
    renderWithRouter(<App />, '/forgot-password')

    await user.type(await screen.findByLabelText('Email'), 'someone@example.com')
    await user.click(screen.getByRole('button', { name: 'Send reset instructions' }))

    expect(await screen.findByText('Local development')).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: /reset-password\?token=abc123/ }),
    ).toBeInTheDocument()
  })

  it('shows nothing about a development link when there is none', async () => {
    mockApi.forgotPassword.mockResolvedValue({
      status: 'ok',
      detail: ACK,
      dev_reset_url: null,
      dev_expires_at: null,
    })

    const user = userEvent.setup()
    renderWithRouter(<App />, '/forgot-password')

    await user.type(await screen.findByLabelText('Email'), 'someone@example.com')
    await user.click(screen.getByRole('button', { name: 'Send reset instructions' }))

    await screen.findByText(ACK)
    expect(screen.queryByText('Local development')).not.toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// Redeeming a reset
// --------------------------------------------------------------------------
describe('reset password', () => {
  it('asks for a link when the page is opened without a token', async () => {
    renderWithRouter(<App />, '/reset-password')

    expect(await screen.findByText(/needs a reset token/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Request a reset link' })).toBeInTheDocument()
  })

  it('submits the token from the query string without ever rendering it', async () => {
    mockApi.resetPassword.mockResolvedValue({ status: 'ok', detail: 'Password updated.' })

    const user = userEvent.setup()
    renderWithRouter(<App />, '/reset-password?token=secret-token-value-123')

    await user.type(await screen.findByLabelText('New password'), STRONG)
    await user.type(screen.getByLabelText('Confirm new password'), STRONG)
    await user.click(screen.getByRole('button', { name: 'Reset password' }))

    expect(mockApi.resetPassword).toHaveBeenCalledWith('secret-token-value-123', STRONG)
    // The token is a bearer credential for the life of this page. It goes in
    // the request and nowhere a screenshot would catch it.
    expect(document.body.textContent).not.toContain('secret-token-value-123')
  })

  it('confirms success and offers the way back to sign in', async () => {
    mockApi.resetPassword.mockResolvedValue({ status: 'ok', detail: 'Password updated.' })

    const user = userEvent.setup()
    renderWithRouter(<App />, '/reset-password?token=secret-token-value-123')

    await user.type(await screen.findByLabelText('New password'), STRONG)
    await user.type(screen.getByLabelText('Confirm new password'), STRONG)
    await user.click(screen.getByRole('button', { name: 'Reset password' }))

    expect(await screen.findByText('Password updated')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign in now' })).toBeInTheDocument()
  })

  it('shows one message for a spent, expired or unknown link', async () => {
    mockApi.resetPassword.mockRejectedValue(
      new ApiError(
        'This password reset link is no longer valid. Request a new one from the sign-in page.',
        400,
      ),
    )

    const user = userEvent.setup()
    renderWithRouter(<App />, '/reset-password?token=spent-token-value-123')

    await user.type(await screen.findByLabelText('New password'), STRONG)
    await user.type(screen.getByLabelText('Confirm new password'), STRONG)
    await user.click(screen.getByRole('button', { name: 'Reset password' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/no longer valid/i)
  })

  it('refuses a mismatched confirmation before calling the server', async () => {
    const user = userEvent.setup()
    renderWithRouter(<App />, '/reset-password?token=secret-token-value-123')

    await user.type(await screen.findByLabelText('New password'), STRONG)
    await user.type(screen.getByLabelText('Confirm new password'), 'not-the-same-99')
    await user.click(screen.getByRole('button', { name: 'Reset password' }))

    expect(await screen.findByText('Passwords do not match.')).toBeInTheDocument()
    expect(mockApi.resetPassword).not.toHaveBeenCalled()
  })

  it('refuses a weak new password before spending the link', async () => {
    // The server checks the policy before consuming the token for the same
    // reason: fumbling the requirement should not burn the only link.
    const user = userEvent.setup()
    renderWithRouter(<App />, '/reset-password?token=secret-token-value-123')

    await user.type(await screen.findByLabelText('New password'), 'short')
    await user.type(screen.getByLabelText('Confirm new password'), 'short')
    await user.click(screen.getByRole('button', { name: 'Reset password' }))

    expect(await screen.findByText(/at least 12 characters/i)).toBeInTheDocument()
    expect(mockApi.resetPassword).not.toHaveBeenCalled()
  })
})

// --------------------------------------------------------------------------
// The signed-in case
// --------------------------------------------------------------------------
describe('when already signed in', () => {
  it('sends an auth URL to the dashboard instead of a form', async () => {
    const { signInAs } = await import('@/test/session')
    signInAs('admin')
    // Every dashboard query is already defaulted to a resolved promise in the
    // mock above; the panels stay in their loading state, which is all this
    // test needs from them.
    renderWithRouter(<App />, '/signup')

    await waitFor(() => {
      expect(screen.queryByLabelText('Full name')).not.toBeInTheDocument()
    })
    expect(screen.getByRole('navigation', { name: 'Primary' })).toBeInTheDocument()
  })
})
