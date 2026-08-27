import { useEffect, useState, type FormEvent } from 'react'
import { Link, useLocation } from 'react-router-dom'

import { AuthAlert, AuthField, AuthLayout } from '@/components/auth/auth-layout'
import { useAuth } from '@/components/auth/context'
import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'

/** What signup and reset-password hand over when they redirect here. */
interface LoginNotice {
  readonly notice?: string
  readonly email?: string
}

/**
 * The sign-in form.
 *
 * The error message is deliberately the server's, unchanged: the API returns
 * one message for every failure - unknown address, wrong password, disabled
 * account - so that this page cannot be used to find out who has an account
 * here. Adding a friendlier, more specific message on the client would undo
 * that in a single line.
 */
export function LoginPage() {
  const { signIn } = useAuth()
  const location = useLocation()
  const handoff = (location.state ?? null) as LoginNotice | null

  const [email, setEmail] = useState(handoff?.email ?? '')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(handoff?.notice ?? null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!handoff?.notice) return
    // Clear the state so a refresh does not re-announce "account created". The
    // banner has been read into component state already.
    window.history.replaceState({}, '')
  }, [handoff?.notice])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setNotice(null)
    setSubmitting(true)
    try {
      await signIn(email, password)
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 429) {
        setError('Too many attempts. Wait a minute and try again.')
      } else if (cause instanceof ApiError) {
        setError(cause.message)
      } else {
        setError('Could not reach the server. Check your connection and try again.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthLayout
      title="Sign in"
      subtitle="Access the risk operations console."
      footer={
        <span className="text-content-muted">
          Do not have an account?{' '}
          <Link to="/signup" className="font-medium text-brand hover:underline">
            Sign up
          </Link>
        </span>
      }
    >
      <form
        onSubmit={handleSubmit}
        className="mt-6 flex flex-col gap-4 rounded-xl border border-border-subtle bg-surface-raised p-6 shadow-raised"
      >
        {notice ? <AuthAlert tone="success">{notice}</AuthAlert> : null}

        <AuthField
          id="email"
          label="Email"
          type="email"
          autoComplete="username"
          required
          autoFocus={!handoff?.email}
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />

        <div className="flex flex-col gap-1.5">
          <div className="flex items-baseline justify-between gap-2">
            <label
              htmlFor="password"
              className="text-[0.7rem] font-semibold tracking-[0.06em] text-content-muted uppercase"
            >
              Password
            </label>
            <Link
              to="/forgot-password"
              className="text-xs font-medium text-brand hover:underline"
            >
              Forgot password?
            </Link>
          </div>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            autoFocus={Boolean(handoff?.email)}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="w-full rounded-lg border border-border-subtle bg-surface-raised px-3 py-2 text-sm text-content shadow-flat transition-colors placeholder:text-content-faint hover:border-border-strong focus:border-brand focus:ring-2 focus:ring-brand/25 focus:outline-none"
          />
        </div>

        {error !== null ? <AuthAlert tone="error">{error}</AuthAlert> : null}

        <Button type="submit" disabled={submitting} className="mt-1 w-full">
          {submitting ? 'Signing in...' : 'Sign in'}
        </Button>
      </form>
    </AuthLayout>
  )
}
