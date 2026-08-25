import { useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge, DecisionBadge, ResolutionBadge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardHeader, CardNote } from '@/components/ui/card'
import { SegmentedControl } from '@/components/ui/field'
import { PageHeader } from '@/components/ui/page-header'
import { Pagination } from '@/components/ui/pagination'
import { QueryBoundary, Skeleton, TableSkeleton } from '@/components/ui/states'
import { DataTable, Td, Th, Tr } from '@/components/ui/table'
import { Timeline, type TimelineEntry } from '@/components/ui/timeline'
import { useQuery } from '@/hooks/use-query'
import { api, type AuditEntry } from '@/lib/api'
import { formatCount, formatDateTime, humanizeCode } from '@/lib/format'
import { decisionTone, type Tone } from '@/lib/risk'
import { cn } from '@/lib/utils'

const PAGE_SIZE = 25

/**
 * The tone for an event type.
 *
 * The lifecycle reads as a progression, so the colours follow it: ingestion and
 * scoring are neutral, an investigation is the brand accent, a decision takes
 * its own decision colour, and a human action is attention.
 */
function eventTone(entry: AuditEntry): Tone {
  if (entry.decision) return decisionTone(entry.decision)
  if (entry.resolution) return 'attention'
  if (entry.event_type.startsWith('investigation')) return 'brand'
  if (entry.event_type.startsWith('feedback')) return 'attention'
  return 'neutral'
}

/** A short glyph per lifecycle stage, so the rail is legible without colour. */
function eventGlyph(entry: AuditEntry): string | undefined {
  if (entry.event_type.startsWith('risk.decision')) return '✓'
  if (entry.event_type.startsWith('investigation')) return '◎'
  if (entry.event_type.startsWith('review')) return '⚑'
  if (entry.event_type.startsWith('feedback')) return '◍'
  return undefined
}

/** Everything the stored document holds, for one event. */
function EventDetail({ entry }: { entry: AuditEntry }) {
  const data = entry.event_data
  const matched = Array.isArray(data.matched_rules) ? (data.matched_rules as string[]) : []
  const reasons = Array.isArray(data.reason_codes) ? (data.reason_codes as string[]) : []

  return (
    <div className="mt-3 flex flex-col gap-3 rounded-lg border border-border-subtle bg-surface-sunken/50 p-3">
      <dl className="grid gap-3 sm:grid-cols-3">
        {entry.decision_id ? (
          <div>
            <dt className="text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
              Decision ID
            </dt>
            <dd className="numeric text-xs break-all">{entry.decision_id}</dd>
          </div>
        ) : null}
        {entry.investigation_id ? (
          <div>
            <dt className="text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
              Investigation ID
            </dt>
            <dd className="numeric text-xs break-all">{entry.investigation_id}</dd>
          </div>
        ) : null}
        {typeof data.input_digest === 'string' ? (
          <div>
            <dt className="text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
              Decision digest
            </dt>
            <dd className="numeric text-xs break-all">{data.input_digest}</dd>
          </div>
        ) : null}
      </dl>

      {matched.length > 0 ? (
        <div>
          <p className="text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
            Matched rules
          </p>
          <p className="numeric mt-1 text-xs">{matched.join(', ')}</p>
        </div>
      ) : null}

      {reasons.length > 0 ? (
        <div>
          <p className="text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
            Reason codes
          </p>
          <div className="mt-1 flex flex-wrap gap-1">
            {reasons.map((code) => (
              <Badge key={code} tone="neutral">
                {code}
              </Badge>
            ))}
          </div>
        </div>
      ) : null}

      <details>
        <summary className="cursor-pointer text-xs font-medium text-content-muted hover:text-content">
          Full event document
        </summary>
        <pre className="mt-2 max-h-72 overflow-auto rounded-md border border-border-subtle bg-surface-raised p-3 text-xs text-content-muted">
          {JSON.stringify(data, null, 2)}
        </pre>
      </details>
    </div>
  )
}

export function AuditPage() {
  const [page, setPage] = useState(1)
  const [eventType, setEventType] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null)
  const [view, setView] = useState<'timeline' | 'table'>('timeline')

  const summary = useQuery((signal) => api.auditSummary(signal), [])
  const query = useQuery(
    (signal) =>
      api.audit({ page, page_size: PAGE_SIZE, event_type: eventType || undefined }, signal),
    [page, eventType],
  )

  const counts = summary.data?.counts ?? {}
  const total = Object.values(counts).reduce((sum, count) => sum + count, 0)

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Assurance"
        title="Audit log"
        description="Every recorded event, newest first. Enough per entry to answer why a decision was made, with the full document one click away."
        meta={
          summary.data ? (
            <span>
              <span className="numeric font-medium text-content-muted">{formatCount(total)}</span>{' '}
              events across {Object.keys(counts).length} types
            </span>
          ) : (
            <Skeleton className="h-4 w-48" />
          )
        }
        actions={
          <SegmentedControl
            label="View"
            value={view}
            options={[
              { value: 'timeline' as const, label: 'Timeline' },
              { value: 'table' as const, label: 'Table' },
            ]}
            onChange={setView}
          />
        }
      />

      {/* --- event-type filter, with its counts ---------------------------- */}
      {summary.data ? (
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by event type">
          <button
            type="button"
            aria-pressed={eventType === ''}
            onClick={() => {
              setEventType('')
              setPage(1)
            }}
            className={cn(
              'rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors',
              eventType === ''
                ? 'border-brand bg-brand text-brand-contrast'
                : 'border-border-subtle text-content-muted hover:border-border-strong hover:text-content',
            )}
          >
            All
            <span className="numeric ml-1.5 opacity-70">{formatCount(total)}</span>
          </button>
          {Object.entries(counts).map(([type, count]) => (
            <button
              key={type}
              type="button"
              aria-pressed={eventType === type}
              onClick={() => {
                setEventType(type)
                setPage(1)
              }}
              className={cn(
                'rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors',
                eventType === type
                  ? 'border-brand bg-brand text-brand-contrast'
                  : 'border-border-subtle text-content-muted hover:border-border-strong hover:text-content',
              )}
            >
              <span className="numeric">{type}</span>
              <span className="numeric ml-1.5 opacity-70">{formatCount(count)}</span>
            </button>
          ))}
        </div>
      ) : null}

      <Card>
        <CardHeader
          title="Events"
          description="Identifiers and outcomes only - the trail never records prompts, model text or credentials."
        />
        <div className="mt-4">
          <QueryBoundary
            loading={query.loading}
            error={query.error}
            data={query.data}
            onRetry={query.refetch}
            isEmpty={(data) => data.items.length === 0}
            emptyTitle="No events recorded"
            emptyDescription="Nothing matches this filter."
            skeleton={<TableSkeleton rows={8} columns={6} />}
          >
            {(data) =>
              view === 'timeline' ? (
                <>
                  <Timeline
                    entries={data.items.map<TimelineEntry>((entry) => ({
                      id: String(entry.audit_id),
                      tone: eventTone(entry),
                      glyph: eventGlyph(entry),
                      title: (
                        <span className="inline-flex flex-wrap items-center gap-2">
                          {humanizeCode(entry.event_type)}
                          {entry.decision ? <DecisionBadge decision={entry.decision} /> : null}
                          {entry.resolution ? (
                            <ResolutionBadge resolution={entry.resolution} />
                          ) : null}
                        </span>
                      ),
                      timestamp: formatDateTime(entry.created_at),
                      body: (
                        <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
                          {entry.transaction_id ? (
                            <Link
                              to={`/transactions/${encodeURIComponent(entry.transaction_id)}`}
                              className="numeric text-xs font-medium text-brand hover:underline"
                            >
                              {entry.transaction_id}
                            </Link>
                          ) : null}
                          <span className="text-xs">
                            {entry.actor_type}
                            {entry.actor_id ? (
                              <span className="numeric ml-1 text-content-faint">
                                {entry.actor_id}
                              </span>
                            ) : null}
                          </span>
                          {entry.policy_version ? (
                            <span className="numeric text-xs text-content-faint">
                              {entry.policy_version}
                            </span>
                          ) : null}
                        </span>
                      ),
                      meta: (
                        <>
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-expanded={expanded === entry.audit_id}
                            onClick={() =>
                              setExpanded(expanded === entry.audit_id ? null : entry.audit_id)
                            }
                            className="-ml-2"
                          >
                            {expanded === entry.audit_id ? 'Hide details' : 'Details'}
                          </Button>
                          {expanded === entry.audit_id ? <EventDetail entry={entry} /> : null}
                        </>
                      ),
                    }))}
                  />
                  <Pagination meta={data.meta} onPageChange={setPage} itemLabel="events" />
                </>
              ) : (
                <>
                  <DataTable>
                    <thead>
                      <tr>
                        <Th>Timestamp</Th>
                        <Th>Event</Th>
                        <Th>Transaction</Th>
                        <Th>Outcome</Th>
                        <Th>Policy</Th>
                        <Th>Actor</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((entry) => (
                        <Tr key={entry.audit_id}>
                          <Td className="numeric text-xs whitespace-nowrap text-content-muted">
                            {formatDateTime(entry.created_at)}
                          </Td>
                          <Td className="numeric text-xs">{entry.event_type}</Td>
                          <Td>
                            {entry.transaction_id ? (
                              <Link
                                to={`/transactions/${encodeURIComponent(entry.transaction_id)}`}
                                className="numeric text-xs font-medium text-brand hover:underline"
                              >
                                {entry.transaction_id}
                              </Link>
                            ) : (
                              <span className="text-content-faint">--</span>
                            )}
                          </Td>
                          <Td>
                            {entry.decision ? <DecisionBadge decision={entry.decision} /> : null}
                            {entry.resolution ? (
                              <ResolutionBadge resolution={entry.resolution} />
                            ) : null}
                            {!entry.decision && !entry.resolution ? (
                              <span className="text-content-faint">--</span>
                            ) : null}
                          </Td>
                          <Td className="numeric text-xs text-content-muted">
                            {entry.policy_version ?? '--'}
                          </Td>
                          <Td className="text-xs text-content-muted">
                            {entry.actor_type}
                            {entry.actor_id ? (
                              <span className="numeric ml-1 text-content-faint">
                                {entry.actor_id}
                              </span>
                            ) : null}
                          </Td>
                        </Tr>
                      ))}
                    </tbody>
                  </DataTable>
                  <Pagination meta={data.meta} onPageChange={setPage} itemLabel="events" />
                </>
              )
            }
          </QueryBoundary>
        </div>
        <CardNote>
          Risk decisions are append-only: an entry here is never edited or removed, and a human
          resolution is recorded beside the machine decision rather than over it.
        </CardNote>
      </Card>
    </div>
  )
}
