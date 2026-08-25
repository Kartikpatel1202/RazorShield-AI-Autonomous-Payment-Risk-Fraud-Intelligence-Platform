import { useState } from 'react'
import { Link } from 'react-router-dom'

import {
  Badge,
  DecisionBadge,
  ResolutionBadge,
  ReviewStatusBadge,
} from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardHeader } from '@/components/ui/card'
import { SegmentedControl, SelectField } from '@/components/ui/field'
import { PageHeader } from '@/components/ui/page-header'
import { Pagination } from '@/components/ui/pagination'
import { ErrorState, QueryBoundary, TableSkeleton } from '@/components/ui/states'
import { DataTable, Td, Th, Tr } from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { useQuery } from '@/hooks/use-query'
import { api, type ReviewCase } from '@/lib/api'
import { OutcomeBadge } from '@/routes/feedback'
import { formatDateTime, formatProbability, humanizeCode } from '@/lib/format'

const PAGE_SIZE = 20
const STATUSES = ['open', 'in_review', 'escalated', 'resolved'] as const

const RESOLUTIONS = [
  { value: 'approved', label: 'Approve', hint: 'Let the payment proceed' },
  { value: 'rejected', label: 'Reject', hint: 'Stop the payment' },
  { value: 'escalated', label: 'Escalate', hint: 'Send to a senior reviewer' },
] as const

/**
 * Feedback outcomes with the reasons each permits.
 *
 * Mirrors the backend's FEEDBACK_REASONS_BY_OUTCOME so the form cannot offer a
 * combination the API will reject. The API validates independently - this is
 * for the analyst's benefit, not a substitute for server-side checks.
 */
const FRAUD_REASONS = [
  'confirmed_fraud',
  'account_takeover',
  'coordinated_activity',
  'stolen_payment_method',
  'suspicious_device',
  'suspicious_ip',
] as const

const CLEAN_REASONS = [
  'legitimate_transaction',
  'known_customer_behavior',
  'trusted_merchant',
  'expected_location',
  'expected_device',
] as const

const FEEDBACK_OPTIONS: readonly {
  outcome: string
  label: string
  reasons: readonly string[]
}[] = [
  { outcome: 'confirmed_fraud', label: 'Confirmed fraud', reasons: FRAUD_REASONS },
  { outcome: 'legitimate', label: 'Legitimate', reasons: CLEAN_REASONS },
  {
    outcome: 'false_positive',
    label: 'False positive',
    reasons: ['model_false_positive', ...CLEAN_REASONS],
  },
  {
    outcome: 'false_negative',
    label: 'False negative',
    reasons: ['model_false_negative', ...FRAUD_REASONS],
  },
  {
    outcome: 'insufficient_evidence',
    label: 'Insufficient evidence',
    reasons: ['insufficient_evidence', 'needs_more_information'],
  },
  {
    outcome: 'escalated',
    label: 'Escalated',
    reasons: [
      'insufficient_evidence',
      'needs_more_information',
      'coordinated_activity',
      'account_takeover',
    ],
  },
]

/**
 * The review detail panel.
 *
 * The critical property this UI must convey: the machine decision and the human
 * resolution are two separate records. They are shown in two visually distinct
 * blocks, labelled explicitly, and resolving never alters the decision above.
 */
function ReviewPanel({
  reviewCase,
  onResolved,
  onClose,
}: {
  reviewCase: ReviewCase
  onResolved: () => void
  onClose: () => void
}) {
  const [reason, setReason] = useState('')
  const [outcome, setOutcome] = useState('')
  const [reasonCode, setReasonCode] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState<Error | undefined>(undefined)

  const settled = reviewCase.status === 'resolved'
  const selected = FEEDBACK_OPTIONS.find((option) => option.outcome === outcome)
  // The API rejects one without the other, so the form blocks submission until
  // both are chosen rather than letting the analyst discover it from a 422.
  const feedbackIncomplete = Boolean(outcome) !== Boolean(reasonCode)

  async function resolve(resolution: string) {
    setPending(resolution)
    setError(undefined)
    try {
      await api.resolveReview(reviewCase.review_case_id, {
        resolution,
        reason: reason.trim() || undefined,
        ...(outcome && reasonCode
          ? {
              feedback_outcome: outcome,
              feedback_reason: reasonCode,
              feedback_notes: reason.trim() || undefined,
            }
          : {}),
      })
      onResolved()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error(String(caught)))
    } finally {
      setPending(null)
    }
  }

  return (
    <Card className="animate-fade-in border-brand/30 shadow-raised">
      <CardHeader
        title={`Review case #${reviewCase.review_case_id}`}
        description={
          <span className="inline-flex flex-wrap items-center gap-2">
            <Link
              to={`/transactions/${encodeURIComponent(reviewCase.transaction_id)}`}
              className="numeric text-xs font-medium text-brand hover:underline"
            >
              {reviewCase.transaction_id}
            </Link>
            <span className="text-content-faint">·</span>
            <span>opened {formatDateTime(reviewCase.created_at)}</span>
          </span>
        }
        actions={
          <Button variant="ghost" size="sm" onClick={onClose}>
            Close
          </Button>
        }
      />

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {/* --- what the machine decided ---------------------------------- */}
        <section className="rounded-lg border border-border-subtle bg-surface-sunken/50 p-3">
          <h3 className="text-xs font-semibold tracking-wide text-content-muted uppercase">
            Machine decision
          </h3>
          {reviewCase.decision ? (
            <div className="mt-2 flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <DecisionBadge decision={reviewCase.decision.decision} />
                <Badge tone="neutral">{reviewCase.decision.policy_version}</Badge>
              </div>
              <p className="numeric text-xs text-content-muted">
                {reviewCase.decision.decision_id}
              </p>
              <dl className="grid grid-cols-2 gap-3 rounded-md border border-border-subtle bg-surface-raised p-2.5">
                <div>
                  <dt className="text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
                    Fraud probability
                  </dt>
                  <dd className="numeric text-lg font-semibold">
                    {formatProbability(reviewCase.decision.fraud_probability)}
                  </dd>
                </div>
                <div>
                  <dt className="text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
                    Anomaly
                  </dt>
                  <dd className="numeric text-lg font-semibold">
                    {reviewCase.decision.anomaly_score ?? '--'} / 100
                  </dd>
                </div>
              </dl>
              <div>
                <p className="text-[0.65rem] font-semibold tracking-[0.08em] text-content-muted uppercase">
                  Reason codes
                </p>
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {reviewCase.decision.reason_codes.map((code) => (
                    <Badge key={code} tone="neutral" title={code}>
                      {humanizeCode(code)}
                    </Badge>
                  ))}
                </div>
              </div>
              <p className="text-xs text-content-faint">
                This record is immutable. Resolving the case never changes it.
              </p>
            </div>
          ) : (
            <p className="mt-2 text-sm text-content-muted">
              No decision is linked to this case.
            </p>
          )}
        </section>

        {/* --- what the human decides ------------------------------------ */}
        <section className="rounded-lg border border-brand/30 bg-brand/5 p-3">
          <h3 className="text-xs font-semibold tracking-wide text-brand uppercase">
            Human resolution
          </h3>

          {settled ? (
            <div className="mt-2 flex flex-col gap-2">
              <ResolutionBadge resolution={reviewCase.resolution} />
              {reviewCase.resolution_reason ? (
                <p className="text-sm text-content-muted">{reviewCase.resolution_reason}</p>
              ) : null}
              <FeedbackForCase transactionId={reviewCase.transaction_id} />
              <p className="text-xs text-content-faint">
                Resolved {formatDateTime(reviewCase.resolved_at)}. Resolutions are recorded once.
              </p>
            </div>
          ) : (
            <div className="mt-2 flex flex-col gap-3">
              {reviewCase.resolution ? (
                <p className="text-xs text-content-muted">
                  Currently <ResolutionBadge resolution={reviewCase.resolution} /> - still open for
                  a final decision.
                </p>
              ) : null}
              <label className="flex flex-col gap-1.5">
                <span className="text-[0.65rem] font-semibold tracking-[0.06em] text-content-muted uppercase">
                  Reason (recorded verbatim)
                </span>
                <textarea
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  rows={3}
                  maxLength={2000}
                  placeholder="Why are you reaching this outcome?"
                  className="w-full rounded-lg border border-border-subtle bg-surface-raised px-2.5 py-1.5 text-sm text-content shadow-flat transition-colors placeholder:text-content-faint hover:border-border-strong focus:border-brand focus:ring-2 focus:ring-brand/25 focus:outline-none"
                />
              </label>

              <div className="rounded-md border border-border-subtle bg-surface-raised p-2.5">
                <p className="text-xs font-semibold tracking-wide text-content-muted uppercase">
                  Feedback (optional)
                </p>
                <p className="mt-0.5 text-xs text-content-faint">
                  What you concluded was <em>true</em>, which is not the same as what you did with
                  the payment. Recorded separately so both stay measurable.
                </p>
                <div className="mt-2.5 grid gap-2.5 sm:grid-cols-2">
                  <SelectField
                    label="Outcome"
                    value={outcome}
                    placeholder="No feedback"
                    options={FEEDBACK_OPTIONS.map((option) => ({
                      value: option.outcome,
                      label: option.label,
                    }))}
                    onChange={(event) => {
                      setOutcome(event.target.value)
                      setReasonCode('')
                    }}
                  />
                  {selected ? (
                    <SelectField
                      label="Reason"
                      value={reasonCode}
                      placeholder="Select a reason"
                      options={selected.reasons.map((code) => ({
                        value: code,
                        label: code.replace(/_/g, ' '),
                      }))}
                      onChange={(event) => setReasonCode(event.target.value)}
                    />
                  ) : null}
                </div>
              </div>

              {feedbackIncomplete ? (
                <p className="text-xs text-warning">
                  Choose a reason to record this outcome, or set the outcome back to &quot;No
                  feedback&quot;.
                </p>
              ) : null}

              {error ? <ErrorState error={error} title="Could not record the resolution" /> : null}

              {/* The three outcomes, given equal visual weight and their own
                  colours. A single primary button would nudge the analyst
                  toward one answer, which is the last thing this form should
                  do. */}
              <div className="grid gap-2 sm:grid-cols-3">
                {RESOLUTIONS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    disabled={pending !== null || feedbackIncomplete}
                    title={option.hint}
                    onClick={() => void resolve(option.value)}
                    className={cn(
                      'flex flex-col items-start gap-0.5 rounded-lg border px-3 py-2.5 text-left transition-colors',
                      'disabled:pointer-events-none disabled:opacity-50',
                      option.value === 'approved' &&
                        'border-positive/40 bg-positive-surface hover:border-positive',
                      option.value === 'rejected' &&
                        'border-danger/40 bg-danger-surface hover:border-danger',
                      option.value === 'escalated' &&
                        'border-warning/40 bg-warning-surface hover:border-warning',
                    )}
                  >
                    <span
                      className={cn(
                        'text-sm font-semibold',
                        option.value === 'approved' && 'text-positive',
                        option.value === 'rejected' && 'text-danger',
                        option.value === 'escalated' && 'text-warning',
                      )}
                    >
                      {pending === option.value ? 'Recording...' : option.label}
                    </span>
                    {/* aria-hidden: the hint repeats what `title` already
                        carries, and folding it into the button's accessible
                        name turns "Approve" into "Approve Let the payment
                        proceed" for anyone listening. */}
                    <span aria-hidden="true" className="text-[0.65rem] text-content-muted">
                      {option.hint}
                    </span>
                  </button>
                ))}
              </div>
              <p className="text-xs text-content-faint">
                Your answer is stored alongside the machine decision, never over it.
              </p>
            </div>
          )}
        </section>
      </div>
    </Card>
  )
}

/** Any structured feedback already recorded for this transaction. */
function FeedbackForCase({ transactionId }: { transactionId: string }) {
  const query = useQuery(
    (signal) => api.feedback({ transaction_id: transactionId, page_size: 5 }, signal),
    [transactionId],
  )

  const items = query.data?.items ?? []
  if (query.loading || items.length === 0) return null

  return (
    <div className="mt-1 flex flex-col gap-1.5 border-t border-border-subtle pt-2">
      <p className="text-xs font-semibold tracking-wide text-content-muted uppercase">
        Feedback recorded
      </p>
      {items.map((item) => (
        <div key={item.feedback_id} className="flex flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <OutcomeBadge outcome={item.outcome} />
            <span className="text-xs text-content-muted">
              {item.reason_code.replace(/_/g, ' ')}
            </span>
          </div>
          {item.notes ? <p className="text-xs text-content-faint">{item.notes}</p> : null}
        </div>
      ))}
    </div>
  )
}

export function ReviewsPage() {
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('open')
  const [selected, setSelected] = useState<number | null>(null)

  const query = useQuery(
    (signal) =>
      api.reviews({ page, page_size: PAGE_SIZE, status: status || undefined }, signal),
    [page, status],
  )

  const selectedCase = query.data?.items.find((item) => item.review_case_id === selected)

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Casework"
        title="Human review"
        description="Transactions the policy routed to a person. The machine decision and the analyst's resolution are recorded separately, so disagreement stays visible."
        meta={
          query.data ? (
            <span>
              <span className="numeric font-medium text-content-muted">
                {query.data.meta.total_items}
              </span>{' '}
              case{query.data.meta.total_items === 1 ? '' : 's'} in this queue
            </span>
          ) : undefined
        }
        actions={
          <SegmentedControl
            label="Filter by status"
            value={status}
            options={[
              { value: '', label: 'All' },
              ...STATUSES.map((option) => ({
                value: option as string,
                label: option.replace('_', ' ').replace(/^./, (c) => c.toUpperCase()),
              })),
            ]}
            onChange={(option) => {
              setStatus(option)
              setPage(1)
              setSelected(null)
            }}
          />
        }
      />

      {selectedCase ? (
        <ReviewPanel
          reviewCase={selectedCase}
          onClose={() => setSelected(null)}
          onResolved={() => {
            setSelected(null)
            query.refetch()
          }}
        />
      ) : null}

      <Card>
        <QueryBoundary
          loading={query.loading}
          error={query.error}
          data={query.data}
          onRetry={query.refetch}
          isEmpty={(data) => data.items.length === 0}
          emptyTitle="Nothing in this queue"
          emptyDescription="No review cases match the selected status."
          skeleton={<TableSkeleton rows={6} columns={8} />}
        >
          {(data) => (
            <>
              <DataTable>
                <thead>
                  <tr>
                    <Th>Case</Th>
                    <Th>Transaction</Th>
                    <Th>Machine decision</Th>
                    <Th numeric>Fraud prob.</Th>
                    <Th numeric>Anomaly</Th>
                    <Th>Why</Th>
                    <Th>Status</Th>
                    <Th>Resolution</Th>
                    <Th>Opened</Th>
                    <Th><span className="sr-only">Actions</span></Th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((item) => (
                    <Tr
                      key={item.review_case_id}
                      className={cn(selected === item.review_case_id && 'bg-brand/5')}
                    >
                      <Td className="numeric text-xs">#{item.review_case_id}</Td>
                      <Td>
                        <Link
                          to={`/transactions/${encodeURIComponent(item.transaction_id)}`}
                          className="numeric text-xs font-medium text-brand hover:underline"
                        >
                          {item.transaction_id}
                        </Link>
                      </Td>
                      <Td>
                        <DecisionBadge decision={item.decision?.decision} />
                      </Td>
                      <Td numeric className="font-semibold">
                        {formatProbability(item.decision?.fraud_probability)}
                      </Td>
                      <Td numeric>{item.decision?.anomaly_score ?? '--'}</Td>
                      <Td>
                        {item.decision?.reason_codes.length ? (
                          <span className="flex flex-wrap gap-1">
                            {item.decision.reason_codes.slice(0, 2).map((code) => (
                              <Badge key={code} tone="neutral" title={code}>
                                {humanizeCode(code)}
                              </Badge>
                            ))}
                            {item.decision.reason_codes.length > 2 ? (
                              <span className="text-xs text-content-faint">
                                +{item.decision.reason_codes.length - 2}
                              </span>
                            ) : null}
                          </span>
                        ) : (
                          <span className="text-content-faint">--</span>
                        )}
                      </Td>
                      <Td>
                        <ReviewStatusBadge status={item.status} />
                      </Td>
                      <Td>
                        <ResolutionBadge resolution={item.resolution} />
                      </Td>
                      <Td className="numeric text-xs whitespace-nowrap text-content-muted">
                        {formatDateTime(item.created_at)}
                      </Td>
                      <Td>
                        <Button
                          variant={selected === item.review_case_id ? 'primary' : 'secondary'}
                          size="sm"
                          onClick={() => setSelected(item.review_case_id)}
                        >
                          {/* "Review", not "Open": the status filter above has
                              an "Open" segment, and two controls with the same
                              name is a coin toss for anyone navigating by
                              keyboard or screen reader. */}
                          Review
                        </Button>
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </DataTable>
              <Pagination meta={data.meta} onPageChange={setPage} itemLabel="cases" />
            </>
          )}
        </QueryBoundary>
      </Card>
    </div>
  )
}
