import { Link, useLocation } from 'react-router-dom'

import { Card } from '@/components/ui/card'

export function NotFoundPage() {
  const { pathname } = useLocation()

  return (
    <Card className="flex flex-col items-start gap-3 py-10 text-center sm:items-center">
      <span
        aria-hidden="true"
        className="grid size-11 place-items-center rounded-full bg-surface-sunken text-lg text-content-faint"
      >
        ⌀
      </span>
      <h1 className="text-lg font-semibold tracking-tight">Page not found</h1>
      <p className="max-w-md text-sm text-content-muted">
        Nothing is served at <span className="numeric text-content">{pathname}</span>. It may have
        moved, or the link may be from an older build.
      </p>
      <Link
        to="/dashboard"
        className="mt-1 rounded-lg border border-border-subtle bg-surface-raised px-3.5 py-2 text-sm font-medium text-content shadow-flat transition-colors hover:border-brand/40 hover:text-brand"
      >
        Back to the dashboard
      </Link>
    </Card>
  )
}
