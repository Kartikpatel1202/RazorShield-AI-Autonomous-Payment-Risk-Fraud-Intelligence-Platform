import { useMemo, useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { AuthAlert, AuthField, AuthLayout } from '@/components/auth/auth-layout'
import { Button } from '@/components/ui/button'
import { ApiError, api } from '@/lib/api'
import { MIN_PASSWORD_LENGTH, passwordProblem } from '@/lib/password'

/**
 * Redeem a reset link and choose a new password.
 *
 * The token comes from the query string and is never rendered, logged or put
 * in any element a screenshot would capture - it is a bearer credential for the
 * duration of this page, and the only thing that should ever happen to it is
 * being posted back.
 *
 * A rejected token produces one message covering unknown, expired and
 * already-used, because the server answers the same way for all three. Telling
 * someone their link has *expired* would confirm it was real.
 */
export function ResetPasswordPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const token = params.get('token') ?? ''

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [touched, setTouched] = useState<Record<string, boolean>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [done, setDone] = useState(false)

  const errors = useMemo(() => {
    const found: Record<string, string | undefined> = {}
    const problem = passwordProblem(password)
    if (problem) found.password = problem
    if (!confirm) found.confirm = 'Re-enter your new password.'
    else if (confirm !== password) found.confirm = 'Passwords do not match.'
    return found
  }, [password, confirm])

  const complete = Object.values(errors).every((value) => value === undefined)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setTouched({ password: true, confirm: true })
    setFormError(null)
    if (!complete) return

    setSubmitting(true)
    try {
      await api.resetPassword(token, password)
      setDone(true)
      // A beat on the success state, then the sign-in page - landing straight
      // on a form gives no confirmation that anything happened.
      window.setTimeout(() => {
        navigate('/login', {
          replace: true,
          state: { notice: 'Password updated. Sign in with your new password.' },
        })
      }, 1600)
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 429) {
        setFormError('Too many attempts. Wait a minute and try again.')
      } else if (cause instanceof ApiError) {
        setFormError(cause.message)
      } else {
        setFormError('Could not reach the server. Check your connection and try again.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  // No token at all: the page was opened directly rather than through a link.
  // Say so here instead of posting an empty token and rendering the server's
  // rejection, which would read as though the link had been tried and failed.
  if (!token) {
    return (
      <AuthLayout
        title="Reset link needed"
        subtitle="This page is opened from the link in your reset email."
        footer={
          <Link to="/login" className="font-medium text-brand hover:underline">
            Back to sign in
          </Link>
        }
      >
        <div className="mt-6 flex flex-col gap-4 rounded-xl border border-border-subtle bg-surface-raised p-6 shadow-raised">
          <AuthAlert tone="error">
            This page needs a reset token, and the address you opened has none.
          </AuthAlert>
          <Link to="/forgot-password">
            <Button className="w-full">Request a reset link</Button>
          </Link>
        </div>
      </AuthLayout>
    )
  }

  if (done) {
    return (
      <AuthLayout title="Password updated" subtitle="Taking you to the sign-in page.">
        <div className="mt-6 flex flex-col gap-4 rounded-xl border border-border-subtle bg-surface-raised p-6 shadow-raised">
          <AuthAlert tone="success">
            Your password has been changed and this link has been used up. Any other reset links
            for your account have been invalidated.
          </AuthAlert>
          <Link to="/login">
            <Button className="w-full">Sign in now</Button>
          </Link>
        </div>
      </AuthLayout>
    )
  }

  return (
    <AuthLayout
      title="Choose a new password"
      subtitle="This link works once and expires shortly."
      footer={
        <Link to="/login" className="font-medium text-brand hover:underline">
          Back to sign in
        </Link>
      }
    >
      <form
        onSubmit={handleSubmit}
        noValidate
        className="mt-6 flex flex-col gap-4 rounded-xl border border-border-subtle bg-surface-raised p-6 shadow-raised"
      >
        <AuthField
          id="new-password"
          label="New password"
          type="password"
          autoComplete="new-password"
          autoFocus
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          onBlur={() => setTouched((current) => ({ ...current, password: true }))}
          error={touched.password ? errors.password : undefined}
          hint={'At least ' + MIN_PASSWORD_LENGTH + ' characters'}
        />

        <AuthField
          id="confirm-password"
          label="Confirm new password"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
          onBlur={() => setTouched((current) => ({ ...current, confirm: true }))}
          error={touched.confirm ? errors.confirm : undefined}
        />

        {formError ? <AuthAlert tone="error">{formError}</AuthAlert> : null}

        <Button type="submit" disabled={submitting} className="mt-1 w-full">
          {submitting ? 'Updating...' : 'Reset password'}
        </Button>
      </form>
    </AuthLayout>
  )
}
