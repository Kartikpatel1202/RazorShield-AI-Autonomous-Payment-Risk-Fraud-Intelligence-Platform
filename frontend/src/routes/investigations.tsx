import { useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge, SeverityBadge } from '@/components/ui/badge'
import { Card, CardDescription, CardTitle } from '@/components/ui/card'
import { PageHeader } from '@/components/ui/page-header'
import { Pagination } from '@/components/ui/pagination'
import { QueryBoundary } from '@/components/ui/states'
import { DataTable, Td, Th, Tr } from '@/components/ui/table'
import { useQuery } from '@/hooks/use-query'
import { api } from '@/lib/api'
import { formatDateTime, formatPercent } from '@/lib/format'

const PAGE_SIZE = 20

/** Narrow an audit event's untyped document without pretending it is typed. */
function readString(data: Record<string, unknown>, key: string): string | null {
  const value = data[key]
  return typeof value === 'string' ? value : null
}

function readNumber(data: Record<string, unknown>, key: string): number | null {
  const value = data[key]
  return typeof value === 'number' ? value : null
}

/**
 * Completed investigations.
 *
 * Sourced from the audit trail's `investigation.completed` events rather than a
 * bespoke endpoint: the trail already records the identifier, outcome,
 * confidence and tool count for every investigation the agent finished, and
 * inventing a parallel listing would be a second source of truth for the same
 * facts.
 */
export function InvestigationsPage() {
  const [page, setPage] = useState(1)

  const query = useQuery(
    (signal) =>
      api.audit(
        { page, page_size: PAGE_SIZE, event_type: 'investigation.completed' },
        signal,
      ),
    [page],
  )

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Casework"
        title="Investigations"
        description="Evidence-grounded agent reports. The agent investigates and explains; the policy engine decides. Open a transaction to read its findings and evidence in full."
      />

      <Card>
        <CardTitle>Completed investigations</CardTitle>
        <CardDescription>
          Recorded by the agent at completion. Investigations produced by the deterministic mock
          provider are flagged as such and never presented as live model output.
        </CardDescription>
        <div className="mt-4">
          <QueryBoundary
            loading={query.loading}
            error={query.error}
            data={query.data}
            onRetry={query.refetch}
            isEmpty={(data) => data.items.length === 0}
            emptyTitle="No investigations yet"
            emptyDescription="Run an investigation from the API to populate this list."
            loadingRows={6}
          >
            {(data) => (
              <>
                <DataTable>
                  <thead>
                    <tr>
                      <Th>Investigation</Th>
                      <Th>Transaction</Th>
                      <Th>Risk level</Th>
                      <Th numeric>Confidence</Th>
                      <Th numeric>Evidence</Th>
                      <Th numeric>Findings</Th>
                      <Th>Provider</Th>
                      <Th>Completed</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((entry) => {
                      const event = entry.event_data
                      const isMock = event.llm_is_mock === true
                      return (
                        <Tr key={entry.audit_id}>
                          <Td className="numeric text-xs">
                            {entry.investigation_id ?? readString(event, 'investigation_id') ?? '--'}
                          </Td>
                          <Td>
                            {entry.transaction_id ? (
                              <Link
                                to={`/transactions/${encodeURIComponent(entry.transaction_id)}`}
                                className="numeric text-brand hover:underline"
                              >
                                {entry.transaction_id}
                              </Link>
                            ) : (
                              <span className="text-content-faint">--</span>
                            )}
                          </Td>
                          <Td>
                            <SeverityBadge severity={readString(event, 'risk_level')} />
                          </Td>
                          <Td numeric>{formatPercent(readNumber(event, 'confidence'))}</Td>
                          <Td numeric>{readNumber(event, 'evidence_count') ?? '--'}</Td>
                          <Td numeric>{readNumber(event, 'finding_count') ?? '--'}</Td>
                          <Td>
                            <Badge tone={isMock ? 'warning' : 'neutral'}>
                              {isMock ? 'MOCK' : (readString(event, 'llm_provider') ?? 'unknown')}
                            </Badge>
                          </Td>
                          <Td className="whitespace-nowrap text-content-muted">
                            {formatDateTime(entry.created_at)}
                          </Td>
                        </Tr>
                      )
                    })}
                  </tbody>
                </DataTable>
                <Pagination meta={data.meta} onPageChange={setPage} itemLabel="investigations" />
              </>
            )}
          </QueryBoundary>
        </div>
      </Card>
    </div>
  )
}
