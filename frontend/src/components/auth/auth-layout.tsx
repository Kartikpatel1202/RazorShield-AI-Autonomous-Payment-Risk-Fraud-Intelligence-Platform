import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

const SIMULATION_NOTICE =
  'Real-Time Risk Intelligence - not real Razorpay infrastructure or transaction data.'

/** The wordmark, matching the application chrome. */
export function Mark({ className }: { className?: string }) {
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

/** The input styling every field on these pages shares. */
export const AUTH_FIELD =
  'w-full rounded-lg border border-border-subtle bg-surface-raised px-3 py-2 text-sm text-content shadow-flat transition-colors placeholder:text-content-faint hover:border-border-strong focus:border-brand focus:ring-2 focus:ring-brand/25 focus:outline-none'

const PILLARS = [
  'Every figure traced to a database query',
  'Append-only decisions, immutable audit trail',
  'Human resolutions recorded beside the machine, never over it',
] as const

/**
 * The frame every unauthenticated page shares.
 *
 * One component rather than four copies of the split screen, because the brand
 * panel is the part most likely to drift: a product whose sign-in and sign-up
 * pages disagree about what it does reads as two products.
 *
 * The panel is hidden below `lg`. On a phone the form is the entire job, and a
 * screen of manifesto before the reader reaches the thing they came for is a
 * cost with no benefit.
 */
export function AuthLayout({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string
  subtitle?: ReactNode | undefined
  children: ReactNode
  footer?: ReactNode | undefined
}) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-[1.1fr_1fr]">
      <aside className="ink-grid relative hidden flex-col justify-between overflow-hidden bg-ink p-10 lg:flex">
        <span
          aria-hidden="true"
          className="absolute -top-32 -right-24 size-96 rounded-full bg-brand/20 blur-3xl"
        />
        <Link to="/login" className="relative flex items-center gap-3">
          <Mark className="size-8" />
          <span className="leading-tight">
            <span className="block text-base font-semibold tracking-tight text-ink-content">
              RazorShield <span className="text-brand-soft">AI</span>
            </span>
            <span className="block text-[0.65rem] tracking-[0.14em] text-ink-faint uppercase">
              Autonomous Payment Risk &amp; Fraud Management
            </span>
          </span>
        </Link>

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
            {PILLARS.map((line) => (
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

      <div className="flex items-center justify-center bg-surface px-4 py-12">
        <div className="w-full max-w-sm">
          {/* The compact wordmark, for the widths where the panel is absent. */}
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <Mark className="size-9" />
            <div>
              <p className="text-base font-semibold tracking-tight">RazorShield AI</p>
              <p className="text-xs text-content-faint">
                Autonomous Payment Risk &amp; Fraud Management
              </p>
            </div>
          </div>

          {/* One heading, sized responsively - not two copies behind
              `lg:hidden`. Duplicating it would render the page title twice in
              the accessibility tree, so every screen reader would announce it
              twice and every `getByText` would find two of it. */}
          <div>
            <h1 className="text-lg font-semibold tracking-tight lg:text-xl">{title}</h1>
            {subtitle ? <p className="mt-1 text-sm text-content-muted">{subtitle}</p> : null}
          </div>

          {children}

          {footer ? <div className="mt-6 text-center text-sm">{footer}</div> : null}

          <p className="mt-6 text-center text-[0.7rem] leading-relaxed text-content-faint lg:hidden">
            {SIMULATION_NOTICE}
          </p>
        </div>
      </div>
    </div>
  )
}

/** A labelled input. Owns its `htmlFor`, so the label is clickable and announced. */
export function AuthField({
  id,
  label,
  hint,
  error,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & {
  id: string
  label: string
  hint?: string | undefined
  error?: string | undefined
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={id}
        className="text-[0.7rem] font-semibold tracking-[0.06em] text-content-muted uppercase"
      >
        {label}
      </label>
      <input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        className={AUTH_FIELD}
        {...props}
      />
      {error ? (
        <p id={`${id}-error`} className="text-xs text-danger">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-xs text-content-faint">
          {hint}
        </p>
      ) : null}
    </div>
  )
}

/** A form-level message. `role="alert"` so it is announced when it appears. */
export function AuthAlert({
  tone,
  children,
}: {
  tone: 'error' | 'success' | 'info'
  children: ReactNode
}) {
  const classes =
    tone === 'error'
      ? 'border-danger/30 bg-danger-surface text-danger'
      : tone === 'success'
        ? 'border-positive/30 bg-positive-surface text-positive'
        : 'border-brand/30 bg-brand/5 text-brand'
  const glyph = tone === 'error' ? '!' : tone === 'success' ? '✓' : 'i'

  return (
    <p
      role={tone === 'error' ? 'alert' : 'status'}
      className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-xs leading-relaxed ${classes}`}
    >
      <span aria-hidden="true" className="font-bold">
        {glyph}
      </span>
      <span>{children}</span>
    </p>
  )
}
