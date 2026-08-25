import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge, DecisionBadge, SeverityBadge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardHeader } from '@/components/ui/card'
import { RangeField, SelectField, TextField, type SelectOption } from '@/components/ui/field'
import { PageHeader } from '@/components/ui/page-header'
import { Pagination } from '@/components/ui/pagination'
import { QueryBoundary, TableSkeleton } from '@/components/ui/states'
import { DataTable, SortableTh, Td, Th, Tr } from '@/components/ui/table'
import { useDebounced } from '@/hooks/use-debounced'
import { useQuery } from '@/hooks/use-query'
import { api } from '@/lib/api'
import { formatAmount, formatCount, formatDateTime, formatProbability } from '@/lib/format'
import { severityTone, toneClasses } from '@/lib/risk'
import { cn } from '@/lib/utils'

const PAGE_SIZE = 25

const DECISIONS: readonly SelectOption[] = [
  { value: 'approve', label: 'APPROVE' },
  { value: 'step_up', label: 'STEP-UP' },
  { value: 'review', label: 'REVIEW' },
  { value: 'block', label: 'BLOCK' },
]

const BANDS: readonly SelectOption[] = [
  { value: 'LOW', label: 'LOW' },
  { value: 'MEDIUM', label: 'MEDIUM' },
  { value: 'HIGH', label: 'HIGH' },
  { value: 'CRITICAL', label: 'CRITICAL' },
]

const STATUSES: readonly SelectOption[] = [
  { value: 'pending', label: 'PENDING' },
  { value: 'authorized', label: 'AUTHORIZED' },
  { value: 'captured', label: 'CAPTURED' },
  { value: 'failed', label: 'FAILED' },
  { value: 'refunded', label: 'REFUNDED' },
]

/** The scenario transactions, offered as a shortcut rather than hardcoded data. */
const DEMO_SCENARIOS = [
  { id: 'TXN_SCENARIO_A_CURRENT', label: 'A · normal' },
  { id: 'TXN_SCENARIO_B_CURRENT', label: 'B · suspicious' },
  { id: 'TXN_SCENARIO_C_CURRENT_1', label: 'C1 · ring' },
  { id: 'TXN_SCENARIO_C_CURRENT_2', label: 'C2' },
  { id: 'TXN_SCENARIO_C_CURRENT_3', label: 'C3' },
] as const

/** A percentage typed by a person, as the 0–1 fraction the API wants. */
function asProbability(percent: string): number | undefined {
  if (!percent.trim()) return undefined
  const value = Number(percent)
  if (!Number.isFinite(value)) return undefined
  return Math.max(0, Math.min(1, value / 100))
}

function asNumber(value: string): number | undefined {
  if (!value.trim()) return undefined
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : undefined
}

/**
 * The transaction explorer.
 *
 * Search, filtering, sorting and pagination all happen server-side. The browser
 * holds one page - never the 20,000-row table - so the view stays responsive
 * regardless of dataset size.
 *
 * Two filters are applied here rather than in SQL, and the panel says so: the
 * explorer endpoint has no amount or anomaly-score parameter, so narrowing by
 * those narrows *the current page*. Presenting that as though it filtered the
 * whole set would be a lie about what the reader is looking at.
 */
export function TransactionsPage() {
  const [page, setPage] = useState(1)
  const [searchInput, setSearchInput] = useState('')
  const [decision, setDecision] = useState('')
  const [riskLevel, setRiskLevel] = useState('')
  const [severity, setSeverity] = useState('')
  const [status, setStatus] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [merchantId, setMerchantId] = useState('')
  const [customerId, setCustomerId] = useState('')
  const [minProbability, setMinProbability] = useState('')
  const [maxProbability, setMaxProbability] = useState('')
  const [minAmount, setMinAmount] = useState('')
  const [maxAmount, setMaxAmount] = useState('')
  const [minAnomaly, setMinAnomaly] = useState('')
  const [sortBy, setSortBy] = useState('timestamp')
  const [descending, setDescending] = useState(true)
  const [advanced, setAdvanced] = useState(false)

  // Debounced so a keystroke does not become a query over 20,000 rows.
  const search = useDebounced(searchInput, 300)
  const probabilityFrom = useDebounced(minProbability, 400)
  const probabilityTo = useDebounced(maxProbability, 400)

  const query = useQuery(
    (signal) =>
      api.explorer(
        {
          page,
          page_size: PAGE_SIZE,
          search: search || undefined,
          decision: decision || undefined,
          risk_level: riskLevel || undefined,
          anomaly_severity: severity || undefined,
          status: status || undefined,
          merchant_id: merchantId || undefined,
          customer_id: customerId || undefined,
          date_from: dateFrom ? `${dateFrom}T00:00:00Z` : undefined,
          date_to: dateTo ? `${dateTo}T23:59:59Z` : undefined,
          min_probability: asProbability(probabilityFrom),
          max_probability: asProbability(probabilityTo),
          sort_by: sortBy,
          descending,
        },
        signal,
      ),
    [
      page,
      search,
      decision,
      riskLevel,
      severity,
      status,
      merchantId,
      customerId,
      dateFrom,
      dateTo,
      probabilityFrom,
      probabilityTo,
      sortBy,
      descending,
    ],
  )

  /** Client-side narrowing, applied to the page the server returned. */
  const amountFrom = asNumber(minAmount)
  const amountTo = asNumber(maxAmount)
  const anomalyFrom = asNumber(minAnomaly)
  const localFilterActive =
    amountFrom !== undefined || amountTo !== undefined || anomalyFrom !== undefined

  const rows = useMemo(() => {
    const items = query.data?.items ?? []
    if (!localFilterActive) return items
    return items.filter((row) => {
      if (amountFrom !== undefined && row.amount < amountFrom) return false
      if (amountTo !== undefined && row.amount > amountTo) return false
      if (anomalyFrom !== undefined && (row.anomaly_score ?? -1) < anomalyFrom) return false
      return true
    })
  }, [query.data, localFilterActive, amountFrom, amountTo, anomalyFrom])

  function handleSort(key: string) {
    if (key === sortBy) {
      setDescending((value) => !value)
    } else {
      setSortBy(key)
      setDescending(true)
    }
    setPage(1)
  }

  /** Any filter change resets to page 1 - staying on page 40 of a 3-row result is a dead end. */
  function updateFilter(setter: (value: string) => void) {
    return (value: string) => {
      setter(value)
      setPage(1)
    }
  }

  const activeFilters = [
    search && 'search',
    decision && 'decision',
    riskLevel && 'risk level',
    severity && 'anomaly severity',
    status && 'status',
    merchantId && 'merchant',
    customerId && 'customer',
    (dateFrom || dateTo) && 'date',
    (minProbability || maxProbability) && 'probability',
    (minAmount || maxAmount) && 'amount',
    minAnomaly && 'anomaly score',
  ].filter(Boolean) as string[]

  function clearFilters() {
    setSearchInput('')
    setDecision('')
    setRiskLevel('')
    setSeverity('')
    setStatus('')
    setMerchantId('')
    setCustomerId('')
    setDateFrom('')
    setDateTo('')
    setMinProbability('')
    setMaxProbability('')
    setMinAmount('')
    setMaxAmount('')
    setMinAnomaly('')
    setPage(1)
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Explorer"
        title="Transactions"
        description="Every payment with its model signals and policy decision. Filtering, sorting and paging happen in the database."
        meta={
          query.data ? (
            <span>
              <span className="numeric font-medium text-content-muted">
                {formatCount(query.data.meta.total_items)}
              </span>{' '}
              transactions match
              {activeFilters.length > 0 ? (
                <>
                  {' '}
                  · {activeFilters.length} filter{activeFilters.length === 1 ? '' : 's'} active
                </>
              ) : null}
            </span>
          ) : undefined
        }
      />

      <Card>
        {/* The filters people reach for constantly, always visible. */}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-5">
          <TextField
            label="Search"
            type="search"
            value={searchInput}
            placeholder="Transaction, customer or merchant"
            onChange={(event) => {
              setSearchInput(event.target.value)
              setPage(1)
            }}
            className="sm:col-span-2"
          />
          <SelectField
            label="Decision"
            value={decision}
            options={DECISIONS}
            onChange={(event) => updateFilter(setDecision)(event.target.value)}
          />
          <SelectField
            label="Risk level"
            value={riskLevel}
            options={BANDS}
            onChange={(event) => updateFilter(setRiskLevel)(event.target.value)}
          />
          <SelectField
            label="Anomaly severity"
            value={severity}
            options={BANDS}
            onChange={(event) => updateFilter(setSeverity)(event.target.value)}
          />
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3">
          <Button
            variant="ghost"
            size="sm"
            aria-expanded={advanced}
            onClick={() => setAdvanced((open) => !open)}
          >
            <span aria-hidden="true" className="text-[0.6rem]">
              {advanced ? '▾' : '▸'}
            </span>
            Advanced filters
          </Button>
          {activeFilters.length > 0 ? (
            <>
              <span className="flex flex-wrap gap-1.5">
                {activeFilters.map((name) => (
                  <Badge key={name} tone="brand">
                    {name}
                  </Badge>
                ))}
              </span>
              <Button variant="ghost" size="sm" onClick={clearFilters}>
                Clear all
              </Button>
            </>
          ) : null}

          <span className="ml-auto flex flex-wrap items-center gap-2">
            <span className="text-xs text-content-faint">Demo scenarios</span>
            {DEMO_SCENARIOS.map((scenario) => (
              <Link
                key={scenario.id}
                to={`/transactions/${scenario.id}`}
                className="rounded-md border border-border-subtle px-2 py-1 text-xs text-content-muted transition-colors hover:border-brand/40 hover:text-brand"
              >
                {scenario.label}
              </Link>
            ))}
          </span>
        </div>

        {advanced ? (
          <div className="animate-fade-in mt-3 grid gap-3 border-t border-border-subtle pt-3 sm:grid-cols-2 lg:grid-cols-4">
            <RangeField
              label="Fraud probability"
              suffix="%"
              type="number"
              min={0}
              max={100}
              step={0.5}
              from={minProbability}
              to={maxProbability}
              onFrom={updateFilter(setMinProbability)}
              onTo={updateFilter(setMaxProbability)}
              placeholderFrom="0"
              placeholderTo="100"
            />
            <RangeField
              label="Date"
              type="date"
              from={dateFrom}
              to={dateTo}
              onFrom={updateFilter(setDateFrom)}
              onTo={updateFilter(setDateTo)}
            />
            <SelectField
              label="Payment status"
              value={status}
              options={STATUSES}
              onChange={(event) => updateFilter(setStatus)(event.target.value)}
            />
            <TextField
              label="Merchant"
              value={merchantId}
              placeholder="mrc_0001"
              onChange={(event) => updateFilter(setMerchantId)(event.target.value)}
            />
            <TextField
              label="Customer"
              value={customerId}
              placeholder="cus_..."
              onChange={(event) => updateFilter(setCustomerId)(event.target.value)}
            />
            <RangeField
              label="Amount"
              type="number"
              min={0}
              from={minAmount}
              to={maxAmount}
              onFrom={setMinAmount}
              onTo={setMaxAmount}
            />
            <TextField
              label="Min anomaly score"
              type="number"
              min={0}
              max={100}
              value={minAnomaly}
              placeholder="0–100"
              onChange={(event) => setMinAnomaly(event.target.value)}
            />
            <p className="self-end text-xs leading-relaxed text-content-faint sm:col-span-2 lg:col-span-1">
              Amount and anomaly score narrow the current page only - the explorer endpoint takes
              no parameter for either. Every other filter is applied in the database.
            </p>
          </div>
        ) : null}
      </Card>

      <Card>
        <CardHeader
          title="Results"
          description={
            localFilterActive && query.data
              ? `Showing ${rows.length} of ${query.data.items.length} rows on this page after the page-level amount and anomaly filters.`
              : undefined
          }
        />
        <div className="mt-4">
          <QueryBoundary
            loading={query.loading}
            error={query.error}
            data={query.data}
            onRetry={query.refetch}
            isEmpty={() => rows.length === 0}
            emptyTitle="No transactions match these filters"
            emptyDescription="Try widening the date range or clearing a filter."
            skeleton={<TableSkeleton rows={8} columns={8} />}
          >
            {(data) => (
              <>
                <DataTable>
                  <thead>
                    <tr>
                      <SortableTh
                        label="Transaction"
                        columnKey="transaction_id"
                        activeKey={sortBy}
                        descending={descending}
                        onSort={handleSort}
                      />
                      <SortableTh
                        label="Timestamp"
                        columnKey="timestamp"
                        activeKey={sortBy}
                        descending={descending}
                        onSort={handleSort}
                      />
                      <SortableTh
                        label="Amount"
                        columnKey="amount"
                        activeKey={sortBy}
                        descending={descending}
                        onSort={handleSort}
                        numeric
                      />
                      <Th>Customer</Th>
                      <Th>Merchant</Th>
                      <SortableTh
                        label="Fraud prob."
                        columnKey="fraud_probability"
                        activeKey={sortBy}
                        descending={descending}
                        onSort={handleSort}
                        numeric
                      />
                      <SortableTh
                        label="Anomaly"
                        columnKey="anomaly_score"
                        activeKey={sortBy}
                        descending={descending}
                        onSort={handleSort}
                        numeric
                      />
                      <Th>Severity</Th>
                      <Th>Risk level</Th>
                      <Th>Decision</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <Tr key={row.transaction_id}>
                        <Td>
                          <Link
                            to={`/transactions/${encodeURIComponent(row.transaction_id)}`}
                            className="numeric text-xs font-medium text-brand hover:underline"
                          >
                            {row.transaction_id}
                          </Link>
                        </Td>
                        <Td className="numeric text-xs whitespace-nowrap text-content-muted">
                          {formatDateTime(row.timestamp)}
                        </Td>
                        <Td numeric>{formatAmount(row.amount, row.currency)}</Td>
                        <Td className="numeric text-xs text-content-muted">{row.customer_id}</Td>
                        <Td className="text-content-muted">{row.merchant_name}</Td>
                        <Td numeric className="font-semibold">
                          {formatProbability(row.fraud_probability)}
                        </Td>
                        <Td numeric>{row.anomaly_score ?? '--'}</Td>
                        <Td>
                          <SeverityBadge severity={row.anomaly_severity} />
                        </Td>
                        <Td>
                          {row.risk_level ? (
                            <span
                              className={cn(
                                'text-xs font-semibold',
                                toneClasses(severityTone(row.risk_level)).text,
                              )}
                            >
                              {row.risk_level}
                            </span>
                          ) : (
                            <span className="text-content-faint">--</span>
                          )}
                        </Td>
                        <Td>
                          <DecisionBadge decision={row.decision} />
                        </Td>
                      </Tr>
                    ))}
                  </tbody>
                </DataTable>
                <Pagination meta={data.meta} onPageChange={setPage} itemLabel="transactions" />
              </>
            )}
          </QueryBoundary>
        </div>
      </Card>
    </div>
  )
}
