import type { ComponentProps, ReactElement } from "react";

import { CreatorPacketView } from "../research/CreatorPacketView";
import { ReportView } from "../research/ReportView";
import type { VideoJob } from "../../types/video";

import { PipelineStages } from "./PipelineStages";
import { PublishPanel } from "./PublishPanel";
import { SchedulePanel } from "./SchedulePanel";
import { ScriptView } from "./ScriptView";

type PublishPanelHandler = ComponentProps<typeof PublishPanel>["onPublish"];
type SchedulePanelHandler = ComponentProps<typeof SchedulePanel>["onSchedule"];

interface VideoJobViewProps {
  job: VideoJob;
  /** Injectable service calls, threaded to the publish/schedule panels for tests. */
  onPublish?: PublishPanelHandler;
  onSchedule?: SchedulePanelHandler;
}

/**
 * Composes a `VideoJob` into the Studio's panels: the staged pipeline strip, the
 * report + creator-packet preview (reusing the existing Deep Research
 * components), the selected script, and the publish + schedule controls.
 *
 * The report/packet are read defensively at index 0 — an empty publishing list
 * is the "band has not run" signal — so the preview/publish controls only render
 * once the research tail has produced a packet. The packet's code-derived
 * warnings and the report's caveats are handed to the publish panel, which gates
 * the publish action behind them (CLAUDE.md §11). Pure presentation (§10).
 */
export function VideoJobView({
  job,
  onPublish,
  onSchedule,
}: VideoJobViewProps): ReactElement {
  const report = job.research.publishing.reports[0];
  const packet = job.research.publishing.packets[0];

  return (
    <div className="video-job">
      <header className="research-result__header">
        <span className={`job-status job-status--${job.status}`}>
          {job.status}
        </span>
        <h2 className="research-result__topic">{job.topic}</h2>
      </header>

      {job.error ? (
        <p className="research-alert research-alert--error" role="alert">
          {job.error}
        </p>
      ) : null}

      <PipelineStages stages={job.stages} />

      {report ? <ReportView report={report} /> : null}
      {packet ? <CreatorPacketView packet={packet} /> : null}
      {job.script ? <ScriptView script={job.script} video={job.video} /> : null}

      {packet ? (
        <div className="studio-publish-row">
          <PublishPanel
            jobId={job.id}
            warnings={packet.warnings}
            caveats={report ? report.caveats : []}
            onPublish={onPublish}
          />
          <SchedulePanel
            jobId={job.id}
            warnings={packet.warnings}
            caveats={report ? report.caveats : []}
            onSchedule={onSchedule}
          />
        </div>
      ) : null}
    </div>
  );
}
