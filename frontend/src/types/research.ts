/**
 * Frontend contracts for the Deep Research surface (roadmap M13).
 *
 * These interfaces mirror the backend Pydantic schema in
 * `backend/app/schemas/research_state.py`. Two deliberate conventions:
 *
 * 1. **Wire casing is snake_case.** The backend models declare no
 *    `alias_generator`, so FastAPI serializes them as snake_case. We match the
 *    wire format here rather than TS camelCase idiom so fields resolve instead
 *    of silently reading `undefined`.
 * 2. **Timestamps are ISO strings.** Pydantic `datetime` fields serialize to
 *    ISO-8601 strings over JSON, so they are typed `string`, not `Date`.
 *
 * The submit endpoint lands in a sibling PR. Until it does, the assumed
 * response body is the serialized `ResearchState` itself (no wrapper envelope).
 * If the backend later wraps it, only `services/research.ts` needs to change.
 */

export type JobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type SourceType = "web" | "pdf" | "paper" | "youtube" | "repo" | "file";

/** Structural support axis for a cross-checked claim (see backend `SupportLevel`). */
export type SupportLevel = "corroborated" | "single_source" | "contradicted";

export type CritiqueDecision = "accept" | "revise";

export type QualityIssueKind =
  | "redundant"
  | "imbalanced"
  | "overstated"
  | "unclear";

/** A decomposed question within the research plan (priority = list order). */
export interface SubQuestion {
  id: string;
  text: string;
  rationale: string | null;
}

export interface ResearchPlan {
  id: string;
  goal: string | null;
  sub_questions: SubQuestion[];
  created_at: string;
}

/** A source discovered during the Knowledge Acquisition band. */
export interface Source {
  id: string;
  url: string;
  type: SourceType;
  discovered_via: string;
  title: string | null;
  discovered_at: string;
  raw_metadata: Record<string, string>;
}

/**
 * A synthesized answer-unit. `disputed` and `weakest_support` are code-derived
 * grounding flags (never model self-reported) and must be surfaced honestly in
 * the UI per CLAUDE.md §11 (evidence-vs-inference distinction).
 */
export interface Finding {
  id: string;
  statement: string;
  detail: string | null;
  sub_question_ids: string[];
  supporting_verdict_ids: string[];
  disputed: boolean;
  weakest_support: SupportLevel;
  synthesized_at: string;
  synthesized_via: string;
}

export interface Synthesis {
  findings: Finding[];
}

export interface QualityIssue {
  kind: QualityIssueKind;
  detail: string;
  finding_ids: string[];
  sub_question_ids: string[];
}

/** An editorial assessment of a synthesis. The reasoning band holds a list. */
export interface Critique {
  id: string;
  decision: CritiqueDecision;
  uncovered_sub_question_ids: string[];
  issues: QualityIssue[];
  rationale: string;
  critiqued_at: string;
  critiqued_via: string;
}

/**
 * Knowledge Acquisition substate. The wire object also carries `chunks` and
 * `evidence`; only `sources` is modelled here because that is the sole
 * acquisition artifact the M13 surface renders (anti-sprawl, CLAUDE.md §7).
 */
export interface KnowledgeAcquisitionState {
  sources: Source[];
}

export interface KnowledgeReasoningState {
  /** Verdicts/evidence exist on the wire but are not rendered by the M13 surface. */
  synthesis: Synthesis;
  critiques: Critique[];
}

export interface ResearchState {
  id: string;
  topic: string;
  status: JobStatus;
  created_at: string;
  updated_at: string;
  error: string | null;
  revision_iteration: number;
  plan: ResearchPlan;
  acquisition: KnowledgeAcquisitionState;
  reasoning: KnowledgeReasoningState;
}

/** Payload sent to the research submit endpoint. */
export interface ResearchRequest {
  topic: string;
}

/**
 * Response body of the research submit endpoint. Defined as the serialized
 * `ResearchState` (see file header). Aliased so call sites read intent and a
 * future envelope change stays localized.
 */
export type ResearchResult = ResearchState;
