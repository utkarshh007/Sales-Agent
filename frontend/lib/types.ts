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

export interface TenderDetail extends TenderRow {
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
}

export const SCORE_ORDER = ["capability", "technical", "oem", "commercial", "eligibility", "timeline", "strategic"] as const;
export const SCORE_LABEL: Record<string, string> = {
  capability: "Capability match", technical: "Technical coverage", oem: "OEM / product match", commercial: "Commercial fit",
  eligibility: "Eligibility", timeline: "Timeline", strategic: "Strategic value",
};
