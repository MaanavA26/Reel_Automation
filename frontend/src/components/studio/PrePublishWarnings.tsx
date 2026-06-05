import type { ReactElement } from "react";

import type { Caveat, CreatorWarning } from "../../types/research";
import { CAVEAT_LABELS } from "../research/caveatLabels";

interface PrePublishWarningsProps {
  warnings: CreatorWarning[];
  caveats: Caveat[];
}

/**
 * The §11 publish gate's evidence block. Surfaces the creator packet's
 * code-derived unsafe/unverified-claim `warnings` and the report's `caveats`
 * **inside** the publish panel, adjacent to the publish action — never in a
 * far-away section a user can skip past. The backend mints both from the
 * reasoning state (the model gets no field to author or suppress them), so this
 * renders every one unconditionally with `role="alert"` treatment.
 *
 * Returns `null` only when there is genuinely nothing to flag — in which case
 * `PublishPanel` lets the operator proceed without an acknowledgment.
 */
export function PrePublishWarnings({
  warnings,
  caveats,
}: PrePublishWarningsProps): ReactElement | null {
  if (warnings.length === 0 && caveats.length === 0) {
    return null;
  }

  return (
    <div className="pre-publish-warnings" role="alert">
      <h4 className="pre-publish-warnings__title">
        Review before publishing{" "}
        <span className="research-count">
          {warnings.length + caveats.length}
        </span>
      </h4>
      <p className="pre-publish-warnings__lead">
        These claims are disputed, weakly supported, or otherwise unverified.
        Confirm you have reviewed them before publishing.
      </p>

      {warnings.length > 0 ? (
        <ul className="creator-warning-list">
          {warnings.map((warning, index) => (
            <li
              key={`warning-${index}`}
              className="creator-warning-card"
            >
              <span className="creator-warning-card__kind">
                {CAVEAT_LABELS[warning.kind]}
              </span>
              <span className="creator-warning-card__detail">
                {warning.detail}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {caveats.length > 0 ? (
        <ul className="caveat-list">
          {caveats.map((caveat, index) => (
            <li key={`caveat-${index}`} className="caveat-card">
              <span className="caveat-card__kind">
                {CAVEAT_LABELS[caveat.kind]}
              </span>
              <span className="caveat-card__detail">{caveat.detail}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
