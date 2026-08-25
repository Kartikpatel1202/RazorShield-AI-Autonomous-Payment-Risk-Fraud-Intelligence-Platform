import { useState, type FormEvent } from 'react'

import { useAuth } from '@/components/auth/context'
import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'

const SIMULATION_NOTICE =
  'Hackathon simulation - not real Razorpay infrastructure or transaction data.'

/** The wordmark, matching the application chrome. */
function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className={className}>
      <path
        d="M12 2 3.5 5.2v6.4c0 5 3.6 9.3 8.5 10.4 4.9-1.1 8.5-5.4 8.5-10.4V5.2L12 2Z"
        className="fill-brand"
      />
      <path d="M12 6.2 8 12.6h3l-1 5.2 4-6.4h-3l1-5.2Z" className="fill-brand-contrast" />
    </svg>
  )
}

const FIELD =
  'w-full rounded-lg border border-border-subtle bg-surface-raised px-3 py-2 text-sm text-content shadow-flat transition-colors placeholder:text-content-faint hover:border-border-strong focus:border-brand focus:ring-2 focus:ring-brand/25 focus:outline-none'

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
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await signIn(email, password)
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 429) {
        setError('Too many attempts. Wait a minute and try again.')
      } else if (cause instanceof ApiError) {
        setError(cause.message)
      } else {
        setError('Could not reach the server.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="grid min-h-dvh lg:grid-cols-[1.1fr_1fr]">
      {/* The brand panel. Present only where there is room for it; on a phone
          the form is the whole job and this would be a screen of scrolling
          before the reader reaches the thing they came for. */}
      <aside className="ink-grid relative hidden flex-col justify-between overflow-hidden bg-ink p-10 lg:flex">
        <span
          aria-hidden="true"
          className="absolute -top-32 -right-24 size-96 rounded-full bg-brand/20 blur-3xl"
        />
        <div className="relative flex items-center gap-3">
          <Mark className="size-8" />
          <span className="text-base font-semibold tracking-tight text-ink-content">
            RazorShield <span className="text-brand-soft">AI</span>
          </span>
        </div>

        <div className="relative max-w-md">
          <h2 className="text-2xl leading-snug font-semibold tracking-tight text-ink-content">
            Two models advise. Deterministic policy decides.
          </h2>
          <p className="mt-3 text-sm leading-relaxed text-ink-muted">
            A supervised fraud model and an independent behavioural anomaly engine score every
            payment. An AI agent investigates and cites its evidence. A versioned rule set - not a
            language model - turns all of it into one auditable decision.
          </p>
          <ul className="mt-6 flex flex-col gap-2.5">
            {[
              'Every figure traced to a database query',
              'Append-only decisions, immutable audit trail',
              'Human resolutions recorded beside the machine, never over it',
            ].map((line) => (
              <li key={line} className="flex items-start gap-2.5 text-sm text-ink-muted">
                <span aria-hidden="true" className="mt-1 text-xs text-brand-soft">
                  ▸
                </span>
                {line}
              </li>
            ))}
          </ul>
        </div>

        <p className="relative text-xs text-ink-faint">{SIMULATION_NOTICE}</p>
      </aside>

      {/* The form. */}
      <div className="flex items-center justify-center bg-surface px-4 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <Mark className="size-9" />
            <div>
              <h1 className="text-base font-semibold tracking-tight">RazorShield AI</h1>
              <p className="text-xs text-content-faint">Risk Operations</p>
            </div>
          </div>

          <div className="hidden lg:block">
            <h1 className="text-xl font-semibold tracking-tight">Sign in</h1>
            <p className="mt-1 text-sm text-content-muted">
              Use the account an administrator created for you.
            </p>
          </div>

          <form
            onSubmit={handleSubmit}
            className="mt-6 flex flex-col gap-4 rounded-xl border border-border-subtle bg-surface-raised p-6 shadow-raised"
          >
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="email"
                className="text-[0.7rem] font-semibold tracking-[0.06em] text-content-muted uppercase"
              >
                Email
              </label>
              <input
                id="email"
                name="email"
                type="email"
                autoComplete="username"
                required
                autoFocus
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className={FIELD}
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="password"
                className="text-[0.7rem] font-semibold tracking-[0.06em] text-content-muted uppercase"
              >
                Password
              </label>
              <input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className={FIELD}
              />
            </div>

            {error !== null && (
              <p
                role="alert"
                className="flex items-start gap-2 rounded-lg border border-danger/30 bg-danger-surface px-3 py-2 text-xs text-danger"
              >
                <span aria-hidden="true" className="font-bold">
                  !
                </span>
                {error}
              </p>
            )}

            <Button type="submit" disabled={submitting} className="mt-1 w-full">
              {submitting ? 'Signing in...' : 'Sign in'}
            </Button>
          </form>

          <p className="mt-6 text-center text-[0.7rem] leading-relaxed text-content-faint lg:hidden">
            {SIMULATION_NOTICE}
          </p>
        </div>
      </div>
    </div>
  )
}
