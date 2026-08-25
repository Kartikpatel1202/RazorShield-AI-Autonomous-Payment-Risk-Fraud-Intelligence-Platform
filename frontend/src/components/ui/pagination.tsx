import { Button } from '@/components/ui/button'
import type { PageMeta } from '@/lib/api'
import { formatCount } from '@/lib/format'

/**
 * Page navigation for server-side paginated tables.
 *
 * Reports the total so the reader knows the table is a window onto a larger
 * set, not the whole of it.
 */
export function Pagination({
  meta,
  onPageChange,
  itemLabel = 'rows',
}: {
  meta: PageMeta
  onPageChange: (page: number) => void
  itemLabel?: string | undefined
}) {
  const first = meta.total_items === 0 ? 0 : (meta.page - 1) * meta.page_size + 1
  const last = Math.min(meta.page * meta.page_size, meta.total_items)

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 pt-3">
      <p className="text-sm text-content-muted">
        <span className="numeric">{formatCount(first)}</span>–
        <span className="numeric">{formatCount(last)}</span> of{' '}
        <span className="numeric font-medium text-content">{formatCount(meta.total_items)}</span>{' '}
        {itemLabel}
      </p>
      <div className="flex items-center gap-2">
        <Button
          variant="secondary"
          disabled={!meta.has_previous}
          onClick={() => onPageChange(meta.page - 1)}
        >
          Previous
        </Button>
        <span className="numeric text-sm text-content-muted">
          {meta.page} / {Math.max(meta.total_pages, 1)}
        </span>
        <Button
          variant="secondary"
          disabled={!meta.has_next}
          onClick={() => onPageChange(meta.page + 1)}
        >
          Next
        </Button>
      </div>
    </div>
  )
}
