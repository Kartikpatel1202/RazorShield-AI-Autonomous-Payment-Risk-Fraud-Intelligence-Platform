/**
 * Typed access to the RazorShield API.
 *
 * Every response type here mirrors a Pydantic model on the backend. The console
 * renders what the API returns and computes no risk figures of its own - if a
 * number appears on screen, it came from a database query.
 */
import { clearSession, loadSession } from '@/lib/auth'
import { appConfig } from '@/lib/config'

export class ApiError extends Error {
  /** Declared and assigned explicitly: `erasableSyntaxOnly` forbids TypeScript
      parameter properties, which are not valid JavaScript on their own. */
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/**
 * Listeners notified when the server rejects our credential.
 *
 * The API module cannot navigate - it knows nothing about React or the router -
 * so it announces the fact and the auth provider decides what to do. Without
 * this, an expired token shows up as an error banner on every panel of the page
 * at once instead of a redirect to the login form.
 */
type UnauthorizedListener = () => void
const unauthorizedListeners = new Set<UnauthorizedListener>()

export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener)
  return () => unauthorizedListeners.delete(listener)
}

function notifyUnauthorized(): void {
  clearSession()
  for (const listener of unauthorizedListeners) listener()
}

/** Request headers including the bearer token, when we hold one. */
export function authHeaders(base: Record<string, string> = {}): Record<string, string> {
  const session = loadSession()
  // Read at call time rather than captured at module load: a login that happens
  // after the first import must affect the very next request.
  return session ? { ...base, authorization: `Bearer ${session.access_token}` } : { ...base }
}

/** Turn a non-2xx response into an ApiError carrying the server's own message. */
async function toApiError(response: Response): Promise<ApiError> {
  if (response.status === 401) notifyUnauthorized()

  let detail = `Request failed (${response.status})`
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') detail = body.detail
  } catch {
    /* response had no JSON body; keep the status message */
  }
  return new ApiError(detail, response.status)
}

/** Query values, with `undefined` meaning "omit this filter entirely". */
export type QueryParams = Record<string, string | number | boolean | undefined | null>

function buildUrl(path: string, params?: QueryParams): string {
  const url = new URL(path, appConfig.apiBaseUrl)
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, String(value))
      }
    }
  }
  return url.toString()
}

export async function apiGet<T>(
  path: string,
  params?: QueryParams,
  signal?: AbortSignal,
): Promise<T> {
  // `exactOptionalPropertyTypes` means an explicit `undefined` is not the same
  // as an absent key, and RequestInit.signal accepts `AbortSignal | null`.
  const response = await fetch(buildUrl(path, params), {
    signal: signal ?? null,
    headers: authHeaders({ accept: 'application/json' }),
  })

  // Surface the server's own message when it sends one; a generic "request
  // failed" hides the 422 that says exactly which filter was wrong.
  if (!response.ok) throw await toApiError(response)

  return (await response.json()) as T
}

export async function apiPost<T>(
  path: string,
  body: unknown,
  options: { readonly anonymous?: boolean } = {},
): Promise<T> {
  const headers = { 'content-type': 'application/json', accept: 'application/json' }
  const response = await fetch(buildUrl(path), {
    method: 'POST',
    // `anonymous` is for the login call itself, which has no token to send and
    // whose 401 means "wrong password", not "session expired" - firing the
    // unauthorized listeners there would bounce the user off the form they are
    // standing on.
    headers: options.anonymous ? headers : authHeaders(headers),
    body: JSON.stringify(body),
  })

  if (!response.ok) {
    if (options.anonymous) {
      let detail = `Request failed (${response.status})`
      try {
        const parsed = (await response.json()) as { detail?: unknown }
        if (typeof parsed.detail === 'string') detail = parsed.detail
      } catch {
        /* no JSON body */
      }
      throw new ApiError(detail, response.status)
    }
    throw await toApiError(response)
  }

  return (await response.json()) as T
}

// --------------------------------------------------------------------------
// Authentication
// --------------------------------------------------------------------------

export interface LoginResponse {
  access_token: string
  token_type: string
  expires_at: string
  user: {
    id: number
    email: string
    full_name: string | null
    role: string
    is_active: boolean
  }
  role: string
  permissions: string[]
}

export interface SessionResponse {
  user: LoginResponse['user']
  role: string
  permissions: string[]
}

export interface SignupResponse {
  status: string
  detail: string
  user: LoginResponse['user']
}

export interface ForgotPasswordResponse {
  status: string
  detail: string
  /**
   * Local development only.
   *
   * The backend has no SMTP integration, so with `AUTH_EXPOSE_DEV_RESET_TOKEN`
   * on it returns the reset link here instead of emailing it. The configuration
   * refuses that flag in production, so this is always null there - the console
   * shows the link when it is present and says nothing about it when it is not.
   */
  dev_reset_url: string | null
  dev_expires_at: string | null
}

export interface ResetPasswordResponse {
  status: string
  detail: string
}

export interface PasswordPolicy {
  min_length: number
  max_bytes: number
  guidance: string[]
}

// --------------------------------------------------------------------------
// Shared shapes
// --------------------------------------------------------------------------

export interface PageMeta {
  page: number
  page_size: number
  total_items: number
  total_pages: number
  has_next: boolean
  has_previous: boolean
}

export interface Page<T> {
  items: T[]
  meta: PageMeta
}

export interface Bucket {
  label: string
  count: number
  lower: number | null
  upper: number | null
}

// --------------------------------------------------------------------------
// Analytics
// --------------------------------------------------------------------------

export interface Overview {
  total_transactions: number
  decided_transactions: number
  approved: number
  step_up: number
  review: number
  blocked: number
  high_risk_transactions: number
  critical_anomalies: number
  open_review_cases: number
  escalated_review_cases: number
  completed_investigations: number
  avg_decision_latency_ms: number | null
  min_decision_latency_ms: number | null
  max_decision_latency_ms: number | null
  latency_sample_size: number
  policy_version: string
  high_risk_threshold: number
  critical_anomaly_threshold: number
  data_from: string | null
  data_to: string | null
}

export interface RiskDistribution {
  decisions: Bucket[]
  fraud_probability: Bucket[]
  anomaly_severity: Bucket[]
  risk_level: Bucket[]
  policy_version: string
}

export interface TrendPoint {
  day: string
  volume: number
  high_risk: number
  review: number
  blocked: number
  step_up: number
  approved: number
}

export interface Trends {
  window_days: number
  points: TrendPoint[]
  data_from: string | null
  data_to: string | null
}

export interface TopRiskItem {
  transaction_id: string
  timestamp: string
  amount: number
  currency: string
  merchant_name: string
  customer_id: string
  decision: string
  fraud_probability: number | null
  anomaly_score: number | null
  anomaly_severity: string | null
}

export interface DecisionAnalytics {
  distribution: Bucket[]
  reason_codes: Bucket[]
  policy_version: string
  decided_transactions: number
}

// --------------------------------------------------------------------------
// Explorer and detail
// --------------------------------------------------------------------------

export interface ExplorerRow {
  transaction_id: string
  timestamp: string
  amount: number
  currency: string
  status: string
  is_fraud: boolean
  customer_id: string
  merchant_id: string
  merchant_name: string
  fraud_probability: number | null
  risk_score: number | null
  risk_level: string | null
  anomaly_score: number | null
  anomaly_severity: string | null
  decision: string | null
  policy_version: string | null
  requires_human_review: boolean | null
}

export interface Finding {
  finding_id: string
  title: string
  severity: string
  explanation: string
  evidence_ids: string[]
}

export interface Evidence {
  evidence_id: string
  source_tool: string
  claim: string
  value: number | null
  severity: string
  transaction_id: string
  observed_before: string
  details: Record<string, unknown>
}

export interface RuleMatch {
  rule_id: string
  action: string
  reason_codes: string[]
  conditions: string[]
}

export interface TransactionDetail {
  transaction: {
    transaction_id: string
    timestamp: string
    amount: number
    currency: string
    status: string
    payment_method: string
    is_fraud: boolean
    failed_attempts: number
    country: string
    city: string
    customer_id: string
    customer_country: string
    customer_historical_risk_level: string
    customer_since: string | null
    merchant_id: string
    merchant_name: string
    merchant_category: string
    device_id: string | null
    device_type: string | null
    ip_address: string | null
    ip_country: string | null
    ip_is_proxy: boolean | null
  }
  signals: {
    fraud_probability: number | null
    risk_score: number | null
    fraud_model_version: string | null
    risk_level: string | null
    anomaly_score: number | null
    anomaly_severity: string | null
    anomaly_model_version: string | null
    customer_deviation_score: number | null
  }
  investigation: {
    investigation_id: string | null
    status: string
    risk_level: string | null
    confidence: number | null
    summary: string | null
    recommended_action: string | null
    agent_is_mock: boolean
    iteration_count: number
    started_at: string | null
    completed_at: string | null
    findings: Finding[]
    evidence: Evidence[]
    confidence_basis: Record<string, unknown> | null
    trace: Record<string, unknown> | null
  } | null
  decision: {
    decision_id: string
    decision: string
    policy_version: string
    decided_at: string
    matched_rules: string[]
    deciding_rules: string[]
    reason_codes: string[]
    rule_matches: RuleMatch[]
    explanation: string
    requires_human_review: boolean
    input_digest: string
    evaluation_ms: number | null
    history_count: number
  } | null
  review: {
    review_case_id: number
    status: string
    resolution: string | null
    resolution_reason: string | null
    created_at: string
    resolved_at: string | null
  } | null
  audit: AuditEntry[]
}

// --------------------------------------------------------------------------
// Reviews, audit, policy, health
// --------------------------------------------------------------------------

export interface ReviewDecisionSummary {
  decision_id: string
  decision: string
  policy_version: string
  matched_rules: string[]
  reason_codes: string[]
  requires_human_review: boolean
  fraud_probability: number | null
  anomaly_score: number | null
  investigation_id: string | null
  decided_at: string
}

export interface ReviewCase {
  review_case_id: number
  transaction_id: string
  status: string
  reason: string | null
  created_at: string
  resolved_at: string | null
  resolution: string | null
  resolution_reason: string | null
  assigned_to: number | null
  decision: ReviewDecisionSummary | null
}

export interface ResolveReviewResponse {
  review_case_id: number
  transaction_id: string
  status: string
  resolution: string
  resolution_reason: string | null
  resolved_at: string | null
  machine_decision: string | null
  machine_decision_id: string | null
  overrides_machine_decision: boolean
}

export interface AuditEntry {
  audit_id: number
  event_type: string
  actor_type: string
  actor_id: string | null
  transaction_id: string | null
  created_at: string
  decision: string | null
  decision_id: string | null
  policy_version: string | null
  investigation_id: string | null
  resolution: string | null
  event_data: Record<string, unknown>
}

export interface AuditSummary {
  counts: Record<string, number>
  known_event_types: string[]
}

export interface PolicyRule {
  rule_id: string
  action: string
  enabled: boolean
  description: string
}

export interface Policy {
  policy_version: string
  description: string
  source: string
  thresholds: {
    fraud_block: number
    fraud_high: number
    fraud_medium: number
    anomaly_critical: number
    anomaly_high: number
    anomaly_medium: number
  }
  evidence: {
    min_independent_sources_for_block: number
    min_high_findings_for_review: number
    min_investigation_confidence: number
  }
  fail_safe: {
    missing_supervised_signal: string
    missing_anomaly_signal: string
    missing_investigation: string
    require_investigation_for_block: boolean
  }
  action_precedence: string[]
  default_action: string
  human_review_required_for: string[]
  rules: PolicyRule[]
  reason_codes: string[]
  editable: boolean
}

export interface ComponentHealth {
  name: string
  status: string
  detail: string | null
  version: string | null
}

export interface SystemHealth {
  status: string
  components: ComponentHealth[]
}


// --------------------------------------------------------------------------
// Phase 8: feedback, monitoring and the assistant
// --------------------------------------------------------------------------

export interface FeedbackRecord {
  feedback_id: string
  transaction_id: string
  decision_id: string | null
  review_case_id: number | null
  analyst_id: number | null
  outcome: string
  reason_code: string
  notes: string | null
  machine_decision: string | null
  policy_version: string | null
  created_at: string
}

export interface ReasonBucket {
  reason_code: string
  count: number
}

export interface FeedbackSummary {
  total_feedback: number
  confirmed_fraud: number
  legitimate: number
  false_positive: number
  false_negative: number
  insufficient_evidence: number
  escalated: number
  ground_truth_labels: number
  total_transactions: number
  total_review_cases: number
  labelled_share_of_transactions: number
  by_reason: ReasonBucket[]
}

export interface ConfusionCell {
  machine_decision: string
  outcome: string
  actually_fraud: boolean
  count: number
}

export interface ConfusionMatrix {
  cells: ConfusionCell[]
  machine_actions: string[]
  true_positive: number
  false_negative: number
  false_positive: number
  true_negative: number
  labelled_included: number
  excluded_open_outcomes: number
}

export interface FeedbackSummaryResponse {
  summary: FeedbackSummary
  confusion_matrix: ConfusionMatrix
}

export interface ModelMetrics {
  sufficient: boolean
  message: string | null
  selection_bias_note: string | null
  labelled_flagged: number | null
  labelled_unflagged: number | null
  precision: number | null
  recall: number | null
  f1: number | null
  false_positive_rate: number | null
  false_negative_rate: number | null
  true_positive: number | null
  false_positive: number | null
  true_negative: number | null
  false_negative: number | null
  labelled_samples: number
  total_feedback: number
  open_outcome_labels: number
  unlabelled_transactions: number
  total_transactions: number
  minimum_required: number
  label_source: string
}

export interface LabelCoverage {
  total_transactions: number
  confirmed_labels: number
  analyst_feedback_total: number
  open_outcome_labels: number
  unlabelled: number
  simulated_fraud_flags: number
  simulated_label_note: string
}

export interface ModelMonitoring {
  metrics: ModelMetrics
  coverage: LabelCoverage
}

export interface WindowSummary {
  from: string
  to: string
  scored_transactions: number
  mean_fraud_probability: number | null
  high_risk_count: number
  high_risk_percent: number | null
  anomaly_scored_transactions: number
  mean_anomaly_score: number | null
  critical_anomaly_count: number
  critical_anomaly_percent: number | null
}

export interface ScoreWindows {
  baseline: WindowSummary | null
  current: WindowSummary | null
  high_risk_threshold: number | null
  critical_anomaly_threshold: number | null
  thresholds: Record<string, number>
}

export interface DriftFeature {
  feature: string
  kind: string
  psi: number | null
  status: string
  baseline_count: number
  current_count: number
  baseline_mean: number | null
  current_mean: number | null
}

export interface DriftReport {
  features: DriftFeature[]
  baseline_from: string | null
  baseline_to: string | null
  current_from: string | null
  current_to: string | null
  thresholds: Record<string, number>
  note: string
}

export interface RulePerformance {
  rule_id: string
  description: string
  primary_action: string
  triggers: number
  approve_count: number
  step_up_count: number
  review_count: number
  block_count: number
  resolved_count: number
  override_count: number
  override_rate: number | null
  override_rate_reportable: boolean
  flagged_high_override: boolean
}

export interface PolicyEffectiveness {
  rules: RulePerformance[]
  high_override_threshold: number
  min_rule_triggers: number
  policy_version: string
  override_note: string
}

export interface FunnelStage {
  stage: string
  count: number
  description: string
}

export interface HighRiskFunnel {
  stages: FunnelStage[]
  withheld_pending_investigation: number
  final_actions: Record<string, number>
  block_threshold: number
  min_independent_sources: number
  policy_version: string
  explanation: string
}

export interface Recommendation {
  id: string
  severity: string
  title: string
  detail: string
  metric_source: string
  action_required: string
}

export interface RecommendationsResponse {
  recommendations: Recommendation[]
  note: string
}

export interface AssistantQuestion {
  topic: string
  question: string
}

export interface AssistantQuestionsResponse {
  questions: AssistantQuestion[]
  note: string
}

export interface AssistantAnswer {
  topic: string
  question: string
  answer: string
  metric_sources: string[]
  time_window: string
  data_availability: string
  sufficient: boolean
  figures: Record<string, unknown>
}

export interface FeedbackCreate {
  transaction_id: string
  outcome: string
  reason_code: string
  notes?: string | undefined
  analyst_id?: number | undefined
  review_case_id?: number | undefined
}

// --------------------------------------------------------------------------
// Endpoint helpers
// --------------------------------------------------------------------------

// --------------------------------------------------------------------------
// Phase 9 - live stream and simulator
// --------------------------------------------------------------------------

/** One event from the live pipeline. Ordering is by `sequence`. */
export interface RiskEvent {
  event_id: string
  sequence: number
  transaction_id: string
  event_type: string
  transaction_sequence: number
  timestamp: string
  payload: Record<string, unknown>
}

export interface EventPage {
  events: RiskEvent[]
  latest_sequence: number
}

export interface SimulatorDecisionCounts {
  approve: number
  step_up: number
  review: number
  block: number
}

export interface SimulatorRecentResult {
  transaction_id: string
  decision: string | null
  fraud_probability: number | null
  anomaly_score: number | null
  investigated: boolean
  duplicate: boolean
  error: string | null
  total_ms: number
}

export interface SimulatorStatus {
  state: string
  run_id: string | null
  scenario: string | null
  transactions_per_second: number | null
  max_transactions: number | null
  seed: number | null
  generated: number
  processed: number
  duplicates: number
  failed: number
  queue_depth: number
  queue_capacity: number
  observed_tps: number
  latency_p50_ms: number | null
  latency_p95_ms: number | null
  started_at: string | null
  stopped_at: string | null
  uptime_seconds: number | null
  decisions: SimulatorDecisionCounts
  investigations: number
  recent: SimulatorRecentResult[]
}

export interface ScenarioRead {
  scenario: string
  title: string
  behaviour: string
  expected_signal: string
}

export interface ScenarioListResponse {
  scenarios: ScenarioRead[]
  note: string
}

export interface LiveMetrics {
  transactions_processed: number
  transactions_per_second: number
  high_risk_count: number
  review_count: number
  block_count: number
  approve_count: number
  step_up_count: number
  active_investigations: number
  queue_depth: number
  queue_capacity: number
  uptime_seconds: number | null
  simulator_state: string
  scenario: string | null
  connected_clients: number
  dropped_deliveries: number
  total_events: number
  latest_sequence: number
}

export interface SimulatorStartRequest {
  scenario: string
  transactions_per_second: number
  max_transactions: number
  seed: number
}

export const api = {
  login: (email: string, password: string) =>
    apiPost<LoginResponse>('/api/auth/login', { email, password }, { anonymous: true }),
  session: (signal?: AbortSignal) => apiGet<SessionResponse>('/api/auth/me', undefined, signal),
  logout: () => apiPost<{ status: string; detail: string }>('/api/auth/logout', {}),

  // Every call below is `anonymous`: they are reached before a session exists,
  // and their 4xx responses mean "that address is taken" or "that link is
  // spent", never "your session expired". Routing them through the normal path
  // would fire the unauthorized listeners and bounce the user off the form they
  // are standing on.
  signup: (body: { full_name: string; email: string; password: string }) =>
    apiPost<SignupResponse>('/api/auth/signup', body, { anonymous: true }),
  forgotPassword: (email: string) =>
    apiPost<ForgotPasswordResponse>('/api/auth/forgot-password', { email }, { anonymous: true }),
  resetPassword: (token: string, password: string) =>
    apiPost<ResetPasswordResponse>(
      '/api/auth/reset-password',
      { token, password },
      { anonymous: true },
    ),
  passwordPolicy: (signal?: AbortSignal) =>
    apiGet<PasswordPolicy>('/api/auth/password-policy', undefined, signal),

  overview: (signal?: AbortSignal) => apiGet<Overview>('/api/analytics/overview', undefined, signal),
  riskDistribution: (signal?: AbortSignal) =>
    apiGet<RiskDistribution>('/api/analytics/risk-distribution', undefined, signal),
  decisionAnalytics: (signal?: AbortSignal) =>
    apiGet<DecisionAnalytics>('/api/analytics/decisions', undefined, signal),
  trends: (days: number, signal?: AbortSignal) =>
    apiGet<Trends>('/api/analytics/trends', { days }, signal),
  topRisk: (limit: number, signal?: AbortSignal) =>
    apiGet<{ items: TopRiskItem[] }>('/api/analytics/top-risk', { limit }, signal),

  explorer: (params: QueryParams, signal?: AbortSignal) =>
    apiGet<Page<ExplorerRow>>('/api/transactions/explorer', params, signal),
  transactionDetail: (id: string, signal?: AbortSignal) =>
    apiGet<TransactionDetail>(`/api/transactions/${encodeURIComponent(id)}/detail`, undefined, signal),

  reviews: (params: QueryParams, signal?: AbortSignal) =>
    apiGet<Page<ReviewCase>>('/api/reviews', params, signal),
  resolveReview: (id: number, body: { resolution: string; reason?: string | undefined }) =>
    apiPost<ResolveReviewResponse>(`/api/reviews/${id}/resolve`, body),

  audit: (params: QueryParams, signal?: AbortSignal) =>
    apiGet<Page<AuditEntry>>('/api/audit', params, signal),
  auditSummary: (signal?: AbortSignal) =>
    apiGet<AuditSummary>('/api/audit/summary', undefined, signal),

  policy: (signal?: AbortSignal) => apiGet<Policy>('/api/policy', undefined, signal),

  feedback: (params: QueryParams, signal?: AbortSignal) =>
    apiGet<Page<FeedbackRecord>>('/api/feedback', params, signal),
  feedbackSummary: (signal?: AbortSignal) =>
    apiGet<FeedbackSummaryResponse>('/api/feedback/summary', undefined, signal),
  createFeedback: (body: FeedbackCreate) => apiPost<FeedbackRecord>('/api/feedback', body),

  modelMonitoring: (signal?: AbortSignal) =>
    apiGet<ModelMonitoring>('/api/monitoring/models', undefined, signal),
  scoreWindows: (signal?: AbortSignal) =>
    apiGet<ScoreWindows>('/api/monitoring/scores', undefined, signal),
  drift: (signal?: AbortSignal) => apiGet<DriftReport>('/api/monitoring/drift', undefined, signal),
  policyEffectiveness: (signal?: AbortSignal) =>
    apiGet<PolicyEffectiveness>('/api/monitoring/policy', undefined, signal),
  highRiskFunnel: (signal?: AbortSignal) =>
    apiGet<HighRiskFunnel>('/api/monitoring/high-risk-funnel', undefined, signal),
  recommendations: (signal?: AbortSignal) =>
    apiGet<RecommendationsResponse>('/api/monitoring/recommendations', undefined, signal),

  assistantQuestions: (signal?: AbortSignal) =>
    apiGet<AssistantQuestionsResponse>('/api/assistant/questions', undefined, signal),
  assistantAnswer: (topic: string, signal?: AbortSignal) =>
    apiGet<AssistantAnswer>('/api/assistant/answer', { topic }, signal),
  systemHealth: (signal?: AbortSignal) =>
    apiGet<SystemHealth>('/api/system/health', undefined, signal),

  liveMetrics: (signal?: AbortSignal) =>
    apiGet<LiveMetrics>('/api/live/metrics', undefined, signal),
  events: (params: QueryParams, signal?: AbortSignal) =>
    apiGet<EventPage>('/api/events', params, signal),
  simulatorStatus: (signal?: AbortSignal) =>
    apiGet<SimulatorStatus>('/api/simulator/status', undefined, signal),
  simulatorScenarios: (signal?: AbortSignal) =>
    apiGet<ScenarioListResponse>('/api/simulator/scenarios', undefined, signal),
  simulatorStart: (body: SimulatorStartRequest) =>
    apiPost<SimulatorStatus>('/api/simulator/start', body),
  simulatorStop: () => apiPost<SimulatorStatus>('/api/simulator/stop', {}),
  simulatorPause: () => apiPost<SimulatorStatus>('/api/simulator/pause', {}),
  simulatorResume: () => apiPost<SimulatorStatus>('/api/simulator/resume', {}),
  simulatorReset: () => apiPost<SimulatorStatus>('/api/simulator/reset', {}),
}

/** Absolute URL for the SSE endpoint; EventSource cannot use a relative path. */
export function eventStreamUrl(lastSequence?: number): string {
  const url = new URL('/api/events/stream', appConfig.apiBaseUrl)
  if (lastSequence !== undefined && lastSequence > 0) {
    url.searchParams.set('last_event_id', String(lastSequence))
  }
  return url.toString()
}
