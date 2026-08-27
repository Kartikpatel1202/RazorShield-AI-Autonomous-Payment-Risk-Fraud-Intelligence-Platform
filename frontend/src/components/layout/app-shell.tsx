import { useEffect, useRef, useState, type ReactNode } from 'react'

import { useAuth } from '@/components/auth/context'
import { useQuery } from '@/hooks/use-query'
import { api } from '@/lib/api'
import { Permission, roleLabel } from '@/lib/auth'
import { healthTone } from '@/lib/risk'
import { cn } from '@/lib/utils'

import { Sidebar } from './sidebar'

const SIMULATION_NOTICE =
  'Real-Time Risk Intelligence - not real Razorpay infrastructure or transaction data.'

/** The wordmark. An inline SVG so it scales and needs no request. */
function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className={className}>
      {/* A shield with a razor notch: the product's two ideas in one glyph. */}
      <path
        d="M12 2 3.5 5.2v6.4c0 5 3.6 9.3 8.5 10.4 4.9-1.1 8.5-5.4 8.5-10.4V5.2L12 2Z"
        className="fill-brand"
      />
      <path d="M12 6.2 8 12.6h3l-1 5.2 4-6.4h-3l1-5.2Z" className="fill-brand-contrast" />
    </svg>
  )
}

const HEALTH_DOT = {
  positive: 'bg-positive',
  warning: 'bg-warning',
  danger: 'bg-danger',
  attention: 'bg-attention',
  neutral: 'bg-ink-faint',
  brand: 'bg-brand',
} as const

/**
 * A compact rollup of subsystem health, always visible in the chrome.
 *
 * Reads as one dot plus one phrase. An operator glances at it; the detail lives
 * on the dashboard, and the title attribute names whatever is degraded.
 */
function HealthPill() {
  const { can } = useAuth()
  // A viewer holds `dashboard:read` and so can see this; a role that cannot
  // would otherwise get a permanent "unreachable" pill from its own 403.
  const allowed = can(Permission.DashboardRead)
  const { data, error } = useQuery(
    (signal) => (allowed ? api.systemHealth(signal) : Promise.resolve(null)),
    [allowed],
  )

  if (!allowed) return null

  const tone = error ? 'danger' : data ? healthTone(data.status) : 'neutral'
  const failing = data?.components.filter((component) => component.status !== 'ok') ?? []
  const text = error
    ? 'System unreachable'
    : !data
      ? 'Checking...'
      : failing.length === 0
        ? 'All systems operational'
        : `${failing.length} subsystem${failing.length === 1 ? '' : 's'} degraded`

  return (
    <span
      title={
        failing.length > 0
          ? `Degraded: ${failing.map((component) => component.name).join(', ')}`
          : 'All subsystems reporting healthy'
      }
      className="hidden items-center gap-2 rounded-full border border-ink-border bg-ink-raised px-2.5 py-1 text-xs font-medium text-ink-muted md:inline-flex"
    >
      <span className="relative flex size-1.5">
        {tone === 'positive' ? (
          <span
            aria-hidden="true"
            className="absolute inline-flex size-full animate-ping-slow rounded-full bg-positive opacity-60"
          />
        ) : null}
        <span className={cn('relative inline-flex size-1.5 rounded-full', HEALTH_DOT[tone])} />
      </span>
      {text}
    </span>
  )
}

/** Who is signed in, and the way out. */
function SessionMenu() {
  const { session, signOut } = useAuth()
  const [open, setOpen] = useState(false)
  const container = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onPointer(event: MouseEvent) {
      if (!container.current?.contains(event.target as Node)) setOpen(false)
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  if (!session) return null

  const name = session.user.full_name ?? session.user.email
  const initials =
    name
      .split(/[\s.@_-]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? '')
      .join('') || '--'

  return (
    <div ref={container} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex items-center gap-2 rounded-full border border-ink-border bg-ink-raised py-1 pr-2.5 pl-1 text-left transition-colors hover:border-brand-soft/50"
      >
        <span
          aria-hidden="true"
          className="grid size-7 place-items-center rounded-full bg-brand text-[0.65rem] font-bold text-brand-contrast"
        >
          {initials}
        </span>
        <span className="hidden sm:block">
          <span className="block max-w-[10rem] truncate text-xs leading-tight font-medium text-ink-content">
            {name}
          </span>
          <span className="block text-[0.65rem] leading-tight text-ink-faint">
            {roleLabel(session.role)}
          </span>
        </span>
        <span aria-hidden="true" className="text-[0.6rem] text-ink-faint">
          ▾
        </span>
      </button>

      {open ? (
        <div
          role="menu"
          className="animate-fade-in absolute right-0 z-30 mt-2 w-60 rounded-xl border border-border-subtle bg-surface-raised p-1.5 shadow-floating"
        >
          <div className="border-b border-border-subtle px-2.5 py-2">
            <p className="truncate text-sm font-medium text-content">{name}</p>
            <p className="truncate text-xs text-content-muted">{session.user.email}</p>
            <p className="mt-1.5 inline-flex items-center gap-1 rounded-md bg-brand/10 px-1.5 py-0.5 text-[0.65rem] font-semibold text-brand">
              {roleLabel(session.role).toUpperCase()}
            </p>
          </div>
          <p className="px-2.5 py-2 text-xs text-content-faint">
            {/* The permission count, never the token. Nothing in this menu
                renders the credential itself. */}
            {session.permissions.length} permission
            {session.permissions.length === 1 ? '' : 's'} granted
          </p>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false)
              void signOut()
            }}
            className="w-full rounded-lg px-2.5 py-2 text-left text-sm font-medium text-content-muted transition-colors hover:bg-surface-sunken hover:text-content"
          >
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  )
}

export function AppShell({ children }: { children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <div className="flex min-h-dvh flex-col bg-surface">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-lg focus:bg-surface-raised focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:shadow-floating"
      >
        Skip to content
      </a>

      {/* The chrome is dark so the frame reads as a fixed instrument panel and
          the data below it reads as the thing inside. */}
      <header className="ink-grid sticky top-0 z-20 border-b border-ink-border bg-ink">
        <div className="mx-auto flex max-w-[110rem] items-center gap-3 px-4 py-2.5 sm:px-6">
          <button
            type="button"
            aria-expanded={menuOpen}
            aria-controls="primary-navigation"
            onClick={() => setMenuOpen((open) => !open)}
            className="-ml-1 rounded-lg p-2 text-ink-muted transition-colors hover:bg-ink-raised hover:text-ink-content lg:hidden"
          >
            <span className="sr-only">{menuOpen ? 'Close' : 'Open'} navigation</span>
            <span aria-hidden="true" className="block text-sm leading-none">
              ☰
            </span>
          </button>

          <span className="flex items-center gap-2.5">
            <Mark className="size-7" />
            <span className="leading-tight">
              <span className="block text-sm font-semibold tracking-tight text-ink-content">
                RazorShield <span className="text-brand-soft">AI</span>
              </span>
              <span className="hidden text-[0.65rem] tracking-[0.14em] text-ink-faint uppercase sm:block">
                Risk Operations
              </span>
            </span>
          </span>

          <div className="ml-auto flex items-center gap-2 sm:gap-3">
            <HealthPill />
            <SessionMenu />
          </div>
        </div>

        <p className="border-t border-ink-border/60 bg-ink-raised/60 px-4 py-1 text-center text-[0.65rem] tracking-wide text-ink-faint sm:px-6">
          {SIMULATION_NOTICE}
        </p>
      </header>

      <div className="mx-auto flex w-full max-w-[110rem] flex-1 flex-col gap-6 px-4 py-6 sm:px-6 lg:flex-row lg:gap-8">
        <aside
          id="primary-navigation"
          className={cn('lg:w-56 lg:shrink-0', menuOpen ? 'block' : 'hidden lg:block')}
        >
          <div className="lg:sticky lg:top-28">
            <Sidebar onNavigate={() => setMenuOpen(false)} />
          </div>
        </aside>
        <main id="main-content" className="min-w-0 flex-1">
          {children}
        </main>
      </div>
    </div>
  )
}
