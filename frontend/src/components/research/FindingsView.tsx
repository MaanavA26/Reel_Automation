import type { ReactElement } from "react";

import type { Finding } from "../../types/research";

import { SupportBadge } from "./SupportBadge";

interface FindingsViewProps {
  findings: Finding[];
}

/**
 * Renders synthesized findings with their honest grounding flags
 * (CLAUDE.md §11). `weakest_support` drives the support badge and `disputed`
 * draws an explicit contradiction caveat — both are code-derived on the
 * backend, so the UI surfaces them rather than re-deriving or hiding them.
 */
export function FindingsView({ findings }: FindingsViewProps): ReactElement {
  return (
    <section className="research-section">
      <h3 className="research-section__title">
        Findings <span className="research-count">{findings.length}</span>
      </h3>
      {findings.length === 0 ? (
        <p className="research-empty">Synthesis produced no findings.</p>
      ) : (
        <ul className="finding-list">
          {findings.map((finding) => (
            <li
              key={finding.id}
              className={
                finding.disputed
                  ? "finding-card finding-card--disputed"
                  : "finding-card"
              }
            >
              <div className="finding-card__head">
                <SupportBadge level={finding.weakest_support} />
                {finding.disputed ? (
                  <span className="finding-flag">Disputed across sources</span>
                ) : null}
              </div>
              <p className="finding-card__statement">{finding.statement}</p>
              {finding.detail ? (
                <p className="finding-card__detail">{finding.detail}</p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
