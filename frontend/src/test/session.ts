/**
 * Session helpers for component tests.
 *
 * The console is behind authentication from Phase 10 onward, so a test that
 * renders `<App />` needs a session in storage or it gets the login form. The
 * default installed by `setup.ts` is an administrator, which keeps every
 * pre-existing page test asserting about the page rather than about auth.
 *
 * Tests that care about roles call `signInAs('viewer')` and get exactly the
 * permission set the backend grants that role - the lists below mirror
 * `app.core.permissions.ROLE_PERMISSIONS`, and `test_security_rbac` asserts the
 * backend side of the same table.
 */
import { Permission, type Session } from '@/lib/auth'

const VIEWER = [
  Permission.DashboardRead,
  Permission.TransactionsRead,
  Permission.MonitoringRead,
  Permission.AuditRead,
  Permission.InvestigationsRead,
  Permission.ReviewsRead,
  Permission.EventsRead,
]

const ANALYST = [
  ...VIEWER,
  Permission.InvestigationsRun,
  Permission.RiskScore,
  Permission.ReviewsResolve,
  Permission.FeedbackWrite,
  Permission.TransactionsIngest,
]

const ADMIN = [...ANALYST, Permission.SimulatorControl, Permission.SystemAdmin]

export const ROLE_PERMISSIONS: Record<string, readonly string[]> = {
  admin: ADMIN,
  risk_analyst: ANALYST,
  viewer: VIEWER,
  merchant: [],
}

export type TestRole = keyof typeof ROLE_PERMISSIONS

export function sessionFor(role: TestRole): Session {
  return {
    access_token: `test-token-${role}`,
    // An hour out, so `loadSession` does not discard it as expired.
    expires_at: new Date(Date.now() + 3_600_000).toISOString(),
    user: {
      id: 1,
      email: `${role}@test.invalid`,
      full_name: `Test ${role}`,
      role,
      is_active: true,
    },
    role,
    permissions: ROLE_PERMISSIONS[role] ?? [],
  }
}

/** Put a session in storage. Call before rendering. */
export function signInAs(role: TestRole): Session {
  const session = sessionFor(role)
  window.sessionStorage.setItem('razorshield.session', JSON.stringify(session))
  return session
}

export function signOut(): void {
  window.sessionStorage.removeItem('razorshield.session')
}
