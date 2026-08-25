/**
 * Client-side session storage and the permission vocabulary.
 *
 * ## Where the token lives, and why
 *
 * `sessionStorage`, not `localStorage` and not a cookie.
 *
 * - Against `localStorage`: a token there outlives the tab and the browser
 *   restart, so an analyst who closes the console on a shared workstation
 *   leaves a working credential behind. `sessionStorage` is scoped to the tab
 *   and cleared when it closes, which matches how long a shift-based operations
 *   console should stay signed in.
 * - Against a cookie: a cookie is attached to every request the browser makes
 *   to this origin, which is what makes CSRF possible. A bearer token read from
 *   storage by our own code and set on our own `fetch` calls is never sent by
 *   the browser on its own, so a hostile page cannot ride on it.
 *
 * Both storages are readable by any script on the origin, so neither survives
 * XSS. That is why the token is short-lived, why the account is re-checked on
 * every request server-side, and why the console ships a strict CSP. The
 * honest summary is in `docs/security.md`.
 */

const STORAGE_KEY = 'razorshield.session'

export interface UserProfile {
  readonly id: number
  readonly email: string
  readonly full_name: string | null
  readonly role: string
  readonly is_active: boolean
}

export interface Session {
  readonly access_token: string
  readonly expires_at: string
  readonly user: UserProfile
  readonly role: string
  readonly permissions: readonly string[]
}

/** Permission strings the console checks. Mirrors `app.core.permissions`. */
export const Permission = {
  DashboardRead: 'dashboard:read',
  TransactionsRead: 'transactions:read',
  MonitoringRead: 'monitoring:read',
  AuditRead: 'audit:read',
  InvestigationsRead: 'investigations:read',
  ReviewsRead: 'reviews:read',
  EventsRead: 'events:read',
  InvestigationsRun: 'investigations:run',
  RiskScore: 'risk:score',
  ReviewsResolve: 'reviews:resolve',
  FeedbackWrite: 'feedback:write',
  TransactionsIngest: 'transactions:ingest',
  SimulatorControl: 'simulator:control',
  SystemAdmin: 'system:admin',
} as const

export type PermissionName = (typeof Permission)[keyof typeof Permission]

function isSession(value: unknown): value is Session {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Partial<Session>
  return (
    typeof candidate.access_token === 'string' &&
    typeof candidate.expires_at === 'string' &&
    typeof candidate.role === 'string' &&
    Array.isArray(candidate.permissions) &&
    typeof candidate.user === 'object' &&
    candidate.user !== null
  )
}

export function loadSession(): Session | null {
  let raw: string | null = null
  try {
    raw = window.sessionStorage.getItem(STORAGE_KEY)
  } catch {
    // Storage can be unavailable (private mode, blocked cookies). Treat that as
    // "not signed in" rather than crashing the app at its first render.
    return null
  }
  if (!raw) return null

  try {
    const parsed: unknown = JSON.parse(raw)
    if (!isSession(parsed)) return null
    // A token whose expiry has already passed is not worth sending; drop it
    // here so the first request after a long idle is a clean redirect to the
    // login form rather than a 401 the user has to see.
    if (Date.parse(parsed.expires_at) <= Date.now()) {
      clearSession()
      return null
    }
    return parsed
  } catch {
    return null
  }
}

export function saveSession(session: Session): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session))
  } catch {
    /* Storage unavailable: the session stays in memory for this page only. */
  }
}

export function clearSession(): void {
  try {
    window.sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    /* nothing to clean up */
  }
}

export function hasPermission(session: Session | null, permission: PermissionName): boolean {
  return session?.permissions.includes(permission) ?? false
}

/**
 * A human-readable name for a role.
 *
 * `risk_analyst` is the analyst role; it predates the console and was not
 * renamed, so the label is applied here rather than in the database.
 */
export function roleLabel(role: string): string {
  switch (role) {
    case 'admin':
      return 'Administrator'
    case 'risk_analyst':
      return 'Analyst'
    case 'viewer':
      return 'Viewer'
    case 'merchant':
      return 'Merchant'
    default:
      return role
  }
}
