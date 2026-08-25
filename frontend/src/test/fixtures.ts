/**
 * API fixtures for component tests.
 *
 * Shaped exactly like the real responses, using the measured Scenario C1 values
 * so the tests exercise the case the console exists to explain.
 */
import type {
  AssistantAnswer,
  AssistantQuestionsResponse,
  AuditEntry,
  AuditSummary,
  DecisionAnalytics,
  DriftReport,
  ExplorerRow,
  FeedbackRecord,
  FeedbackSummaryResponse,
  HighRiskFunnel,
  ModelMonitoring,
  Overview,
  Page,
  Policy,
  PolicyEffectiveness,
  RecommendationsResponse,
  ReviewCase,
  RiskDistribution,
  ScoreWindows,
  SystemHealth,
  TopRiskItem,
  TransactionDetail,
  Trends,
} from '@/lib/api'

export const overview: Overview = {
  total_transactions: 20000,
  decided_transactions: 20000,
  approved: 18038,
  step_up: 1551,
  review: 410,
  blocked: 1,
  high_risk_transactions: 301,
  critical_anomalies: 176,
  open_review_cases: 411,
  escalated_review_cases: 0,
  completed_investigations: 3,
  avg_decision_latency_ms: 0.1794,
  min_decision_latency_ms: 0.0477,
  max_decision_latency_ms: 97.99,
  latency_sample_size: 20000,
  policy_version: 'policy-v1',
  high_risk_threshold: 0.533209,
  critical_anomaly_threshold: 99.4,
  data_from: '2026-05-24T10:11:17Z',
  data_to: '2026-08-22T09:58:00Z',
}

export const riskDistribution: RiskDistribution = {
  decisions: [
    { label: 'APPROVE', count: 18038, lower: null, upper: null },
    { label: 'STEP_UP', count: 1551, lower: null, upper: null },
    { label: 'REVIEW', count: 410, lower: null, upper: null },
    { label: 'BLOCK', count: 1, lower: null, upper: null },
  ],
  fraud_probability: Array.from({ length: 10 }, (_, index) => ({
    label: `${(index / 10).toFixed(1)}-${((index + 1) / 10).toFixed(1)}`,
    count: index === 0 ? 19245 : 84,
    lower: index / 10,
    upper: (index + 1) / 10,
  })),
  anomaly_severity: [
    { label: 'LOW', count: 18191, lower: null, upper: null },
    { label: 'MEDIUM', count: 1258, lower: null, upper: null },
    { label: 'HIGH', count: 346, lower: null, upper: null },
    { label: 'CRITICAL', count: 205, lower: null, upper: null },
  ],
  risk_level: [
    { label: 'LOW', count: 19411, lower: 0, upper: 0.15 },
    { label: 'MEDIUM', count: 288, lower: 0.15, upper: 0.533209 },
    { label: 'HIGH', count: 43, lower: 0.533209, upper: 0.9 },
    { label: 'CRITICAL', count: 258, lower: 0.9, upper: 1 },
  ],
  policy_version: 'policy-v1',
}

export const decisionAnalytics: DecisionAnalytics = {
  distribution: riskDistribution.decisions,
  reason_codes: [
    { label: 'LOW_FRAUD_PROBABILITY', count: 18038, lower: null, upper: null },
    { label: 'MODEL_DISAGREEMENT', count: 110, lower: null, upper: null },
  ],
  policy_version: 'policy-v1',
  decided_transactions: 20000,
}

export const trends: Trends = {
  window_days: 30,
  points: [
    {
      day: '2026-08-20T00:00:00Z',
      volume: 210,
      high_risk: 3,
      review: 2,
      blocked: 0,
      step_up: 12,
      approved: 196,
    },
    {
      day: '2026-08-21T00:00:00Z',
      volume: 198,
      high_risk: 5,
      review: 4,
      blocked: 1,
      step_up: 9,
      approved: 184,
    },
  ],
  data_from: '2026-08-20T00:00:00Z',
  data_to: '2026-08-21T00:00:00Z',
}

export const emptyTrends: Trends = {
  window_days: 1,
  points: [],
  data_from: null,
  data_to: null,
}

export const systemHealth: SystemHealth = {
  status: 'ok',
  components: [
    { name: 'backend', status: 'ok', detail: null, version: null },
    { name: 'database', status: 'ok', detail: null, version: null },
    { name: 'fraud_model', status: 'ok', detail: null, version: 'xgboost-v1' },
    {
      name: 'anomaly_model',
      status: 'ok',
      detail: null,
      version: 'isolation-forest-v1',
    },
    {
      name: 'investigation_agent',
      status: 'ok',
      detail: 'deterministic mock provider',
      version: 'mock',
    },
    { name: 'policy_engine', status: 'ok', detail: null, version: 'policy-v1' },
  ],
}

export const degradedHealth: SystemHealth = {
  status: 'unavailable',
  components: [
    { name: 'backend', status: 'ok', detail: null, version: null },
    { name: 'database', status: 'ok', detail: null, version: null },
    { name: 'fraud_model', status: 'unavailable', detail: 'FileNotFoundError', version: null },
    {
      name: 'anomaly_model',
      status: 'ok',
      detail: null,
      version: 'isolation-forest-v1',
    },
    { name: 'investigation_agent', status: 'ok', detail: null, version: 'mock' },
    { name: 'policy_engine', status: 'ok', detail: null, version: 'policy-v1' },
  ],
}

export const topRisk: { items: TopRiskItem[] } = {
  items: [
    {
      transaction_id: 'txn_00012506',
      timestamp: '2026-08-20T11:00:00Z',
      amount: 122134.09,
      currency: 'INR',
      merchant_name: 'Kirana Cart',
      customer_id: 'cus_000123',
      decision: 'REVIEW',
      fraud_probability: 0.99996,
      anomaly_score: 99,
      anomaly_severity: 'HIGH',
    },
  ],
}

export const explorerPage: Page<ExplorerRow> = {
  items: [
    {
      transaction_id: 'TXN_SCENARIO_C_CURRENT_1',
      timestamp: '2026-08-22T09:56:00Z',
      amount: 24500,
      currency: 'INR',
      status: 'pending',
      is_fraud: true,
      customer_id: 'CUSTOMER_FRAUD_001',
      merchant_id: 'mrc_0001',
      merchant_name: 'Kirana Cart',
      fraud_probability: 0.20029,
      risk_score: 20,
      risk_level: 'MEDIUM',
      anomaly_score: 100,
      anomaly_severity: 'CRITICAL',
      decision: 'REVIEW',
      policy_version: 'policy-v1',
      requires_human_review: true,
    },
    {
      transaction_id: 'TXN_SCENARIO_A_CURRENT',
      timestamp: '2026-08-22T09:57:00Z',
      amount: 2450,
      currency: 'INR',
      status: 'successful',
      is_fraud: false,
      customer_id: 'CUSTOMER_NORMAL_001',
      merchant_id: 'mrc_0001',
      merchant_name: 'Kirana Cart',
      fraud_probability: 0.000049,
      risk_score: 0,
      risk_level: 'LOW',
      anomaly_score: 58,
      anomaly_severity: 'LOW',
      decision: 'APPROVE',
      policy_version: 'policy-v1',
      requires_human_review: false,
    },
  ],
  meta: {
    page: 1,
    page_size: 25,
    total_items: 20000,
    total_pages: 800,
    has_next: true,
    has_previous: false,
  },
}

export const emptyExplorerPage: Page<ExplorerRow> = {
  items: [],
  meta: {
    page: 1,
    page_size: 25,
    total_items: 0,
    total_pages: 0,
    has_next: false,
    has_previous: false,
  },
}

export const transactionDetail: TransactionDetail = {
  transaction: {
    transaction_id: 'TXN_SCENARIO_C_CURRENT_1',
    timestamp: '2026-08-22T09:56:00Z',
    amount: 24500,
    currency: 'INR',
    status: 'pending',
    payment_method: 'card',
    is_fraud: true,
    failed_attempts: 0,
    country: 'SG',
    city: 'Singapore',
    customer_id: 'CUSTOMER_FRAUD_001',
    customer_country: 'IN',
    customer_historical_risk_level: 'low',
    customer_since: '2026-05-01T00:00:00Z',
    merchant_id: 'mrc_0001',
    merchant_name: 'Kirana Cart',
    merchant_category: 'retail',
    device_id: 'dev_scn_fraud_shared_001',
    device_type: 'web_desktop',
    ip_address: '198.18.100.31',
    ip_country: 'SG',
    ip_is_proxy: true,
  },
  signals: {
    fraud_probability: 0.20029,
    risk_score: 20,
    fraud_model_version: 'xgboost-v1',
    risk_level: 'MEDIUM',
    anomaly_score: 100,
    anomaly_severity: 'CRITICAL',
    anomaly_model_version: 'isolation-forest-v1',
    customer_deviation_score: 100,
  },
  investigation: {
    investigation_id: 'INV-4A929E29E226',
    status: 'completed',
    risk_level: 'HIGH',
    confidence: 0.925,
    summary: 'Investigation gathered 11 pieces of evidence across the available tools.',
    recommended_action: 'REVIEW',
    agent_is_mock: true,
    iteration_count: 5,
    started_at: '2026-08-23T04:45:00Z',
    completed_at: '2026-08-23T04:45:01Z',
    findings: [
      {
        finding_id: 'F-001',
        title: 'Elevated risk indicators observed',
        severity: 'HIGH',
        explanation: 'Several high-severity observations were gathered from independent tools.',
        evidence_ids: ['EV-003', 'EV-004'],
      },
    ],
    evidence: [
      {
        evidence_id: 'EV-004',
        source_tool: 'get_device_history',
        claim: 'Device is shared across 3 distinct customers before this transaction',
        value: 3,
        severity: 'HIGH',
        transaction_id: 'TXN_SCENARIO_C_CURRENT_1',
        observed_before: '2026-08-22T09:56:00Z',
        details: { customer_count: 3 },
      },
    ],
    confidence_basis: null,
    trace: null,
  },
  decision: {
    decision_id: 'DEC-abc123def456',
    decision: 'REVIEW',
    policy_version: 'policy-v1',
    decided_at: '2026-08-22T09:56:00Z',
    matched_rules: ['MODEL_DISAGREEMENT_HIGH_ANOMALY', 'MODERATE_COMBINED_RISK'],
    deciding_rules: ['MODEL_DISAGREEMENT_HIGH_ANOMALY'],
    reason_codes: ['MODEL_DISAGREEMENT', 'CRITICAL_BEHAVIORAL_ANOMALY', 'COORDINATED_ACTIVITY'],
    rule_matches: [
      {
        rule_id: 'MODEL_DISAGREEMENT_HIGH_ANOMALY',
        action: 'REVIEW',
        reason_codes: ['MODEL_DISAGREEMENT'],
        conditions: ['anomaly_score 100 >= anomaly_critical 99.4'],
      },
    ],
    explanation: 'Routed to human review: an analyst must decide this transaction.',
    requires_human_review: true,
    input_digest: '857da42d58e4'.padEnd(64, '0'),
    evaluation_ms: 0.2657,
    history_count: 1,
  },
  review: null,
  audit: [],
}

/** A transaction the pipeline has barely touched, for empty-state coverage. */
export const bareTransactionDetail: TransactionDetail = {
  ...transactionDetail,
  signals: {
    fraud_probability: null,
    risk_score: null,
    fraud_model_version: null,
    risk_level: null,
    anomaly_score: null,
    anomaly_severity: null,
    anomaly_model_version: null,
    customer_deviation_score: null,
  },
  investigation: null,
  decision: null,
  review: null,
  audit: [],
}

export const reviewPage: Page<ReviewCase> = {
  items: [
    {
      review_case_id: 825,
      transaction_id: 'TXN_SCENARIO_B_CURRENT',
      status: 'open',
      reason: 'BLOCK: CRITICAL_SUPERVISED_RISK',
      created_at: '2026-08-22T09:58:00Z',
      resolved_at: null,
      resolution: null,
      resolution_reason: null,
      assigned_to: null,
      decision: {
        decision_id: 'DEC-ff401dd1e3f04e25',
        decision: 'BLOCK',
        policy_version: 'policy-v1',
        matched_rules: ['CRITICAL_SUPERVISED_RISK'],
        reason_codes: ['VERY_HIGH_FRAUD_PROBABILITY', 'INDEPENDENT_CORROBORATION'],
        requires_human_review: true,
        fraud_probability: 0.99964,
        anomaly_score: 100,
        investigation_id: 'INV-EA44A37CF90C',
        decided_at: '2026-08-22T09:58:00Z',
      },
    },
  ],
  meta: {
    page: 1,
    page_size: 20,
    total_items: 411,
    total_pages: 21,
    has_next: true,
    has_previous: false,
  },
}

export const emptyReviewPage: Page<ReviewCase> = {
  items: [],
  meta: {
    page: 1,
    page_size: 20,
    total_items: 0,
    total_pages: 0,
    has_next: false,
    has_previous: false,
  },
}

export const auditPage: Page<AuditEntry> = {
  items: [
    {
      audit_id: 1,
      event_type: 'risk.decision',
      actor_type: 'system',
      actor_id: 'risk-decision-engine',
      transaction_id: 'TXN_SCENARIO_C_CURRENT_1',
      created_at: '2026-08-22T09:56:00Z',
      decision: 'REVIEW',
      decision_id: 'DEC-abc123def456',
      policy_version: 'policy-v1',
      investigation_id: 'INV-4A929E29E226',
      resolution: null,
      event_data: {
        matched_rules: ['MODEL_DISAGREEMENT_HIGH_ANOMALY'],
        reason_codes: ['MODEL_DISAGREEMENT'],
        input_digest: 'abc123',
      },
    },
  ],
  meta: {
    page: 1,
    page_size: 25,
    total_items: 20012,
    total_pages: 801,
    has_next: true,
    has_previous: false,
  },
}

export const auditSummary: AuditSummary = {
  counts: { 'risk.decision': 20000, 'investigation.completed': 12 },
  known_event_types: [
    'risk.decision',
    'review.case_opened',
    'review.resolved',
    'investigation.completed',
  ],
}

export const policy: Policy = {
  policy_version: 'policy-v1',
  description: 'Baseline RazorShield policy.',
  source: 'default.yaml',
  thresholds: {
    fraud_block: 0.9,
    fraud_high: 0.533209,
    fraud_medium: 0.15,
    anomaly_critical: 99.4,
    anomaly_high: 97.91,
    anomaly_medium: 92.41,
  },
  evidence: {
    min_independent_sources_for_block: 2,
    min_high_findings_for_review: 2,
    min_investigation_confidence: 0.5,
  },
  fail_safe: {
    missing_supervised_signal: 'REVIEW',
    missing_anomaly_signal: 'REVIEW',
    missing_investigation: 'STEP_UP',
    require_investigation_for_block: true,
  },
  action_precedence: ['BLOCK', 'REVIEW', 'STEP_UP', 'APPROVE'],
  default_action: 'APPROVE',
  human_review_required_for: ['BLOCK', 'REVIEW'],
  rules: [
    {
      rule_id: 'CRITICAL_SUPERVISED_RISK',
      action: 'BLOCK (or REVIEW without corroboration)',
      enabled: true,
      description: 'BLOCK on very high fraud probability with independent corroboration.',
    },
    {
      rule_id: 'MODEL_DISAGREEMENT_HIGH_ANOMALY',
      action: 'REVIEW',
      enabled: true,
      description: 'REVIEW when the anomaly engine is alarmed and the fraud model is not.',
    },
  ],
  reason_codes: ['MODEL_DISAGREEMENT', 'COORDINATED_ACTIVITY'],
  editable: false,
}


// --------------------------------------------------------------------------
// Phase 8: feedback and monitoring
// --------------------------------------------------------------------------

export const feedbackSummary: FeedbackSummaryResponse = {
  summary: {
    total_feedback: 200,
    confirmed_fraud: 63,
    legitimate: 74,
    false_positive: 57,
    false_negative: 6,
    insufficient_evidence: 0,
    escalated: 0,
    ground_truth_labels: 200,
    total_transactions: 20000,
    total_review_cases: 411,
    labelled_share_of_transactions: 0.01,
    by_reason: [
      { reason_code: 'known_customer_behavior', count: 74 },
      { reason_code: 'coordinated_activity', count: 63 },
      { reason_code: 'model_false_positive', count: 57 },
      { reason_code: 'model_false_negative', count: 6 },
    ],
  },
  confusion_matrix: {
    cells: [
      {
        machine_decision: 'REVIEW',
        outcome: 'confirmed_fraud',
        actually_fraud: true,
        count: 63,
      },
      {
        machine_decision: 'APPROVE',
        outcome: 'legitimate',
        actually_fraud: false,
        count: 74,
      },
    ],
    machine_actions: ['APPROVE', 'STEP_UP', 'REVIEW', 'BLOCK'],
    true_positive: 63,
    false_negative: 6,
    false_positive: 57,
    true_negative: 74,
    labelled_included: 200,
    excluded_open_outcomes: 0,
  },
}

export const emptyFeedbackSummary: FeedbackSummaryResponse = {
  summary: {
    ...feedbackSummary.summary,
    total_feedback: 0,
    confirmed_fraud: 0,
    legitimate: 0,
    false_positive: 0,
    false_negative: 0,
    ground_truth_labels: 0,
    labelled_share_of_transactions: 0,
    by_reason: [],
  },
  confusion_matrix: {
    ...feedbackSummary.confusion_matrix,
    cells: [],
    true_positive: 0,
    false_negative: 0,
    false_positive: 0,
    true_negative: 0,
    labelled_included: 0,
    excluded_open_outcomes: 0,
  },
}

export const feedbackPage: Page<FeedbackRecord> = {
  items: [
    {
      feedback_id: 'FBK-abc123',
      transaction_id: 'TXN_SCENARIO_C_CURRENT_1',
      decision_id: 'DEC-abc123def456',
      review_case_id: 1234,
      analyst_id: null,
      outcome: 'confirmed_fraud',
      reason_code: 'coordinated_activity',
      notes: 'Shared device and IP confirmed across three customers.',
      machine_decision: 'REVIEW',
      policy_version: 'policy-v1',
      created_at: '2026-08-23T14:39:58Z',
    },
  ],
  meta: {
    page: 1,
    page_size: 20,
    total_items: 200,
    total_pages: 10,
    has_next: true,
    has_previous: false,
  },
}

export const emptyFeedbackPage: Page<FeedbackRecord> = {
  items: [],
  meta: {
    page: 1,
    page_size: 20,
    total_items: 0,
    total_pages: 0,
    has_next: false,
    has_previous: false,
  },
}

export const modelMonitoring: ModelMonitoring = {
  metrics: {
    sufficient: true,
    message: null,
    selection_bias_note: null,
    labelled_flagged: 120,
    labelled_unflagged: 80,
    precision: 0.525,
    recall: 0.913,
    f1: 0.667,
    false_positive_rate: 0.435,
    false_negative_rate: 0.087,
    true_positive: 63,
    false_positive: 57,
    true_negative: 74,
    false_negative: 6,
    labelled_samples: 200,
    total_feedback: 200,
    open_outcome_labels: 0,
    unlabelled_transactions: 19800,
    total_transactions: 20000,
    minimum_required: 30,
    label_source: 'analyst_feedback',
  },
  coverage: {
    total_transactions: 20000,
    confirmed_labels: 200,
    analyst_feedback_total: 200,
    open_outcome_labels: 0,
    unlabelled: 19800,
    simulated_fraud_flags: 295,
    simulated_label_note:
      "The dataset's is_fraud column is a generation-time property of the simulation, not an analyst confirmation. It is reported for reference and is never used as ground truth in the metrics above.",
  },
}

export const insufficientModelMonitoring: ModelMonitoring = {
  metrics: {
    ...modelMonitoring.metrics,
    sufficient: false,
    message:
      'Insufficient labeled data. 1 ground-truth label available; 30 required before precision and recall are meaningful.',
    precision: null,
    recall: null,
    f1: null,
    false_positive_rate: null,
    false_negative_rate: null,
    true_positive: null,
    false_positive: null,
    true_negative: null,
    false_negative: null,
    labelled_samples: 1,
    selection_bias_note: null,
    labelled_flagged: null,
    labelled_unflagged: null,
  },
  coverage: { ...modelMonitoring.coverage, confirmed_labels: 1, unlabelled: 19999 },
}

export const biasedModelMonitoring: ModelMonitoring = {
  metrics: {
    ...modelMonitoring.metrics,
    labelled_unflagged: 0,
    true_negative: 0,
    false_negative: 0,
    recall: 1,
    false_negative_rate: 0,
    selection_bias_note:
      'Every labelled transaction was one the system flagged, because labels come from the review queue. With no un-flagged examples, recall and the false-negative rate are 1.0 and 0.0 by construction rather than by measurement.',
  },
  coverage: modelMonitoring.coverage,
}

export const scoreWindows: ScoreWindows = {
  baseline: {
    from: '2026-05-24T00:00:00Z',
    to: '2026-07-23T00:00:00Z',
    scored_transactions: 13273,
    mean_fraud_probability: 0.0221,
    high_risk_count: 196,
    high_risk_percent: 1.48,
    anomaly_scored_transactions: 13273,
    mean_anomaly_score: 50.74,
    critical_anomaly_count: 113,
    critical_anomaly_percent: 0.85,
  },
  current: {
    from: '2026-07-23T00:00:00Z',
    to: '2026-08-22T00:00:00Z',
    scored_transactions: 6725,
    mean_fraud_probability: 0.022,
    high_risk_count: 103,
    high_risk_percent: 1.53,
    anomaly_scored_transactions: 6725,
    mean_anomaly_score: 52.23,
    critical_anomaly_count: 61,
    critical_anomaly_percent: 0.91,
  },
  high_risk_threshold: 0.533209,
  critical_anomaly_threshold: 99.4,
  thresholds: { psi_watch: 0.1, psi_drift: 0.25 },
}

export const driftReport: DriftReport = {
  features: [
    {
      feature: 'amount',
      kind: 'numeric',
      psi: 0.0014,
      status: 'NORMAL',
      baseline_count: 13273,
      current_count: 6725,
      baseline_mean: 6481.65,
      current_mean: 6445.44,
    },
    {
      feature: 'anomaly_score',
      kind: 'numeric',
      psi: 0.3707,
      status: 'DRIFT_DETECTED',
      baseline_count: 13273,
      current_count: 6725,
      baseline_mean: 50.74,
      current_mean: 52.23,
    },
    {
      feature: 'merchant',
      kind: 'categorical',
      psi: 0.0011,
      status: 'NORMAL',
      baseline_count: 13273,
      current_count: 6725,
      baseline_mean: null,
      current_mean: null,
    },
    {
      feature: 'location',
      kind: 'categorical',
      psi: null,
      status: 'INSUFFICIENT_DATA',
      baseline_count: 4,
      current_count: 2,
      baseline_mean: null,
      current_mean: null,
    },
  ],
  baseline_from: '2026-05-24T00:00:00Z',
  baseline_to: '2026-07-23T00:00:00Z',
  current_from: '2026-07-23T00:00:00Z',
  current_to: '2026-08-22T00:00:00Z',
  thresholds: { psi_watch: 0.1, psi_drift: 0.25 },
  note: 'Drift means the distribution of a feature moved between the two windows. It is not evidence of fraud.',
}

export const emptyDriftReport: DriftReport = {
  ...driftReport,
  features: [],
  baseline_from: null,
  baseline_to: null,
  current_from: null,
  current_to: null,
}

export const policyEffectiveness: PolicyEffectiveness = {
  rules: [
    {
      rule_id: 'MODEL_DISAGREEMENT_HIGH_ANOMALY',
      description: 'REVIEW when the anomaly engine is alarmed and the fraud model is not.',
      primary_action: 'REVIEW',
      triggers: 110,
      approve_count: 0,
      step_up_count: 0,
      review_count: 110,
      block_count: 0,
      resolved_count: 45,
      override_count: 20,
      override_rate: 0.444,
      override_rate_reportable: true,
      flagged_high_override: true,
    },
    {
      rule_id: 'LOW_RISK',
      description: 'APPROVE when both engines are present, both are quiet, and nothing found.',
      primary_action: 'APPROVE',
      triggers: 18038,
      approve_count: 18038,
      step_up_count: 0,
      review_count: 0,
      block_count: 0,
      resolved_count: 0,
      override_count: 0,
      override_rate: null,
      override_rate_reportable: false,
      flagged_high_override: false,
    },
  ],
  high_override_threshold: 0.3,
  min_rule_triggers: 10,
  policy_version: 'policy-v1',
  override_note:
    'An override means the analyst contradicted a position the engine took. A REVIEW decision is the engine declining to decide, so REVIEW cases are never overrides.',
}

export const highRiskFunnel: HighRiskFunnel = {
  stages: [
    {
      stage: 'HIGH_FRAUD_SCORE',
      count: 258,
      description: 'Fraud probability at or above the block threshold (0.9).',
    },
    {
      stage: 'DECIDED',
      count: 258,
      description: 'Of those, transactions carrying a current policy decision.',
    },
    {
      stage: 'INVESTIGATION_AVAILABLE',
      count: 1,
      description: 'Of those, transactions with an investigation on record.',
    },
    {
      stage: 'SUFFICIENT_CORROBORATION',
      count: 1,
      description: 'Investigations that produced at least 2 independent high-severity sources.',
    },
    {
      stage: 'BLOCK_ELIGIBLE',
      count: 1,
      description: 'Transactions meeting every condition the block rule requires.',
    },
    {
      stage: 'FINAL_DECISION_BLOCK',
      count: 1,
      description: 'Transactions the engine actually blocked.',
    },
  ],
  withheld_pending_investigation: 257,
  final_actions: { REVIEW: 257, BLOCK: 1 },
  block_threshold: 0.9,
  min_independent_sources: 2,
  policy_version: 'policy-v1',
  explanation:
    'A high model score is necessary but not sufficient for a block. The policy requires independent corroboration before taking the one action a customer cannot undo.',
}

export const recommendations: RecommendationsResponse = {
  recommendations: [
    {
      id: 'override-model_disagreement_high_anomaly',
      severity: 'high',
      title: 'MODEL_DISAGREEMENT_HIGH_ANOMALY has a high analyst override rate',
      detail: 'Analysts reached a different outcome on 20 of 45 resolved cases (44%).',
      metric_source: '/api/monitoring/policy',
      action_required: 'human_review',
    },
  ],
  note: 'Recommendations are analytical only. No model is retrained, no threshold moved, no policy edited and no decision revised by this system.',
}

export const assistantQuestions: AssistantQuestionsResponse = {
  questions: [
    { topic: 'high_risk_not_blocked', question: 'Why were high-risk transactions not blocked?' },
    { topic: 'model_drift', question: 'Is model behavior drifting?' },
  ],
  note: 'The assistant answers from structured backend metrics only.',
}

export const assistantAnswer: AssistantAnswer = {
  topic: 'high_risk_not_blocked',
  question: 'Why were high-risk transactions not blocked?',
  answer:
    '258 transactions scored at or above the block threshold (0.9), and 1 was blocked. The remaining 257 had their block withheld and were routed to human review instead.',
  metric_sources: ['/api/monitoring/high-risk-funnel', '/api/policy'],
  time_window: '2026-05-24 to 2026-08-22 (entire dataset)',
  data_availability: '258 decided high-score transactions',
  sufficient: true,
  figures: {},
}

export const insufficientAssistantAnswer: AssistantAnswer = {
  ...assistantAnswer,
  topic: 'model_performance',
  question: 'How is the model performing against analyst labels?',
  answer:
    'Insufficient labeled data. 1 ground-truth label available; 30 required before precision and recall are meaningful.',
  sufficient: false,
  data_availability: '1 labelled of 20,000 transactions',
}
