import type { ReactElement } from "react";

import type { Report } from "../../types/research";

import { CaveatsPanel } from "./CaveatsPanel";

interface ReportViewProps {
  report: Report;
}

/**
 * Renders the published `Report`: model-authored title/abstract/sections, a
 * code-resolved References list from the citation bibliography, and the
 * non-omittable `CaveatsPanel`. Pure presentation — it receives a fully-resolved
 * report and never touches the service layer (CLAUDE.md §10).
 *
 * Citations carry a code-copied source snapshot (never model-authored), so the
 * References list is safe to render directly as the report's provenance trail
 * (CLAUDE.md §11).
 */
export function ReportView({ report }: ReportViewProps): ReactElement {
  return (
    <section className="research-section report-view">
      <h3 className="research-section__title">Published report</h3>
      <h4 className="report-view__title">{report.title}</h4>
      <p className="report-view__abstract">{report.abstract}</p>

      {report.sections.length > 0 ? (
        <div className="report-view__sections">
          {report.sections.map((section) => (
            <article key={section.id} className="report-section-block">
              <h5 className="report-section-block__heading">{section.heading}</h5>
              <p className="report-section-block__narrative">{section.narrative}</p>
            </article>
          ))}
        </div>
      ) : null}

      {report.citations.length > 0 ? (
        <div className="report-view__references">
          <h4 className="report-view__references-title">
            References{" "}
            <span className="research-count">{report.citations.length}</span>
          </h4>
          <ol className="reference-list">
            {report.citations.map((citation) => (
              <li key={citation.id} className="reference-list__item">
                <span className="source-type">{citation.source_type}</span>
                <a
                  className="source-list__link"
                  href={citation.source_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {citation.title ?? citation.source_url}
                </a>
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      <CaveatsPanel caveats={report.caveats} />
    </section>
  );
}
