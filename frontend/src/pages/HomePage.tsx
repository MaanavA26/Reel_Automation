import type { ReactElement } from "react";

import { buildApiUrl } from "../services/api";

export function HomePage(): ReactElement {
  return (
    <section className="home-page">
      <p className="home-page__description">
        This frontend currently provides only the minimum typed shell required
        to support future research, media, and publishing workflows.
      </p>
      <h2 className="home-page__section-title">Initial boundaries</h2>
      <ul className="home-page__list">
        <li>Reusable UI components live in `src/components`.</li>
        <li>Route-level views live in `src/pages`.</li>
        <li>Backend access stays behind `src/services`.</li>
        <li>Shared frontend contracts live in `src/types`.</li>
      </ul>
      <p className="home-page__footer">
        Backend health endpoint: {buildApiUrl("/api/v1/health")}
      </p>
    </section>
  );
}
