/* Shapes of the JSON the Python console returns.
 *
 * These are hand-written against `webapp/payloads.py` rather than generated,
 * because the payloads are stable and few, and a code generator would be a
 * second build step to keep honest. Where the server can legitimately omit a
 * column — a supervisor with no Lattes record, a plan nobody has profiled —
 * the field is optional or nullable here, so the compiler forces the absent
 * case to be handled instead of rendering `undefined` into the page.
 */

export interface AppState {
  ready: boolean;
  space_id?: string;
  run_id?: string;
  corpus_fingerprint?: string;
  counts?: Record<string, number>;
  campaigns?: CampaignSummary[];
}

export interface CampaignSummary {
  campaign_id: string;
  name: string;
  created_at: string;
  recipients: number;
  sent: number;
}

/* ------------------------------------------------------------ work plans */

export interface PlanRow {
  id_opportunity: string;
  plan_title?: string | null;
  project_title?: string | null;
  project_code?: string | null;
  professor_name?: string | null;
  professor_siape?: string | null;
  center?: string | null;
  edital?: string | null;
  area?: string | null;
  status?: string | null;
  application_status?: string | null;
  has_funding?: boolean;
  funded_slots?: number | null;
  rank_pct?: number | null;
  skills?: string[];
}

export interface OpportunitiesPayload {
  rows: PlanRow[];
  centers: string[];
  editais: string[];
  skill_options: string[];
}

export interface SkillRow {
  label: string;
  mentions: number;
  category?: string | null;
  generic?: boolean;
}

export interface TopicShare {
  topic_id: number | string;
  label: string;
  share: number;
}

export interface ScoreRow {
  facet: string;
  channel: string;
  score: number;
  percentile: number | null;
}

export interface PlanDetail {
  record: PlanRow & {
    objectives?: string | null;
    methodology?: string | null;
    acquired_skills?: string | null;
    introduction_justification?: string | null;
    references_text?: string | null;
  };
  skills: SkillRow[];
  scores: ScoreRow[];
  topics: Record<'domain' | 'methods', TopicShare[]>;
}

/* ------------------------------------------------------------ professors */

export interface ProfessorRow {
  siape: string;
  canonical_name?: string | null;
  department?: string | null;
  center?: string | null;
  funded_slots?: number | null;
  funded_opportunities?: number | null;
  opportunities?: number | null;
  publications?: number | null;
  orientations?: number | null;
  current_fused_pct?: number | null;
  trajectory_fused_pct?: number | null;
}

export interface ProfessorsPayload {
  rows: ProfessorRow[];
  centers: string[];
}

export interface EvidenceRow {
  scope: 'current' | 'trajectory' | string;
  kind?: string | null;
  label: string;
  year?: number | null;
  score: number;
  weight: number;
}

export interface CollaboratorRow {
  collaborator_name: string;
  target_siape?: string | null;
  weight: number;
}

export interface ProfessorDetail {
  record: ProfessorRow & {
    email?: string | null;
    lattes_id?: string | null;
    profile_summary?: string | null;
  };
  opportunities: PlanRow[];
  skills: SkillRow[];
  scores: ScoreRow[];
  evidence: EvidenceRow[];
  collaborators: CollaboratorRow[];
  topics: Record<'domain' | 'methods', TopicShare[]>;
}

/* ------------------------------------------------------------- landscape */

export interface ProjectPoint {
  project_code?: string | null;
  project_title: string;
  professor_name?: string | null;
  topic_id?: number | string | null;
  topic_label?: string | null;
  work_plans?: number | null;
  x?: number | null;
  y?: number | null;
}

export interface TopicRow {
  topic_id: number | string;
  label: string;
  terms?: string[];
  projects?: number;
}

export interface LandscapePayload {
  projects: ProjectPoint[];
  topics: TopicRow[];
  map: { method?: string; stress?: number; spearman?: number; [k: string]: unknown };
}

/* --------------------------------------------------------------- network */

export type ThreadState =
  | 'awaiting' | 'replied' | 'open' | 'indicated' | 'declined' | 'closed';

export interface GraphNode {
  id: string;
  name: string;
  center?: string | null;
  department?: string | null;
  cluster?: number | null;
  x?: number | null;
  y?: number | null;
  funded_slots?: number | null;
  opportunities?: number | null;
  publications?: number | null;
  orientations?: number | null;
  current_projects?: number | null;
  latest_year?: number | null;
  external_collaborators?: number | null;
  keywords?: string[];
  current_pct?: number | null;
  trajectory_pct?: number | null;
  methods_pct?: number | null;
  skills_pct?: number | null;
  contacted?: boolean;
  state?: ThreadState | null;
  campaign_id?: string | null;
  degree: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  weight: number;
  shared?: number;
  why?: string[];
  because?: string;
}

export interface NetworkPayload {
  mode: string;
  modes: { key: string; label: string }[];
  note: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/* -------------------------------------------------------------- outreach */

export interface RecipientRow {
  siape: string;
  professor_name?: string | null;
  email?: string | null;
  subject?: string | null;
  body_text?: string | null;
  status?: string | null;
  state?: ThreadState | null;
  note?: string | null;
  error?: string | null;
  sent_at?: string | null;
  replied_at?: string | null;
  is_customized?: boolean;
  primary_title?: string | null;
  opportunity_percentile?: number | null;
  funded_slots?: number | null;
  matched_skills?: string[];
  qualifying_evidence?: QualifyingPlan[];
  rationale?: unknown;
}

/** A work plan as frozen into a campaign, with the percentile it had then. */
export interface QualifyingPlan extends PlanRow {
  opportunity_percentile?: number | null;
}

export interface CampaignDetail {
  campaign_id: string;
  name?: string;
  send_command: string;
  states: { key: ThreadState; label: string }[];
  funnel: Partial<Record<ThreadState, number>>;
  summary?: { by_status?: Record<string, number> };
  rows: RecipientRow[];
}

export interface OutcomeResult {
  outcome: { state: ThreadState; note?: string | null; replied_at?: string | null };
  funnel: Partial<Record<ThreadState, number>>;
}

/* ---------------------------------------------------------------- engine */

export interface BenchmarkRow {
  benchmark: string;
  channel: string;
  metric: string;
  value: number;
  note?: string | null;
}

export interface TopicQuality {
  selected_k?: number;
  npmi?: number;
  stability?: number;
  exclusivity?: number;
  selection_reason?: string;
  candidates?: { k: number; score: number }[];
}

export interface EnginePayload {
  space: {
    space_id: string;
    created_at?: string;
    fingerprint?: string;
    identity: unknown;
    stats?: {
      training_documents?: number;
      atoms?: number;
      channels?: string[];
    };
  };
  run: { run_id: string; profile: unknown };
  benchmarks: BenchmarkRow[];
  map: Record<string, number>;
  topic_quality: Record<string, TopicQuality>;
}
