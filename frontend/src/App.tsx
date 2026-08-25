import { Link, Navigate, Route, Routes } from 'react-router-dom'

import { AuthProvider } from '@/components/auth/auth-provider'
import { useAuth } from '@/components/auth/context'
import { AppShell } from '@/components/layout/app-shell'
import { Permission, roleLabel, type PermissionName } from '@/lib/auth'
import { AuditPage } from '@/routes/audit'
import { DashboardPage } from '@/routes/dashboard'
import { FeedbackPage } from '@/routes/feedback'
import { InvestigationsPage } from '@/routes/investigations'
import { LivePage } from '@/routes/live'
import { LoginPage } from '@/routes/login'
import { MonitoringPage } from '@/routes/monitoring'
import { NotFoundPage } from '@/routes/not-found'
import { ReviewsPage } from '@/routes/reviews'
import { RulesPage } from '@/routes/rules'
import { TransactionDetailPage } from '@/routes/transaction-detail'
import { TransactionsPage } from '@/routes/transactions'

/**
 * Refuse a route the signed-in role has no permission for.
 *
 * This is presentation, not security. Every endpoint the page behind it would
 * call is independently checked on the server, so removing this guard would
 * leak nothing - it would just replace a clear message with a page of failed
 * requests.
 */
function Guard({ permission, children }: { permission: PermissionName; children: React.ReactNode }) {
  const { can, session } = useAuth()
  if (!can(permission)) {
    return (
      <section className="flex flex-col items-start gap-3 rounded-card border border-border-subtle bg-surface-raised p-8 shadow-flat">
        <span
          aria-hidden="true"
          className="grid size-10 place-items-center rounded-full bg-warning-surface text-lg text-warning"
        >
          ⚿
        </span>
        <h1 className="text-lg font-semibold tracking-tight">Not available for your role</h1>
        <p className="max-w-lg text-sm leading-relaxed text-content-muted">
          This page needs the{' '}
          <code className="numeric rounded bg-surface-sunken px-1.5 py-0.5 text-xs text-content">
            {permission}
          </code>{' '}
          permission, which{' '}
          <span className="font-medium text-content">{roleLabel(session?.role ?? '')}</span> does
          not hold. The server refuses these requests independently - this message only saves you
          the round trip.
        </p>
        <Link
          to="/dashboard"
          className="rounded-lg border border-border-subtle px-3.5 py-2 text-sm font-medium text-content transition-colors hover:border-brand/40 hover:text-brand"
        >
          Back to the dashboard
        </Link>
      </section>
    )
  }
  return <>{children}</>
}

/** Everything behind authentication. */
function Console() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/login" element={<Navigate to="/dashboard" replace />} />
        <Route
          path="/dashboard"
          element={
            <Guard permission={Permission.DashboardRead}>
              <DashboardPage />
            </Guard>
          }
        />
        <Route
          path="/live"
          element={
            <Guard permission={Permission.EventsRead}>
              <LivePage />
            </Guard>
          }
        />
        <Route
          path="/transactions"
          element={
            <Guard permission={Permission.TransactionsRead}>
              <TransactionsPage />
            </Guard>
          }
        />
        <Route
          path="/transactions/:transactionId"
          element={
            <Guard permission={Permission.TransactionsRead}>
              <TransactionDetailPage />
            </Guard>
          }
        />
        <Route
          path="/investigations"
          element={
            <Guard permission={Permission.InvestigationsRead}>
              <InvestigationsPage />
            </Guard>
          }
        />
        <Route
          path="/reviews"
          element={
            <Guard permission={Permission.ReviewsRead}>
              <ReviewsPage />
            </Guard>
          }
        />
        <Route
          path="/feedback"
          element={
            <Guard permission={Permission.MonitoringRead}>
              <FeedbackPage />
            </Guard>
          }
        />
        {/* Nested: the monitoring page renders its own tab router. */}
        <Route
          path="/monitoring/*"
          element={
            <Guard permission={Permission.MonitoringRead}>
              <MonitoringPage />
            </Guard>
          }
        />
        <Route
          path="/rules"
          element={
            <Guard permission={Permission.DashboardRead}>
              <RulesPage />
            </Guard>
          }
        />
        <Route
          path="/audit"
          element={
            <Guard permission={Permission.AuditRead}>
              <AuditPage />
            </Guard>
          }
        />
        {/* The Phase 1 shell linked here; keep it working rather than 404ing. */}
        <Route path="/audit-log" element={<Navigate to="/audit" replace />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </AppShell>
  )
}

function Routed() {
  const { signedIn } = useAuth()
  // One branch, not a per-route redirect: while signed out there is exactly one
  // reachable page, so there is no URL a probe can hit that renders console
  // chrome before its data requests fail.
  if (!signedIn) {
    return (
      <Routes>
        <Route path="*" element={<LoginPage />} />
      </Routes>
    )
  }
  return <Console />
}

export function App() {
  return (
    <AuthProvider>
      <Routed />
    </AuthProvider>
  )
}
