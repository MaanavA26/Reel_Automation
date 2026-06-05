import { useMemo, useState, type ReactElement } from "react";

import type { Caveat, CreatorWarning } from "../../types/research";
import type {
  PublishMetadata,
  PublishPlatform,
  ScheduleRequest,
  ScheduleResult,
} from "../../types/video";
import { scheduleVideo, VideoRequestError } from "../../services/video";

import { MetadataFields, parseTags } from "./MetadataFields";
import { PrePublishWarnings } from "./PrePublishWarnings";

interface SchedulePanelProps {
  jobId: string;
  warnings: CreatorWarning[];
  caveats: Caveat[];
  /** Injectable schedule call — defaults to the live (mockable) service. */
  onSchedule?: (request: ScheduleRequest) => Promise<ScheduleResult>;
}

type ScheduleStatus = "idle" | "loading" | "success" | "error";

/** Converts a `datetime-local` value (no zone) to an ISO-8601 string. */
function toIso(localValue: string): string | null {
  if (localValue.length === 0) {
    return null;
  }
  const parsed = new Date(localValue);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

/**
 * The schedule control: target platform + SEO metadata + a future publish time,
 * calling the typed, mockable `scheduleVideo` service. A placeholder for the
 * scheduling endpoint that lands on a sibling branch — the contract is real
 * (`ScheduleRequest`/`ScheduleResult`), the wiring is injectable for offline use.
 *
 * A scheduled publish **is** a publish action, so the §11 gate applies here too:
 * the code-derived unsafe-claim warnings and report caveats render inside this
 * panel and — when anything is flagged — scheduling is disabled until the
 * operator explicitly acknowledges them, mirroring `PublishPanel`.
 */
export function SchedulePanel({
  jobId,
  warnings,
  caveats,
  onSchedule = scheduleVideo,
}: SchedulePanelProps): ReactElement {
  const [platform, setPlatform] = useState<PublishPlatform>("youtube_shorts");
  const [metadata, setMetadata] = useState<PublishMetadata>({
    title: "",
    description: "",
    tags: [],
  });
  const [tagsInput, setTagsInput] = useState("");
  const [scheduledForLocal, setScheduledForLocal] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [status, setStatus] = useState<ScheduleStatus>("idle");
  const [message, setMessage] = useState<string | null>(null);

  const hasFlags = warnings.length > 0 || caveats.length > 0;
  const scheduledForIso = toIso(scheduledForLocal);
  const canSchedule = useMemo(
    () =>
      status !== "loading" &&
      metadata.title.trim().length > 0 &&
      scheduledForIso !== null &&
      (!hasFlags || acknowledged),
    [status, metadata.title, scheduledForIso, hasFlags, acknowledged],
  );

  async function handleSchedule(): Promise<void> {
    if (!canSchedule || scheduledForIso === null) {
      return;
    }
    setStatus("loading");
    setMessage(null);
    const request: ScheduleRequest = {
      job_id: jobId,
      platform,
      metadata: { ...metadata, tags: parseTags(tagsInput) },
      scheduled_for: scheduledForIso,
    };
    try {
      const result = await onSchedule(request);
      setStatus("success");
      setMessage(
        `Scheduled for ${result.platform} at ${result.scheduled_for}.`,
      );
    } catch (error) {
      setStatus("error");
      setMessage(
        error instanceof VideoRequestError || error instanceof Error
          ? error.message
          : "Unexpected error.",
      );
    }
  }

  return (
    <section className="research-section schedule-panel">
      <h3 className="research-section__title">Schedule</h3>

      <PrePublishWarnings warnings={warnings} caveats={caveats} />

      <MetadataFields
        idPrefix="schedule"
        platform={platform}
        metadata={metadata}
        tagsInput={tagsInput}
        disabled={status === "loading"}
        onPlatformChange={setPlatform}
        onTitleChange={(title) => setMetadata((m) => ({ ...m, title }))}
        onDescriptionChange={(description) =>
          setMetadata((m) => ({ ...m, description }))
        }
        onTagsInputChange={setTagsInput}
      />

      <div className="metadata-field">
        <label className="metadata-field__label" htmlFor="schedule-when">
          Publish at
        </label>
        <input
          id="schedule-when"
          className="metadata-field__input"
          type="datetime-local"
          value={scheduledForLocal}
          disabled={status === "loading"}
          onChange={(event) => setScheduledForLocal(event.target.value)}
        />
      </div>

      {hasFlags ? (
        <label className="publish-ack">
          <input
            type="checkbox"
            checked={acknowledged}
            disabled={status === "loading"}
            onChange={(event) => setAcknowledged(event.target.checked)}
          />
          <span>
            I have reviewed the flagged claims above and accept responsibility
            for scheduling them for publication.
          </span>
        </label>
      ) : null}

      <button
        type="button"
        className="studio-action studio-action--secondary"
        disabled={!canSchedule}
        onClick={handleSchedule}
      >
        {status === "loading" ? "Scheduling…" : "Schedule"}
      </button>

      {message ? (
        <p
          className={
            status === "error"
              ? "research-alert research-alert--error"
              : "research-alert research-alert--loading"
          }
          role={status === "error" ? "alert" : undefined}
        >
          {message}
        </p>
      ) : null}
    </section>
  );
}
