import type { ReactElement } from "react";

import type { ResearchPlan } from "../../types/research";

interface PlanViewProps {
  plan: ResearchPlan;
}

/** Renders the research plan: optional refined goal + ordered sub-questions. */
export function PlanView({ plan }: PlanViewProps): ReactElement {
  return (
    <section className="research-section">
      <h3 className="research-section__title">Research plan</h3>
      {plan.goal ? <p className="research-section__lead">{plan.goal}</p> : null}
      {plan.sub_questions.length === 0 ? (
        <p className="research-empty">No sub-questions were produced.</p>
      ) : (
        <ol className="plan-list">
          {plan.sub_questions.map((subQuestion) => (
            <li key={subQuestion.id} className="plan-list__item">
              <p className="plan-list__question">{subQuestion.text}</p>
              {subQuestion.rationale ? (
                <p className="plan-list__rationale">{subQuestion.rationale}</p>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
