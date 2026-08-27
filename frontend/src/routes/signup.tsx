import { useMemo, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { AuthAlert, AuthField, AuthLayout } from '@/components/auth/auth-layout'
import { Button } from '@/components/ui/button'
import { ApiError, api } from '@/lib/api'
import {
  MIN_PASSWORD_LENGTH,
  looksLikeEmail,
  passwordProblem,
  passwordStrength,
} from '@/lib/password'
import { cn } from '@/lib/utils'

const METER_TONE = {
  danger: 'bg-danger',
  warning: 'bg-warning',
  positive: 'bg-positive',
} as const

const METER_TEXT = {
  danger: 'text-danger',
  warning: 'text-warning',
  positive: 'text-positive',
} as const

/** A four-segment strength reading, shown once the user starts typing. */
function StrengthMeter({ password }: { password: string }) {
  const strength = passwordStrength(password)
  if (!password) return null

  return (
    <div className="flex flex-col gap-1">
      <div className="flex gap-1" aria-hidden="true">
        {[0, 1, 2, 3].map((index) => (
          <span
            key={index}
            className={cn(
              'h-1 flex-1 rounded-full transition-colors',
              index < strength.score ? METER_TONE[strength.tone] : 'bg-surface-sunken',
            )}
          />
        ))}
      </div>
      <p className={cn('text-xs font-medium', METER_TEXT[strength.tone])}>
        {strength.label}
        <span className="ml-1 font-normal text-content-faint">
          - length matters more than symbols
        </span>
      </p>
    </div>
  )
}

/**
 * Self-service registration.
 *
 * Every account created here is a **viewer**. There is no role control on this
 * form, and there is none on the request either - the API schema has no role
 * field and rejects unknown keys, so a crafted request cannot add one. Anything
 * beyond read access is granted by an administrator.
 *
 * Validation surfaces on blur rather than on every keystroke: a message that
 * appears while someone is halfway through typing a password tells them they
 * have failed at a task they have not finished.
 */
export function SignupPage() {
  const navigate = useNavigate()

  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [touched, setTouched] = useState<Record<string, boolean>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const errors = useMemo(() => {
    const found: Record<string, string | undefined> = {}
    if (!fullName.trim()) found.fullName = 'Enter your name.'
    if (!email.trim()) found.email = 'Enter your email address.'
    else if (!looksLikeEmail(email)) found.email = 'That does not look like an email address.'
    const problem = passwordProblem(password, email)
    if (problem) found.password = problem
    if (!confirm) found.confirm = 'Re-enter your password.'
    else if (confirm !== password) found.confirm = 'Passwords do not match.'
    return found
  }, [fullName, email, password, confirm])

  const complete = Object.values(errors).every((value) => value === undefined)

  function markTouched(field: string) {
    setTouched((current) => ({ ...current, [field]: true }))
  }

  /** Shown only once the field has been visited, or after a submit attempt. */
  function errorFor(field: string): string | undefined {
    return touched[field] ? errors[field] : undefined
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setTouched({ fullName: true, email: true, password: true, confirm: true })
    setFormError(null)
    if (!complete) return

    setSubmitting(true)
    try {
      await api.signup({ full_name: fullName.trim(), email: email.trim(), password })
      // The account now exists but no session does - registration and
      // authentication are separate steps. The banner rides on navigation
      // state so a reload does not replay it.
      navigate('/login', {
        replace: true,
        state: { notice: 'Account created successfully. Please sign in.', email: email.trim() },
      })
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 429) {
        setFormError('Too many sign-up attempts. Wait a minute and try again.')
      } else if (cause instanceof ApiError) {
        // 409 duplicate and 422 policy both carry a message written for a
        // person, and neither says anything about another account.
        setFormError(cause.message)
      } else {
        setFormError('Could not reach the server. Check your connection and try again.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthLayout
      title="Create your account"
      subtitle="Read-only access to the risk console. An administrator can grant more."
      footer={
        <span className="text-content-muted">
          Already have an account?{' '}
          <Link to="/login" className="font-medium text-brand hover:underline">
            Sign in
          </Link>
        </span>
      }
    >
      <form
        onSubmit={handleSubmit}
        noValidate
        className="mt-6 flex flex-col gap-4 rounded-xl border border-border-subtle bg-surface-raised p-6 shadow-raised"
      >
        <AuthField
          id="full-name"
          label="Full name"
          autoComplete="name"
          autoFocus
          value={fullName}
          onChange={(event) => setFullName(event.target.value)}
          onBlur={() => markTouched('fullName')}
          error={errorFor('fullName')}
        />

        <AuthField
          id="email"
          label="Email"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          onBlur={() => markTouched('email')}
          error={errorFor('email')}
        />

        <div className="flex flex-col gap-2">
          <AuthField
            id="password"
            label="Password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            onBlur={() => markTouched('password')}
            error={errorFor('password')}
            hint={'At least ' + MIN_PASSWORD_LENGTH + ' characters'}
          />
          <StrengthMeter password={password} />
        </div>

        <AuthField
          id="confirm-password"
          label="Confirm password"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
          onBlur={() => markTouched('confirm')}
          error={errorFor('confirm')}
        />

        {formError ? <AuthAlert tone="error">{formError}</AuthAlert> : null}

        <Button type="submit" disabled={submitting} className="mt-1 w-full">
          {submitting ? 'Creating account...' : 'Create account'}
        </Button>

        <p className="text-center text-[0.7rem] leading-relaxed text-content-faint">
          New accounts get <strong className="font-semibold text-content-muted">viewer</strong>{' '}
          access: read the dashboard, transactions, monitoring and audit trail. Resolving reviews
          and running the simulator need a role an administrator assigns.
        </p>
      </form>
    </AuthLayout>
  )
}
