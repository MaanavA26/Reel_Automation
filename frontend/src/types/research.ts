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

/**
 * Class of a code-derived report caveat / creator warning (see backend
 * `CaveatKind`). `CreatorWarning.kind` is always one of the two finding-level
 * members (`disputed_finding` | `weak_support`), but the wire type is the full
 * enum — see `CreatorWarning` below.
 */
export type CaveatKind =
  | "disputed_finding"
  | "weak_support"
  | "uncovered_sub_question"
  | "quality_issue"
  | "unresolved_critique";

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

/**
 * A code-derived limitation/warning on a published report (see backend
 * `Caveat`). The §11 keystone of the publishing band: never model-authored, so
 * the UI surfaces it unconditionally and never lets a polished report bury it.
 */
export interface Caveat {
  kind: CaveatKind;
  detail: string;
  finding_ids: string[];
  sub_question_ids: string[];
  critique_id: string | null;
}

/**
 * A source-grounded reference in a published report (see backend `Citation`).
 * Code-resolved by walking the provenance chain — never model-authored — so the
 * snapshot (`source_url`/`title`) is safe to render as the bibliography.
 */
export interface Citation {
  id: string;
  source_id: string;
  source_url: string;
  source_type: SourceType;
  title: string | null;
  evidence_ids: string[];
  verdict_ids: string[];
}

/** One section of a report, anchored to the plan by sub-question id. */
export interface ReportSection {
  id: string;
  heading: string;
  narrative: string;
  finding_ids: string[];
  sub_question_ids: string[];
}

/**
 * A structured, source-grounded research report — the band-D export artifact.
 * `title`/`abstract`/section prose are model-authored; `citations` and
 * `caveats` are code-derived (CLAUDE.md §11), so the UI renders the caveats as a
 * non-omittable panel rather than trusting the abstract not to overstate.
 */
export interface Report {
  id: string;
  title: string;
  abstract: string;
  sections: ReportSection[];
  citations: Citation[];
  caveats: Caveat[];
  published_at: string;
  published_via: string;
}

/**
 * A code-derived unsafe/unverified-claim warning on a creator packet (see
 * backend `CreatorWarning`). `kind` is typed as the full `CaveatKind` to mirror
 * the wire exactly, though at runtime it is always a finding-level member
 * (`disputed_finding` | `weak_support`). Cross-links to creative elements by
 * shared `finding_ids` (CLAUDE.md §11).
 */
export interface CreatorWarning {
  kind: CaveatKind;
  detail: string;
  finding_ids: string[];
}

/** A model-authored opening hook for a short-form video. */
export interface HookIdea {
  text: string;
  finding_ids: string[];
}

/** A model-authored framing/angle for the topic. */
export interface ContentAngle {
  angle: string;
  rationale: string;
  finding_ids: string[];
}

/** A model-authored short-form narrative arc option. */
export interface NarrativeOption {
  title: string;
  script_outline: string;
  finding_ids: string[];
}

/**
 * A code-derived key fact for a creator packet (see backend `KeyFact`):
 * projected from a `Finding`, carrying the same honest grounding flags so the
 * fact sheet can never overstate past the synthesis (CLAUDE.md §11).
 */
export interface KeyFact {
  statement: string;
  finding_id: string;
  disputed: boolean;
  weakest_support: SupportLevel;
}

/**
 * A short-form creator packet — the band-D handoff artifact for media. Creative
 * elements (hooks/angles/narratives) are model-authored; `key_facts` and
 * `warnings` are code-derived (CLAUDE.md §11).
 */
export interface CreatorPacket {
  id: string;
  report_id: string;
  hooks: HookIdea[];
  angles: ContentAngle[];
  narratives: NarrativeOption[];
  key_facts: KeyFact[];
  warnings: CreatorWarning[];
  created_at: string;
  published_via: string;
}

/**
 * Research Publishing substate. A job is conceptually single-report /
 * single-packet; the wire carries lists (the "step has not run" signal is the
 * empty list), so the surface renders `reports[0]`/`packets[0]` defensively.
 */
export interface ResearchPublishingState {
  reports: Report[];
  packets: CreatorPacket[];
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
  publishing: ResearchPublishingState;
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
