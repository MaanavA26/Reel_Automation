import { useState, type FormEvent, type ReactElement } from "react";

import { VideoJobView } from "../components/studio/VideoJobView";
import { sampleVideo } from "../fixtures/sampleVideo";
import { submitVideoJob } from "../services/video";
import type { VideoJob } from "../types/video";

type RequestStatus = "idle" | "loading" | "success" | "error";

/**
 * Studio view (roadmap M13) — the operator's control panel for producing and
 * publishing videos. Owns the submit lifecycle (idle → loading → success/error)
 * and delegates all rendering to `VideoJobView`. Backend access stays behind
 * `services/video` (CLAUDE.md §10).
 *
 * The video routes land on sibling branches; a "Load sample job" affordance
 * renders the in-repo fixture so the whole surface — pipeline, preview, publish,
 * schedule — works offline before the endpoints ship.
 */
export function StudioPage(): ReactElement {
  const [topic, setTopic] = useState("");
  const [status, setStatus] = useState<RequestStatus>("idle");
  const [job, setJob] = useState<VideoJob | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const trimmed = topic.trim();
    if (trimmed.length === 0 || status === "loading") {
      return;
    }

    setStatus("loading");
    setErrorMessage(null);
    try {
      const result = await submitVideoJob(trimmed);
      setJob(result);
      setStatus("success");
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : "Unexpected error.",
      );
      setStatus("error");
    }
  }

  function loadSample(): void {
    setJob(sampleVideo);
    setStatus("success");
    setErrorMessage(null);
  }

  return (
    <section className="studio-page">
      <p className="research-page__lead">
        Enter a topic to kick off a video job. Track the pipeline (research →
        packet → script → render), preview the report, packet, and script, then
        publish or schedule to a platform — after reviewing the flagged claims.
      </p>

      <form className="research-form" onSubmit={handleSubmit}>
        <label className="research-form__label" htmlFor="studio-topic">
          Video topic
        </label>
        <div className="research-form__row">
          <input
            id="studio-topic"
            className="research-form__input"
            type="text"
            value={topic}
            placeholder="e.g. How effective are four-day work weeks?"
            onChange={(event) => setTopic(event.target.value)}
            disabled={status === "loading"}
          />
          <button
            className="research-form__submit"
            type="submit"
            disabled={status === "loading" || topic.trim().length === 0}
          >
            {status === "loading" ? "Starting…" : "Start video"}
          </button>
        </div>
        <button
          className="research-form__sample"
          type="button"
          onClick={loadSample}
          disabled={status === "loading"}
        >
          Load sample job
        </button>
      </form>

      {status === "error" && errorMessage ? (
        <p className="research-alert research-alert--error" role="alert">
          {errorMessage}
        </p>
      ) : null}

      {status === "loading" ? (
        <p className="research-alert research-alert--loading">
          Kicking off the video pipeline…
        </p>
      ) : null}

      {status === "success" && job ? <VideoJobView job={job} /> : null}
    </section>
  );
}
