import type { ReactElement } from "react";

import type { SupportLevel } from "../../types/research";

const SUPPORT_LABELS: Record<SupportLevel, string> = {
  corroborated: "Corroborated",
  single_source: "Single source",
  contradicted: "Contradicted",
};

interface SupportBadgeProps {
  level: SupportLevel;
}

/**
 * Renders a claim's structural support level honestly (CLAUDE.md §11): each
 * level is visually distinct so a single-source or contradicted finding is never
 * mistaken for a corroborated one. The modifier class drives the colour.
 */
export function SupportBadge({ level }: SupportBadgeProps): ReactElement {
  return (
    <span className={`support-badge support-badge--${level}`}>
      {SUPPORT_LABELS[level]}
    </span>
  );
}
