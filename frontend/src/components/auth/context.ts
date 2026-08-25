/**
 * The auth context and the hook that reads it.
 *
 * Separate from `auth-provider.tsx` so that file exports a component and
 * nothing else - React Fast Refresh only re-renders a module whose exports are
 * all components, and mixing a hook in silently degrades the dev experience
 * into full page reloads.
 */
import { createContext, use } from 'react'

import type { PermissionName, Session } from '@/lib/auth'

export interface AuthState {
  readonly session: Session | null
  readonly signedIn: boolean
  readonly signIn: (email: string, password: string) => Promise<void>
  readonly signOut: () => Promise<void>
  /**
   * Whether the signed-in role holds a permission.
   *
   * A *usability* affordance and nothing more: it decides whether to render a
   * control. The server decides whether the request behind that control is
   * allowed, and re-checks on every call. A user who edits `sessionStorage` to
   * grant themselves `simulator:control` gets a visible button and a 403.
   */
  readonly can: (permission: PermissionName) => boolean
}

export const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const context = use(AuthContext)
  if (context === null) {
    throw new Error('useAuth must be used inside <AuthProvider>')
  }
  return context
}
