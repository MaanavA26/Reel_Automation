/**
 * Frontend contracts for the Studio / video-production surface (roadmap M13).
 *
 * The Studio reuses the Deep Research contracts (`ResearchState`,
 * `CreatorPacket`, `Caveat`, `CreatorWarning`, `JobStatus`) rather than
 * redefining them — a video job *is* a research job whose creator packet has
 * been carried through the media tail (script → render). These types model only
 * what is genuinely new: the staged pipeline, the rendered-video descriptor, and
 * the publish/schedule request shapes.
 *
 * Wire conventions match `types/research.ts`:
 *
 * 1. **Wire casing is snake_case.** The backend Pydantic models declare no
 *    `alias_generator`, so FastAPI serializes snake_case. We match the wire so
 *    fields resolve instead of silently reading `undefined`.
 * 2. **Timestamps are ISO strings**, not `Date`.
 *
 * The video endpoints (`POST /api/v1/videos`, publish, schedule) land on sibling
 * branches. Until they do, these interfaces target the *documented* shapes
 * (`MediaPlan` / `RenderedVideo` in `backend/app/media/`, CLAUDE.md §3.3 publish
 * targets). If the backend wraps or renames, only `services/video.ts` changes.
 */

import type {
  CreatorPacket,
  JobStatus,
  Report,
  ResearchState,
} from "./research";

/**
 * The ordered stages of the video-production pipeline. Mirrors the system
 * pipeline: Deep Research → creator packet → script selection → media render.
 * `research` and `packet` are produced by the existing Deep Research bands;
 * `script` and `render` are the Media Production Layer tail (CLAUDE.md §3.3).
 */
export type VideoStageId = "research" | "packet" | "script" | "render";

/** A single pipeline stage with its independent status (priority = list order). */
export interface VideoStage {
  id: VideoStageId;
  label: string;
  status: JobStatus;
  /** Optional human-readable note (e.g. why a stage is pending or failed). */
  detail: string | null;
}

/**
 * The selected short-form script — the media-tail handoff. Deliberately *not* a
 * heavy invented schema: the script is the chosen `NarrativeOption.script_outline`
 * projected into ordered narration beats (`MediaPlan.script_segments` on the
 * backend), carrying the re-join key back to its packet/narrative.
 */
export interface VideoScript {
  narrative_title: string;
  /** Ordered narration beats split from the chosen narrative's outline. */
  script_segments: string[];
  source_packet_id: string;
}

/**
 * A rendered video artifact descriptor (mirrors backend `RenderedVideo`). Not the
 * bytes — where the assembled vertical short-form file lives plus the metadata a
 * publishing step needs.
 */
export interface RenderedVideo {
  id: string;
  video_uri: string;
  duration_ms: number;
  width: number;
  height: number;
  produced_via: string;
}

/**
 * A video job: the canonical `ResearchState` plus the media-tail artifacts and
 * the per-stage status strip the Studio renders. `script`/`video` are null until
 * their stages complete (defensive, mirroring the publishing-band empty-list
 * "step has not run" signal).
 */
export interface VideoJob {
  id: string;
  topic: string;
  status: JobStatus;
  created_at: string;
  updated_at: string;
  error: string | null;
  stages: VideoStage[];
  /** The underlying research state — drives the report/packet preview panels. */
  research: ResearchState;
  script: VideoScript | null;
  video: RenderedVideo | null;
}

/** Payload sent to the video submit endpoint. Mirrors `ResearchJobRequest`. */
export interface VideoJobRequest {
  topic: string;
}

/** Target platform for a publish action (CLAUDE.md §1 short-form targets). */
export type PublishPlatform = "youtube_shorts" | "instagram_reels";

/**
 * SEO / discovery metadata for a publish action. snake_case to match the wire;
 * the operator authors these in the publish panel before a (placeholder) publish.
 */
export interface PublishMetadata {
  title: string;
  description: string;
  tags: string[];
}

/** Payload to publish a rendered video to a platform. */
export interface PublishRequest {
  job_id: string;
  platform: PublishPlatform;
  metadata: PublishMetadata;
}

/** Result of a publish action (placeholder shape until the endpoint ships). */
export interface PublishResult {
  job_id: string;
  platform: PublishPlatform;
  status: "published" | "queued" | "failed";
  /** Where the published video lives on the platform, when available. */
  published_url: string | null;
  published_at: string | null;
}

/** Payload to schedule a publish for a future time. */
export interface ScheduleRequest {
  job_id: string;
  platform: PublishPlatform;
  metadata: PublishMetadata;
  /** ISO-8601 timestamp at which to publish. */
  scheduled_for: string;
}

/** Result of a schedule action (placeholder shape until the endpoint ships). */
export interface ScheduleResult {
  job_id: string;
  platform: PublishPlatform;
  status: "scheduled" | "failed";
  scheduled_for: string;
}

// Re-exported for the Studio preview panels, which compose the existing Deep
// Research components against these shapes.
export type { CreatorPacket, Report };
