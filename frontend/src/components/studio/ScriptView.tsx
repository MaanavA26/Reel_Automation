import type { ReactElement } from "react";

import type { RenderedVideo, VideoScript } from "../../types/video";

interface ScriptViewProps {
  script: VideoScript;
  video: RenderedVideo | null;
}

/** Formats a duration in milliseconds as `m:ss`. */
function formatDuration(durationMs: number): string {
  const totalSeconds = Math.round(durationMs / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

/**
 * Renders the selected short-form script — the chosen narrative title and its
 * ordered narration beats — plus the rendered-video descriptor when the render
 * stage has completed. The script is the media-tail handoff artifact, not an
 * invented schema. Pure presentation (CLAUDE.md §10).
 */
export function ScriptView({ script, video }: ScriptViewProps): ReactElement {
  return (
    <section className="research-section script-view">
      <h3 className="research-section__title">Script</h3>
      <p className="script-view__narrative">{script.narrative_title}</p>
      <ol className="script-beat-list">
        {script.script_segments.map((segment, index) => (
          <li key={`beat-${index}`} className="script-beat">
            {segment}
          </li>
        ))}
      </ol>

      {video ? (
        <div className="render-summary">
          <h4 className="packet-block__title">Rendered video</h4>
          <dl className="render-summary__grid">
            <div className="render-summary__item">
              <dt>Duration</dt>
              <dd>{formatDuration(video.duration_ms)}</dd>
            </div>
            <div className="render-summary__item">
              <dt>Dimensions</dt>
              <dd>
                {video.width}×{video.height}
              </dd>
            </div>
            <div className="render-summary__item">
              <dt>Produced via</dt>
              <dd>{video.produced_via}</dd>
            </div>
            <div className="render-summary__item render-summary__item--wide">
              <dt>File</dt>
              <dd className="render-summary__uri">{video.video_uri}</dd>
            </div>
          </dl>
        </div>
      ) : null}
    </section>
  );
}
