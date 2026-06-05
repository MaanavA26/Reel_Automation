import type { ReactElement } from "react";

import type { ResearchResult } from "../../types/research";

import { CreatorPacketView } from "./CreatorPacketView";
import { CritiqueView } from "./CritiqueView";
import { FindingsView } from "./FindingsView";
import { PlanView } from "./PlanView";
import { ReportView } from "./ReportView";
import { SourcesView } from "./SourcesView";

interface ResearchResultViewProps {
  result: ResearchResult;
}

/**
 * Composes the rendered sections of a `ResearchState`: the reasoning surface
 * (plan, sources, findings, critique) followed by the publishing surface
 * (report, creator packet) when present. A job is single-report / single-packet,
 * so the publishing lists are read defensively at index 0 — an empty list is the
 * "publishing band has not run" signal. Pure presentation — it receives a
 * fully-resolved result and never touches the service layer (CLAUDE.md §10).
 */
export function ResearchResultView({
  result,
}: ResearchResultViewProps): ReactElement {
  const report = result.publishing.reports[0];
  const packet = result.publishing.packets[0];

  return (
    <div className="research-result">
      <header className="research-result__header">
        <span className={`job-status job-status--${result.status}`}>
          {result.status}
        </span>
        <h2 className="research-result__topic">{result.topic}</h2>
      </header>

      {result.error ? (
        <p className="research-alert research-alert--error" role="alert">
          {result.error}
        </p>
      ) : null}

      <PlanView plan={result.plan} />
      <SourcesView sources={result.acquisition.sources} />
      <FindingsView findings={result.reasoning.synthesis.findings} />
      <CritiqueView
        critiques={result.reasoning.critiques}
        subQuestions={result.plan.sub_questions}
      />
      {report ? <ReportView report={report} /> : null}
      {packet ? <CreatorPacketView packet={packet} /> : null}
    </div>
  );
}
