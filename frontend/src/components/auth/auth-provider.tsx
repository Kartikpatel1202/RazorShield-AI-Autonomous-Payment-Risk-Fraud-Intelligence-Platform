import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import { AuthContext, type AuthState } from '@/components/auth/context'
import { api, onUnauthorized } from '@/lib/api'
import {
  clearSession,
  hasPermission,
  loadSession,
  saveSession,
  type PermissionName,
  type Session,
} from '@/lib/auth'

/**
 * Holds the console's session.
 *
 * Restoring from storage on first render rather than fetching `/auth/me` first:
 * a page reload should land on the dashboard, not flash a login form while a
 * round trip decides. The token is verified server-side on the very next
 * request anyway, and a 401 from any of them ends the session here.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => loadSession())

  // A 401 from any request means the credential is no longer good - expired, or
  // the account was deactivated while the tab was open. Dropping the session
  // here turns that into one redirect to the login form instead of an error
  // banner on every panel that happened to be loading.
  useEffect(() => onUnauthorized(() => setSession(null)), [])

  const signIn = useCallback(async (email: string, password: string) => {
    const response = await api.login(email, password)
    const next: Session = {
      access_token: response.access_token,
      expires_at: response.expires_at,
      user: response.user,
      role: response.role,
      permissions: response.permissions,
    }
    saveSession(next)
    setSession(next)
  }, [])

  const signOut = useCallback(async () => {
    try {
      // Best-effort: the endpoint records the event. It cannot revoke the token
      // - a JWT is self-contained - so the meaningful part of signing out is
      // discarding it here, which happens either way.
      await api.logout()
    } catch {
      /* already unauthenticated, or the server is unreachable */
    }
    clearSession()
    setSession(null)
  }, [])

  const value = useMemo<AuthState>(
    () => ({
      session,
      signedIn: session !== null,
      signIn,
      signOut,
      can: (permission: PermissionName) => hasPermission(session, permission),
    }),
    [session, signIn, signOut],
  )

  return <AuthContext value={value}>{children}</AuthContext>
}
