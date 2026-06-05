import type { ReactElement } from "react";

import type { Caveat } from "../../types/research";

import { CAVEAT_LABELS } from "./caveatLabels";

interface CaveatsPanelProps {
  caveats: Caveat[];
}

/**
 * Renders a report's code-derived limitations as a non-omittable panel
 * (CLAUDE.md §11). The backend mints `caveats` from the reasoning state — the
 * model gets no field to author or suppress them — so the UI surfaces every one
 * unconditionally and with distinct warning treatment. A polished abstract can
 * still overstate; this panel is the structural counterweight, never collapsed
 * or buried below the narrative.
 *
 * Returns `null` only when there are genuinely no caveats (a clean report), not
 * as a way to hide them.
 */
export function CaveatsPanel({ caveats }: CaveatsPanelProps): ReactElement | null {
  if (caveats.length === 0) {
    return null;
  }

  return (
    <section className="research-section caveats-panel" role="alert">
      <h3 className="research-section__title">
        Limitations &amp; caveats{" "}
        <span className="research-count">{caveats.length}</span>
      </h3>
      <ul className="caveat-list">
        {caveats.map((caveat, index) => (
          <li key={`${caveat.kind}-${index}`} className="caveat-card">
            <span className="caveat-card__kind">{CAVEAT_LABELS[caveat.kind]}</span>
            <span className="caveat-card__detail">{caveat.detail}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
