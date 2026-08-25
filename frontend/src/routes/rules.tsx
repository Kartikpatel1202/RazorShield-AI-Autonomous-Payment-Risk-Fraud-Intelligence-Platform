import { Badge, DecisionBadge } from '@/components/ui/badge'
import { Card, CardDescription, CardTitle } from '@/components/ui/card'
import { PageHeader } from '@/components/ui/page-header'
import { QueryBoundary } from '@/components/ui/states'
import { DataTable, Td, Th, Tr } from '@/components/ui/table'
import { useQuery } from '@/hooks/use-query'
import { api } from '@/lib/api'
import { humanizeCode } from '@/lib/format'

function ThresholdRow({
  label,
  value,
  note,
}: {
  label: string
  value: number
  note: string
}) {
  return (
    <Tr>
      <Td className="numeric text-xs">{label}</Td>
      <Td numeric className="font-semibold">
        {value}
      </Td>
      <Td className="text-content-muted">{note}</Td>
    </Tr>
  )
}

/**
 * The policy viewer.
 *
 * Read-only, and it says so. Phase 7 exposes no write path at all: changing
 * policy means changing a reviewed, versioned file, not submitting a form from
 * a dashboard.
 */
export function RulesPage() {
  const query = useQuery((signal) => api.policy(signal), [])

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Assurance"
        title="Decision policy"
        description="What the engine is currently enforcing. Rule logic is typed code; thresholds and enablement are versioned configuration."
      />

      <QueryBoundary
        loading={query.loading}
        error={query.error}
        data={query.data}
        onRetry={query.refetch}
        loadingRows={8}
      >
        {(policy) => (
          <div className="flex flex-col gap-5">
            <Card>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <CardTitle>{policy.policy_version}</CardTitle>
                  <CardDescription>{policy.description}</CardDescription>
                </div>
                <div className="flex items-center gap-2">
                  <Badge tone="neutral">{policy.source}</Badge>
                  <Badge tone="positive" glyph="🔒">
                    READ-ONLY
                  </Badge>
                </div>
              </div>
              <p className="mt-3 text-sm text-content-muted">
                Editing is deliberately not available here. A policy change is a reviewed change to
                a versioned file, so every decision can be traced to a policy that existed in
                source control when it was made.
              </p>
            </Card>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardTitle>Thresholds</CardTitle>
                <CardDescription>
                  Every value is a measured operating point, not a round number.
                </CardDescription>
                <div className="mt-4">
                  <DataTable className="min-w-[26rem]">
                    <thead>
                      <tr>
                        <Th>Threshold</Th>
                        <Th numeric>Value</Th>
                        <Th>Meaning</Th>
                      </tr>
                    </thead>
                    <tbody>
                      <ThresholdRow
                        label="fraud_block"
                        value={policy.thresholds.fraud_block}
                        note="Block band (with corroboration)"
                      />
                      <ThresholdRow
                        label="fraud_high"
                        value={policy.thresholds.fraud_high}
                        note="High supervised risk"
                      />
                      <ThresholdRow
                        label="fraud_medium"
                        value={policy.thresholds.fraud_medium}
                        note="Moderate supervised risk"
                      />
                      <ThresholdRow
                        label="anomaly_critical"
                        value={policy.thresholds.anomaly_critical}
                        note="Critical behavioural anomaly"
                      />
                      <ThresholdRow
                        label="anomaly_high"
                        value={policy.thresholds.anomaly_high}
                        note="High behavioural anomaly"
                      />
                      <ThresholdRow
                        label="anomaly_medium"
                        value={policy.thresholds.anomaly_medium}
                        note="Elevated behavioural anomaly"
                      />
                    </tbody>
                  </DataTable>
                </div>
              </Card>

              <Card>
                <CardTitle>Action precedence</CardTitle>
                <CardDescription>
                  When several rules match, the most restrictive wins - but precedence never
                  creates a match.
                </CardDescription>
                <ol className="mt-4 flex flex-wrap items-center gap-2">
                  {policy.action_precedence.map((action, index) => (
                    <li key={action} className="flex items-center gap-2">
                      <DecisionBadge decision={action} />
                      {index < policy.action_precedence.length - 1 ? (
                        <span aria-hidden="true" className="text-content-faint">
                          &gt;
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ol>

                <div className="mt-5 flex flex-col gap-3">
                  <div>
                    <p className="text-xs font-medium tracking-wide text-content-muted uppercase">
                      Default action
                    </p>
                    <div className="mt-1">
                      <DecisionBadge decision={policy.default_action} />
                    </div>
                  </div>
                  <div>
                    <p className="text-xs font-medium tracking-wide text-content-muted uppercase">
                      Requires human review
                    </p>
                    <div className="mt-1 flex gap-1.5">
                      {policy.human_review_required_for.map((action) => (
                        <DecisionBadge key={action} decision={action} />
                      ))}
                    </div>
                  </div>
                </div>

                <div className="mt-5 border-t border-border-subtle pt-4">
                  <p className="text-xs font-medium tracking-wide text-content-muted uppercase">
                    Evidence requirements
                  </p>
                  <dl className="mt-2 flex flex-col gap-1 text-sm">
                    <div className="flex justify-between gap-2">
                      <dt className="text-content-muted">Independent sources for a block</dt>
                      <dd className="numeric">
                        {policy.evidence.min_independent_sources_for_block}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt className="text-content-muted">High findings for review</dt>
                      <dd className="numeric">{policy.evidence.min_high_findings_for_review}</dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt className="text-content-muted">Minimum investigation confidence</dt>
                      <dd className="numeric">
                        {policy.evidence.min_investigation_confidence}
                      </dd>
                    </div>
                  </dl>
                </div>
              </Card>
            </div>

            <Card>
              <CardTitle>Fail-safe behaviour</CardTitle>
              <CardDescription>
                A missing signal is an unknown, never a clean bill. The configuration layer refuses
                to load a policy that approves on one.
              </CardDescription>
              <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {[
                  ['Supervised signal missing', policy.fail_safe.missing_supervised_signal],
                  ['Anomaly signal missing', policy.fail_safe.missing_anomaly_signal],
                  ['Investigation missing', policy.fail_safe.missing_investigation],
                ].map(([label, action]) => (
                  <div
                    key={label}
                    className="rounded-lg border border-border-subtle p-3"
                  >
                    <p className="text-xs text-content-muted">{label}</p>
                    <div className="mt-1.5">
                      <DecisionBadge decision={action} />
                    </div>
                  </div>
                ))}
                <div className="rounded-lg border border-border-subtle p-3">
                  <p className="text-xs text-content-muted">Block without investigation</p>
                  <div className="mt-1.5">
                    <Badge
                      tone={policy.fail_safe.require_investigation_for_block ? 'positive' : 'danger'}
                    >
                      {policy.fail_safe.require_investigation_for_block
                        ? 'WITHHELD'
                        : 'PERMITTED'}
                    </Badge>
                  </div>
                </div>
              </div>
            </Card>

            <Card>
              <CardTitle>Rules ({policy.rules.length})</CardTitle>
              <CardDescription>
                Configuration selects among these; it cannot invent new ones.
              </CardDescription>
              <div className="mt-4">
                <DataTable>
                  <thead>
                    <tr>
                      <Th>Rule</Th>
                      <Th>Action</Th>
                      <Th>Enabled</Th>
                      <Th>Description</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {policy.rules.map((rule) => (
                      <Tr key={rule.rule_id}>
                        <Td className="numeric text-xs font-semibold">{rule.rule_id}</Td>
                        <Td className="text-xs text-content-muted">{rule.action}</Td>
                        <Td>
                          <Badge tone={rule.enabled ? 'positive' : 'neutral'}>
                            {rule.enabled ? 'ON' : 'OFF'}
                          </Badge>
                        </Td>
                        <Td className="text-content-muted">{rule.description}</Td>
                      </Tr>
                    ))}
                  </tbody>
                </DataTable>
              </div>
            </Card>

            <Card>
              <CardTitle>Reason codes ({policy.reason_codes.length})</CardTitle>
              <CardDescription>
                Stable identifiers emitted by specific rules. Append-only: renaming one would
                change the meaning of historical decisions.
              </CardDescription>
              <div className="mt-4 flex flex-wrap gap-1.5">
                {policy.reason_codes.map((code) => (
                  <Badge key={code} tone="neutral" title={code}>
                    {humanizeCode(code)}
                  </Badge>
                ))}
              </div>
            </Card>
          </div>
        )}
      </QueryBoundary>
    </div>
  )
}
