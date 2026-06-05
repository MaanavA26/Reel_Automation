import type { ReactElement } from "react";

import type { ResearchResult } from "../../types/research";

import { CritiqueView } from "./CritiqueView";
import { FindingsView } from "./FindingsView";
import { PlanView } from "./PlanView";
import { SourcesView } from "./SourcesView";

interface ResearchResultViewProps {
  result: ResearchResult;
}

/**
 * Composes the four rendered sections of a `ResearchState` (plan, sources,
 * findings, critique). Pure presentation — it receives a fully-resolved result
 * and never touches the service layer (CLAUDE.md §10).
 */
export function ResearchResultView({
  result,
}: ResearchResultViewProps): ReactElement {
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
    </div>
  );
}
