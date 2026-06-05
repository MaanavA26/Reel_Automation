import type { ReactElement } from "react";

import type { VideoStage } from "../../types/video";

interface PipelineStagesProps {
  stages: VideoStage[];
}

/**
 * Renders the video pipeline as an ordered status strip:
 * research → packet → script → render. Each stage carries its own `JobStatus`
 * (reusing the `job-status--*` treatment from the research surface), so a job
 * mid-flight shows which stage is running and a failed stage is legible inline.
 * Pure presentation; never touches the service layer (CLAUDE.md §10).
 */
export function PipelineStages({ stages }: PipelineStagesProps): ReactElement {
  return (
    <section className="research-section pipeline-stages">
      <h3 className="research-section__title">Pipeline</h3>
      <ol className="pipeline-stage-list">
        {stages.map((stage, index) => (
          <li key={stage.id} className="pipeline-stage">
            <span className="pipeline-stage__index">{index + 1}</span>
            <div className="pipeline-stage__body">
              <div className="pipeline-stage__head">
                <span className="pipeline-stage__label">{stage.label}</span>
                <span className={`job-status job-status--${stage.status}`}>
                  {stage.status}
                </span>
              </div>
              {stage.detail ? (
                <p className="pipeline-stage__detail">{stage.detail}</p>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
