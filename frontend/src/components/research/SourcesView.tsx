import type { ReactElement } from "react";

import type { Source } from "../../types/research";

interface SourcesViewProps {
  sources: Source[];
}

/**
 * Renders discovered sources. `discovered_via` is shown as first-class
 * provenance (CLAUDE.md §11): a source is always tool-discovered, never minted
 * by a model, and the UI keeps that visible.
 */
export function SourcesView({ sources }: SourcesViewProps): ReactElement {
  return (
    <section className="research-section">
      <h3 className="research-section__title">
        Sources <span className="research-count">{sources.length}</span>
      </h3>
      {sources.length === 0 ? (
        <p className="research-empty">No sources were discovered.</p>
      ) : (
        <ul className="source-list">
          {sources.map((source) => (
            <li key={source.id} className="source-list__item">
              <div className="source-list__head">
                <span className="source-type">{source.type}</span>
                <a
                  className="source-list__link"
                  href={source.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {source.title ?? source.url}
                </a>
              </div>
              <p className="source-list__meta">via {source.discovered_via}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
