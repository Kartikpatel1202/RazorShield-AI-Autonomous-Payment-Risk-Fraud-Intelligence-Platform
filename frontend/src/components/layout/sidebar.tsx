import { NavLink } from 'react-router-dom'

import { useAuth } from '@/components/auth/context'
import { cn } from '@/lib/utils'

import { NAV_GROUPS, NAV_ITEMS } from './navigation'

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { can } = useAuth()
  // Links the signed-in role cannot follow are not rendered. The page behind
  // each is still guarded server-side; this only keeps the sidebar honest about
  // what this user can actually do.
  const visible = NAV_ITEMS.filter((item) => can(item.permission))

  return (
    <nav aria-label="Primary" className="flex flex-col gap-5">
      {NAV_GROUPS.map((group) => {
        const items = visible.filter((item) => item.group === group)
        if (items.length === 0) return null

        return (
          <div key={group}>
            <p className="mb-1.5 px-3 text-[0.65rem] font-semibold tracking-[0.12em] text-content-faint uppercase">
              {group}
            </p>
            <div className="flex flex-col gap-0.5">
              {items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  onClick={onNavigate}
                  title={item.description}
                  className={({ isActive }) =>
                    cn(
                      'group relative flex items-center gap-2.5 rounded-lg py-2 pr-3 pl-3',
                      'text-sm font-medium transition-colors',
                      isActive
                        ? 'bg-brand/10 text-brand'
                        : 'text-content-muted hover:bg-surface-sunken hover:text-content',
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      {/* An accent rail on the active item rather than a filled
                          block: it marks position without competing with the
                          risk colours the page itself is using. */}
                      <span
                        aria-hidden="true"
                        className={cn(
                          'absolute inset-y-1.5 left-0 w-0.5 rounded-full transition-opacity',
                          isActive ? 'bg-brand opacity-100' : 'opacity-0',
                        )}
                      />
                      <span
                        aria-hidden="true"
                        className={cn(
                          'w-4 shrink-0 text-center text-xs',
                          isActive ? 'text-brand' : 'text-content-faint',
                        )}
                      >
                        {item.glyph}
                      </span>
                      <span className="truncate">{item.label}</span>
                    </>
                  )}
                </NavLink>
              ))}
            </div>
          </div>
        )
      })}
    </nav>
  )
}
