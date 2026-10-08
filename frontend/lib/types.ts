export type Priority = "HOT" | "HIGH" | "MEDIUM" | "LOW" | "SUPPRESS";
export type Decision = "ACCEPTED" | "REJECTED" | "MANUAL_REVIEW" | "PENDING";

export interface TenderRow {
  id: number;
  score: number | null;
  priority: Priority | null;
  title: string;
  organization: string | null;
  location: string | null;
  opportunity_type: string;
  matched_capability: string | null;
  matched_products: string[];
  tender_value_inr: number | null;
  service_value_inr: number | null;
  closing_at: string | null;
  published_at: string | null;
  match_confidence: number | null;
  decision: Decision;
  pipeline_status: string;
  documents_status: string;
  flags: string[];
  portal_code: string | null;
  reference_number: string | null;
  portal_tender_id: string | null;
  score_parts?: Record<string, [number | null, number]> | null;
  source_count?: number;
  plain_summary?: string;
}

export interface ScoreComponent {
  points: number | null;
  max: number;
  applicable: boolean;
  reason: string;
}

export interface MatchOut {
  capability_id: string;
  capability_name: string;
  sub_capability: string | null;
  match_type: "DIRECT" | "SEMANTIC" | "ADJACENT";
  confidence: number;
  evidence: string | null;
  explanation: string | null;
  source: string;
  explicit_oem: boolean;
  products: { id: string; name: string; oem: string }[];
}

export interface FieldOut {
  value: string;
  confidence: number;
  evidence: string;
  source: string;
}

export interface Brief {
  headline: string;
  stage: string | null;
  kind: string | null;
  fit: string | null;
  points: { label: string; value: string }[];
  source: "rules" | "llm";
}

export interface Contacts {
  client: { organization: string | null; department: string | null; name?: string | null; designation?: string;
    email?: string; phone?: string; address?: string | null; source?: string; evidence?: string } | null;
  submission: { mode: "ONLINE_PORTAL" | "EMAIL" | "PHYSICAL" | "UNKNOWN"; url?: string | null; portal_name?: string | null;
    email?: string; address?: string; source?: string; evidence?: string };
  helpdesk: { email: string | null; phone: string | null; source: string; evidence: string } | null;
  other_emails: { email: string; source: string; context: string }[];
  read_documents: string[];
}

export interface TenderDetail extends TenderRow {
  brief: Brief;
  contacts: Contacts;
  version: number;
  source_url: string | null;
  department: string | null;
  opening_at: string | null;
  emd_inr: number | null;
  corrigendum: string | null;
  blocker_note: string | null;
  first_seen_at: string | null;
  last_analyzed_at: string | null;
  analysis_mode: string | null;
  model_version: string | null;
  relevance_reason: string | null;
  rejection_reason: string | null;
  summary: string | null;
  recommendation: string | null;
  extracted: Record<string, FieldOut> & {
    _requirements?: { text: string; kind: string; capability_ids: string[]; match_type: string; confidence: number; evidence: string; explanation: string; other_oems_named: string[] }[];
    _eligibility?: { assessment: string; issues: string[] };
    _risks?: string[];
    _notes?: string[];
    _eligibility_check?: EligibilityCheck | null;
    _segment?: { name: string; weight: number } | null;
    _next_actions?: NextAction[];
  };
  value_analysis: {
    total_value_inr?: number | null;
    service_value_inr?: number | null;
    product_value_inr?: number | null;
    deterministic_value_inr?: number | null;
    deterministic_evidence?: string | null;
    llm_value_inr?: number | null;
    llm_evidence?: string | null;
    emd_inr?: number | null;
    estimated_band?: { low: number; high: number; basis: string } | null;
    boq_lines?: number;
    components?: { description: string; kind: string; value_inr: number | null; evidence: string }[];
  };
  portal_text: string | null;
  document_names: string | null;
  sources: { portal_code: string; portal_name: string; match_basis: string; source_url: string | null; lookup_hint: string | null; first_seen_at: string | null; last_seen_at: string | null }[];
  matches: MatchOut[];
  score_breakdown: Record<string, ScoreComponent> | null;
  documents: {
    id: number; filename: string; origin: string; status: string; size_bytes: number | null; method: string | null;
    pages: number | null; sections: string[]; error: string | null; uploaded_by: string | null; created_at: string | null;
    chars: number; source_url: string | null;
  }[];
  versions: { version: number; change: string | null; created_at: string | null }[];
  rejections: { stage: string; code: string; detail: string }[];
  reviews: { id: number; code: string; reason: string; status: string; resolution: string | null; notes: string | null; resolved_by: string | null; version: number }[];
  alerts: { kind: string; status: string; subject: string; created_at: string | null }[];
  audit: { action: string; actor: string; decision: string | null; reason: string | null; model_version: string | null; created_at: string | null; details: Record<string, unknown> }[];
}

export interface Overview {
  new_24h: number; hot: number; high: number; medium: number; closing_soon: number; service: number; oem: number;
  hybrid: number; accepted: number; manual_review: number; rejected: number; pending: number; awaiting_documents: number;
  total: number; analysis_mode: string;
  portals: { code: string; name: string; enabled: boolean; last_run_at: string | null; last_status: string | null; last_error: string | null; blocker: string | null; tenders: number }[];
}

export interface Me {
  email: string;
  role: "admin" | "analyst" | "viewer";
  mfa_enabled: boolean;
  mfa_setup_required: boolean;
}

export const SCORE_ORDER = ["capability", "technical", "oem", "commercial", "eligibility", "timeline", "strategic"] as const;
export const SCORE_LABEL: Record<string, string> = {
  capability: "Capability match", technical: "Technical coverage", oem: "OEM / product match", commercial: "Commercial fit",
  eligibility: "Eligibility", timeline: "Timeline", strategic: "Strategic value",
};

export interface EligibilityCriterion {
  code: string;
  label: string;
  requirement: string;
  evidence: string;
  company: unknown;
  status: "MET" | "NOT_MET" | "UNKNOWN" | "RELAXED" | "INFO";
  note: string;
}

export interface EligibilityCheck {
  assessment: "FEASIBLE" | "PARTIAL" | "INFEASIBLE" | "UNKNOWN";
  startup_relaxation: boolean;
  msme_relaxation: boolean;
  source: string;
  criteria: EligibilityCriterion[];
}

export interface NextAction {
  action: string;
  why: string;
  points: number;
}

export interface Outcome {
  stage: string;
  no_bid_reason: string | null;
  our_bid_value_inr: number | null;
  award_value_inr: number | null;
  winner: string | null;
  our_rank: number | null;
  loss_reason: string | null;
  notes: string | null;
  updated_by: string | null;
  updated_at: string | null;
}

export interface HistoryOut {
  similar: { id: number; title: string; organization: string | null; similarity: number; published_at: string | null;
    decision: string; value_inr: number | null; outcome: string | null; award_value_inr: number | null; winner: string | null }[];
  buyer: { organization: string; tenders_seen: number; surfaced: number; bid: number; won: number; lost: number;
    recent: { id: number; title: string; decision: string; published_at: string | null; outcome: string | null }[] } | null;
  recurrence: { previous_id: number; previous_title: string; previous_published_at: string; interval_days: number;
    similarity: number; next_expected_around: string } | null;
}

export interface Analytics {
  window_days: number;
  history_days: number;
  funnel: { discovered: number; surfaced: number; pursued: number; submitted: number; won: number;
    surfaced_rate: number | null; win_rate: number | null; win_rate_sample: number };
  sources: { code: string; name: string; enabled: boolean; seen: number; surfaced: number; detailed: number;
    yield: number | null; tenders_per_relevant: number | null }[];
  screening: { decided_without_llm: number; decided_without_llm_rate: number | null; analysed_by_llm: number;
    analysed_by_rules: number; rejection_reasons: { code: string; label: string; count: number; share: number }[] };
  mix: Record<"by_capability" | "by_type" | "by_segment" | "by_priority", { label: string; count: number }[]>;
  trend: { week: string; discovered: number; surfaced: number }[];
  reviews: { open: number; resolved: number; pursued_after_review: number; median_hours_to_decide: number | null;
    by_reason: { code: string; count: number }[] };
  outcomes: { decided: number; enough_data: boolean;
    win_rate_by_capability: { label: string; won: number; lost: number; win_rate: number | null }[];
    win_rate_by_segment: { label: string; won: number; lost: number; win_rate: number | null }[];
    win_rate_by_type: { label: string; won: number; lost: number; win_rate: number | null }[];
    competitors: { name: string; wins_against_us: number }[];
    no_bid_reasons: { reason: string; count: number }[]; loss_reasons: { reason: string; count: number }[];
    median_price_gap_when_lost: number | null };
  pipeline_value: { open_opportunities: number; with_stated_value: number; stated_value_inr: number;
    stated_value_by_type: Record<string, number>; bidding_now: number };
}

export interface DashKpi { key: string; label: string; value: number | null; prev: number | null; delta: number | null;
  format: "int" | "inr" | "pct" | "score"; note: string | null; spark: number[] | null; lower_is_better: boolean }
export interface DashGroup { label: string; count: number; value: number; avg_score: number | null }
export interface Dashboard {
  as_of: string;
  window: { days: number; since: string | null; history_days: number; bucket: "day" | "week"; has_previous: boolean };
  filters: { applied: Record<string, string>; options: { portals: { code: string; name: string }[];
    types: { code: string; label: string }[]; segments: string[]; capabilities: string[] } };
  kpis: DashKpi[];
  trend: { date: string; read: number; surfaced: number }[];
  funnel: { stage: string; count: number }[];
  type_mix: { code: string; label: string; count: number; value: number }[];
  by_capability: DashGroup[];
  by_segment: DashGroup[];
  value_bands: { label: string; count: number }[];
  deadlines: { label: string; count: number; value: number }[];
  sources: { code: string; name: string; read: number; surfaced: number; avg_score: number | null; yield: number | null;
    status: string | null; last_run_at: string | null }[];
  rejections: { code: string; label: string; count: number }[];
  pipeline: { stage: string; count: number }[];
  top: { id: number; title: string; organization: string | null; score: number | null; priority: string | null; type: string;
    capability: string | null; value: number | null; closing_at: string | null; decision: string; stage: string | null;
    plain_summary: string | null }[];
  insights: { kind: "risk" | "opportunity" | "trend" | "anomaly" | "info"; title: string; detail: string; tender_ids?: number[] }[];
}
