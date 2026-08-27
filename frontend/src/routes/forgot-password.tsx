import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'

import { AuthAlert, AuthField, AuthLayout } from '@/components/auth/auth-layout'
import { Button } from '@/components/ui/button'
import { ApiError, api, type ForgotPasswordResponse } from '@/lib/api'
import { looksLikeEmail } from '@/lib/password'

/**
 * Request a password reset link.
 *
 * The response is the same sentence whether or not the address is registered,
 * and this page shows it verbatim. Any attempt to be more helpful - "we could
 * not find that account", or even a different layout for the two cases - turns
 * the form into a way to find out who has an account here, which is exactly
 * what the sign-in page was built not to be.
 */
export function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  const [touched, setTouched] = useState(false)
  const [result, setResult] = useState<ForgotPasswordResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const emailError = !email.trim()
    ? 'Enter your email address.'
    : !looksLikeEmail(email)
      ? 'That does not look like an email address.'
      : undefined

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setTouched(true)
    setError(null)
    if (emailError) return

    setSubmitting(true)
    try {
      setResult(await api.forgotPassword(email.trim()))
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 429) {
        setError('Too many reset requests. Wait a minute and try again.')
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
      title="Reset your password"
      subtitle="We will send a link to the address on your account."
      footer={
        <Link to="/login" className="font-medium text-brand hover:underline">
          Back to sign in
        </Link>
      }
    >
      {result ? (
        <div className="mt-6 flex flex-col gap-4 rounded-xl border border-border-subtle bg-surface-raised p-6 shadow-raised">
          <AuthAlert tone="success">{result.detail}</AuthAlert>

          {result.dev_reset_url ? (
            <div className="flex flex-col gap-2 rounded-lg border border-warning/30 bg-warning-surface p-3">
              <p className="text-xs font-semibold text-warning">Local development</p>
              <p className="text-xs leading-relaxed text-content-muted">
                This deployment has no email integration, so the link is shown here instead of
                being sent. It expires{' '}
                {result.dev_expires_at
                  ? new Date(result.dev_expires_at).toLocaleTimeString()
                  : 'shortly'}{' '}
                and works once. The server refuses to enable this in production.
              </p>
              {/* An <a>, not a router link: the token lives in the query string
                  and a full navigation is the same thing the emailed link would
                  do, so this exercises the real path. */}
              <a
                href={result.dev_reset_url}
                className="numeric text-xs break-all text-brand hover:underline"
              >
                {result.dev_reset_url}
              </a>
            </div>
          ) : null}

          <p className="text-xs leading-relaxed text-content-faint">
            Did not get anything? Check the address and try again - and remember that this message
            appears whether or not an account exists, so it is not confirmation that one does.
          </p>

          <Button
            variant="secondary"
            className="w-full"
            onClick={() => {
              setResult(null)
              setTouched(false)
            }}
          >
            Try a different address
          </Button>
        </div>
      ) : (
        <form
          onSubmit={handleSubmit}
          noValidate
          className="mt-6 flex flex-col gap-4 rounded-xl border border-border-subtle bg-surface-raised p-6 shadow-raised"
        >
          <AuthField
            id="email"
            label="Email"
            type="email"
            autoComplete="username"
            autoFocus
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            onBlur={() => setTouched(true)}
            error={touched ? emailError : undefined}
          />

          {error ? <AuthAlert tone="error">{error}</AuthAlert> : null}

          <Button type="submit" disabled={submitting} className="mt-1 w-full">
            {submitting ? 'Sending...' : 'Send reset instructions'}
          </Button>
        </form>
      )}
    </AuthLayout>
  )
}
