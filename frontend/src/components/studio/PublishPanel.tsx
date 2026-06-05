import { useMemo, useState, type ReactElement } from "react";

import type { Caveat, CreatorWarning } from "../../types/research";
import type {
  PublishMetadata,
  PublishPlatform,
  PublishRequest,
  PublishResult,
} from "../../types/video";
import { publishVideo, VideoRequestError } from "../../services/video";

import { MetadataFields, parseTags } from "./MetadataFields";
import { PrePublishWarnings } from "./PrePublishWarnings";

interface PublishPanelProps {
  jobId: string;
  warnings: CreatorWarning[];
  caveats: Caveat[];
  /** Injectable publish call — defaults to the live (mockable) service. */
  onPublish?: (request: PublishRequest) => Promise<PublishResult>;
}

type PublishStatus = "idle" | "loading" | "success" | "error";

/**
 * The publish control. The load-bearing §11 requirement lives here: the
 * code-derived unsafe-claim warnings and report caveats render **inside** this
 * panel via `PrePublishWarnings`, adjacent to the publish button, and — when
 * there is anything to flag — the action is **gated behind an explicit
 * acknowledgment checkbox** (Publish stays disabled until the operator confirms
 * they have reviewed them). A clean job (no warnings/caveats) skips the gate.
 *
 * Backend access stays behind `services/video` (CLAUDE.md §10); the service call
 * is injectable so the surface works offline and is unit-testable.
 */
export function PublishPanel({
  jobId,
  warnings,
  caveats,
  onPublish = publishVideo,
}: PublishPanelProps): ReactElement {
  const [platform, setPlatform] = useState<PublishPlatform>("youtube_shorts");
  const [metadata, setMetadata] = useState<PublishMetadata>({
    title: "",
    description: "",
    tags: [],
  });
  const [tagsInput, setTagsInput] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [status, setStatus] = useState<PublishStatus>("idle");
  const [message, setMessage] = useState<string | null>(null);

  const hasFlags = warnings.length > 0 || caveats.length > 0;
  const canPublish = useMemo(
    () =>
      status !== "loading" &&
      metadata.title.trim().length > 0 &&
      (!hasFlags || acknowledged),
    [status, metadata.title, hasFlags, acknowledged],
  );

  async function handlePublish(): Promise<void> {
    if (!canPublish) {
      return;
    }
    setStatus("loading");
    setMessage(null);
    const request: PublishRequest = {
      job_id: jobId,
      platform,
      metadata: { ...metadata, tags: parseTags(tagsInput) },
    };
    try {
      const result = await onPublish(request);
      setStatus("success");
      setMessage(
        result.published_url
          ? `Published to ${result.platform}: ${result.published_url}`
          : `Publish ${result.status} for ${result.platform}.`,
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
    <section className="research-section publish-panel">
      <h3 className="research-section__title">Publish</h3>

      <PrePublishWarnings warnings={warnings} caveats={caveats} />

      <MetadataFields
        idPrefix="publish"
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
            for publishing them.
          </span>
        </label>
      ) : null}

      <button
        type="button"
        className="studio-action"
        disabled={!canPublish}
        onClick={handlePublish}
      >
        {status === "loading" ? "Publishing…" : "Publish"}
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
