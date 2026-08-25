import type { HTMLAttributes, ReactNode, TdHTMLAttributes, ThHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

/**
 * A dense data table.
 *
 * The wrapper scrolls horizontally on its own so the page body never does -
 * the console must not overflow at mobile width, and a risk table has more
 * columns than a phone has room for.
 */
export function DataTable({ children, className }: { children: ReactNode; className?: string | undefined }) {
  return (
    <div className="table-scroll">
      <table className={cn('w-full min-w-[52rem] border-collapse text-sm', className)}>
        {children}
      </table>
    </div>
  )
}

export function Th({
  className,
  numeric,
  ...props
}: ThHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean | undefined }) {
  return (
    <th
      scope="col"
      className={cn(
        'border-b border-border-subtle px-3 py-2.5 text-xs font-semibold',
        'tracking-wide text-content-muted uppercase',
        numeric ? 'text-right' : 'text-left',
        className,
      )}
      {...props}
    />
  )
}

export function Td({
  className,
  numeric,
  ...props
}: TdHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean | undefined }) {
  return (
    <td
      className={cn(
        'border-b border-border-subtle px-3 py-2.5 align-middle',
        numeric && 'numeric text-right',
        className,
      )}
      {...props}
    />
  )
}

export function Tr({ className, ...props }: HTMLAttributes<HTMLTableRowElement>) {
  return <tr className={cn('hover:bg-surface-sunken/60', className)} {...props} />
}

/** Sortable column header. The active key and direction are always announced. */
export function SortableTh({
  label,
  columnKey,
  activeKey,
  descending,
  onSort,
  numeric,
}: {
  label: string
  columnKey: string
  activeKey: string
  descending: boolean
  onSort: (key: string) => void
  numeric?: boolean | undefined
}) {
  const active = activeKey === columnKey
  return (
    <Th numeric={numeric} aria-sort={active ? (descending ? 'descending' : 'ascending') : 'none'}>
      <button
        type="button"
        onClick={() => onSort(columnKey)}
        className={cn(
          'inline-flex items-center gap-1 uppercase hover:text-content',
          active && 'text-content',
        )}
      >
        {label}
        <span aria-hidden="true" className="text-[0.65rem]">
          {active ? (descending ? '▼' : '▲') : '↕'}
        </span>
      </button>
    </Th>
  )
}
