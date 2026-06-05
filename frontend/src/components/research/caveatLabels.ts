import type { CaveatKind } from "../../types/research";

/**
 * Human-readable labels for a `CaveatKind`, shared by the report `CaveatsPanel`
 * and the packet `CreatorPacketView`. A single map mirrors the backend, which
 * reuses one predicate across the report `Caveat` and the creator `CreatorWarning`
 * "so the two never drift" (CLAUDE.md §11) — this keeps the UI labels in lockstep.
 */
export const CAVEAT_LABELS: Record<CaveatKind, string> = {
  disputed_finding: "Disputed finding",
  weak_support: "Weak support",
  uncovered_sub_question: "Uncovered sub-question",
  quality_issue: "Quality issue",
  unresolved_critique: "Unresolved critique",
};
