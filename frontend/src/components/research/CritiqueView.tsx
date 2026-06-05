import type { ReactElement } from "react";

import type { Critique, SubQuestion } from "../../types/research";

interface CritiqueViewProps {
  /** The reasoning band holds a critique per revision iteration. */
  critiques: Critique[];
  /** Used to resolve uncovered sub-question ids to readable text. */
  subQuestions: SubQuestion[];
}

/**
 * Renders the editorial critique. The reasoning band appends one critique per
 * revision iteration (M10b); the latest is the operative assessment, so it is
 * shown with an iteration count for honesty. Coverage gaps
 * (`uncovered_sub_question_ids`) are surfaced explicitly — they are
 * code-derived structural facts, not model opinion (CLAUDE.md §11).
 */
export function CritiqueView({
  critiques,
  subQuestions,
}: CritiqueViewProps): ReactElement | null {
  if (critiques.length === 0) {
    return null;
  }

  const latest = critiques[critiques.length - 1];
  const subQuestionText = new Map(
    subQuestions.map((subQuestion) => [subQuestion.id, subQuestion.text]),
  );

  return (
    <section className="research-section">
      <h3 className="research-section__title">Editorial critique</h3>
      <div className="critique-head">
        <span className={`critique-decision critique-decision--${latest.decision}`}>
          {latest.decision}
        </span>
        {critiques.length > 1 ? (
          <span className="critique-iteration">
            after {critiques.length} review passes
          </span>
        ) : null}
      </div>
      <p className="critique-rationale">{latest.rationale}</p>

      {latest.uncovered_sub_question_ids.length > 0 ? (
        <div className="critique-block">
          <h4 className="critique-block__title">Coverage gaps</h4>
          <ul className="critique-gaps">
            {latest.uncovered_sub_question_ids.map((id) => (
              <li key={id}>{subQuestionText.get(id) ?? id}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {latest.issues.length > 0 ? (
        <div className="critique-block">
          <h4 className="critique-block__title">Quality issues</h4>
          <ul className="critique-issues">
            {latest.issues.map((issue, index) => (
              <li key={`${issue.kind}-${index}`} className="critique-issue">
                <span className="critique-issue__kind">{issue.kind}</span>
                <span className="critique-issue__detail">{issue.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
