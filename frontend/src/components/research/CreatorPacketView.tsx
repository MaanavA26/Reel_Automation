import type { ReactElement } from "react";

import type { CreatorPacket, CreatorWarning } from "../../types/research";

import { CAVEAT_LABELS } from "./caveatLabels";
import { SupportBadge } from "./SupportBadge";

interface CreatorPacketViewProps {
  packet: CreatorPacket;
}

/**
 * Renders the short-form `CreatorPacket`: hooks, content angles, narrative
 * options, a code-derived key-fact sheet, and prominently-flagged
 * unsafe/unverified-claim warnings (CLAUDE.md §11).
 *
 * The warnings are code-derived from the *full* synthesis — independent of which
 * findings a given hook/angle/narrative cites — so a punchy hook cannot quietly
 * rest on a contradicted or single-source finding. We mirror the backend's
 * shared-`finding_ids` cross-link: any creative element whose `finding_ids`
 * intersect a warning's `finding_ids` is flagged inline, not just listed in the
 * warnings section.
 *
 * Pure presentation; never touches the service layer (CLAUDE.md §10).
 */
export function CreatorPacketView({
  packet,
}: CreatorPacketViewProps): ReactElement {
  const warnedFindingIds = new Set(
    packet.warnings.flatMap((warning) => warning.finding_ids),
  );
  const isWarned = (findingIds: string[]): boolean =>
    findingIds.some((id) => warnedFindingIds.has(id));

  return (
    <section className="research-section creator-packet">
      <h3 className="research-section__title">Creator packet</h3>

      {packet.warnings.length > 0 ? (
        <div className="creator-warnings" role="alert">
          <h4 className="creator-warnings__title">
            Unsafe / unverified claim warnings{" "}
            <span className="research-count">{packet.warnings.length}</span>
          </h4>
          <ul className="creator-warning-list">
            {packet.warnings.map((warning: CreatorWarning, index) => (
              <li
                key={`${warning.kind}-${index}`}
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
        </div>
      ) : null}

      {packet.hooks.length > 0 ? (
        <div className="packet-block">
          <h4 className="packet-block__title">Hooks</h4>
          <ul className="packet-list">
            {packet.hooks.map((hook, index) => (
              <li key={`hook-${index}`} className="packet-item">
                <p className="packet-item__text">{hook.text}</p>
                {isWarned(hook.finding_ids) ? (
                  <span className="packet-flag">Rests on a flagged finding</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {packet.angles.length > 0 ? (
        <div className="packet-block">
          <h4 className="packet-block__title">Content angles</h4>
          <ul className="packet-list">
            {packet.angles.map((angle, index) => (
              <li key={`angle-${index}`} className="packet-item">
                <p className="packet-item__text">{angle.angle}</p>
                <p className="packet-item__detail">{angle.rationale}</p>
                {isWarned(angle.finding_ids) ? (
                  <span className="packet-flag">Rests on a flagged finding</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {packet.narratives.length > 0 ? (
        <div className="packet-block">
          <h4 className="packet-block__title">Narrative options</h4>
          <ul className="packet-list">
            {packet.narratives.map((narrative, index) => (
              <li key={`narrative-${index}`} className="packet-item">
                <p className="packet-item__text">{narrative.title}</p>
                <p className="packet-item__detail">{narrative.script_outline}</p>
                {isWarned(narrative.finding_ids) ? (
                  <span className="packet-flag">Rests on a flagged finding</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {packet.key_facts.length > 0 ? (
        <div className="packet-block">
          <h4 className="packet-block__title">Key facts</h4>
          <ul className="packet-list">
            {packet.key_facts.map((fact) => (
              <li key={fact.finding_id} className="packet-item packet-item--fact">
                <div className="packet-item__head">
                  <SupportBadge level={fact.weakest_support} />
                  {fact.disputed ? (
                    <span className="finding-flag">Disputed across sources</span>
                  ) : null}
                </div>
                <p className="packet-item__text">{fact.statement}</p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
