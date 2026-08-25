import { Permission, type PermissionName } from '@/lib/auth'

/**
 * Primary navigation.
 *
 * Every entry is implemented. The Phase 1 "Soon" placeholders are gone - a nav
 * item that goes nowhere is worse than no nav item.
 *
 * Each entry names the permission its page needs. That is used to hide links a
 * role cannot follow, which is a courtesy and not a control: the server refuses
 * the request behind every one of these regardless of what the sidebar renders.
 *
 * Entries are grouped because nine flat links is a list to be scanned, while
 * three short groups is a structure to be navigated - and the grouping matches
 * how the work is actually done: watch, then investigate, then measure.
 */
export type NavGroup = 'Operations' | 'Casework' | 'Assurance'

export interface NavItem {
  readonly label: string
  readonly to: string
  readonly description: string
  readonly permission: PermissionName
  readonly group: NavGroup
  /** A single glyph. Decorative - the label always carries the meaning. */
  readonly glyph: string
}

export const NAV_GROUPS: readonly NavGroup[] = ['Operations', 'Casework', 'Assurance']

export const NAV_ITEMS: readonly NavItem[] = [
  {
    label: 'Dashboard',
    to: '/dashboard',
    description: 'Risk posture at a glance',
    permission: Permission.DashboardRead,
    group: 'Operations',
    glyph: '◈',
  },
  {
    label: 'Live',
    to: '/live',
    description: 'Real-time risk stream',
    permission: Permission.EventsRead,
    group: 'Operations',
    glyph: '◉',
  },
  {
    label: 'Transactions',
    to: '/transactions',
    description: 'Search and filter every payment',
    permission: Permission.TransactionsRead,
    group: 'Operations',
    glyph: '▤',
  },
  {
    label: 'Investigations',
    to: '/investigations',
    description: 'Evidence-grounded agent reports',
    permission: Permission.InvestigationsRead,
    group: 'Casework',
    glyph: '◎',
  },
  {
    label: 'Reviews',
    to: '/reviews',
    description: 'Human review queue',
    permission: Permission.ReviewsRead,
    group: 'Casework',
    glyph: '⚑',
  },
  {
    label: 'Feedback',
    to: '/feedback',
    description: 'What analysts concluded',
    permission: Permission.MonitoringRead,
    group: 'Casework',
    glyph: '◍',
  },
  {
    label: 'Monitoring',
    to: '/monitoring',
    description: 'Model, drift and policy metrics',
    permission: Permission.MonitoringRead,
    group: 'Assurance',
    glyph: '◑',
  },
  {
    label: 'Policy',
    to: '/rules',
    description: 'The active decision policy',
    permission: Permission.DashboardRead,
    group: 'Assurance',
    glyph: '§',
  },
  {
    label: 'Audit Log',
    to: '/audit',
    description: 'Every recorded event',
    permission: Permission.AuditRead,
    group: 'Assurance',
    glyph: '≡',
  },
]
