/**
 * Service abstraction for the Studio / video endpoints (roadmap M13).
 *
 * Mirrors `services/research.ts` exactly: all backend access stays behind
 * `src/services`, presentation never calls `fetch` directly, and every method
 * accepts an injectable `transport` (defaulting to global `fetch`) so a caller
 * or test can substitute a stub without a live backend — the same seam that lets
 * the Studio run against the in-repo sample fixture offline.
 *
 * The video submit / publish / schedule routes land on sibling branches. Until
 * they do, these clients target the *assumed* contracts documented in
 * `types/video.ts`. If the backend wraps or renames a payload, only this file
 * changes.
 */

import type {
  PublishRequest,
  PublishResult,
  ScheduleRequest,
  ScheduleResult,
  VideoJob,
  VideoJobRequest,
} from "../types/video";

import { buildApiUrl } from "./api";

/** Minimal `fetch` shape this service depends on — keeps the seam mockable. */
export type FetchLike = (
  input: string,
  init?: RequestInit,
) => Promise<Response>;

export const VIDEO_SUBMIT_PATH = "/api/v1/videos";
export const VIDEO_PUBLISH_PATH = "/api/v1/videos/publish";
export const VIDEO_SCHEDULE_PATH = "/api/v1/videos/schedule";

/** Builds the id-addressable status path for a video job. */
export function videoJobPath(jobId: string): string {
  return `${VIDEO_SUBMIT_PATH}/${encodeURIComponent(jobId)}`;
}

/** Raised when a video endpoint returns a non-2xx response. */
export class VideoRequestError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "VideoRequestError";
    this.status = status;
  }
}

async function postJson<TBody, TResult>(
  path: string,
  body: TBody,
  transport: FetchLike,
  action: string,
): Promise<TResult> {
  const response = await transport(buildApiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw new VideoRequestError(
      response.status,
      `${action} failed (${response.status} ${response.statusText}).`,
    );
  }

  return (await response.json()) as TResult;
}

/**
 * Submit a topic and kick off a video-production job.
 *
 * @param topic The user-supplied topic/theme to turn into a video.
 * @param transport Injectable `fetch` implementation (defaults to global fetch).
 */
export async function submitVideoJob(
  topic: string,
  transport: FetchLike = fetch,
): Promise<VideoJob> {
  const body: VideoJobRequest = { topic };
  return postJson(VIDEO_SUBMIT_PATH, body, transport, "Video submit");
}

/**
 * Read a video job's current status / artifacts by id (status polling +
 * result reads share this endpoint, mirroring the research job store).
 */
export async function getVideoJob(
  jobId: string,
  transport: FetchLike = fetch,
): Promise<VideoJob> {
  const response = await transport(buildApiUrl(videoJobPath(jobId)));

  if (!response.ok) {
    throw new VideoRequestError(
      response.status,
      `Video status read failed (${response.status} ${response.statusText}).`,
    );
  }

  return (await response.json()) as VideoJob;
}

/** Publish a rendered video to a platform with the supplied SEO metadata. */
export async function publishVideo(
  request: PublishRequest,
  transport: FetchLike = fetch,
): Promise<PublishResult> {
  return postJson(VIDEO_PUBLISH_PATH, request, transport, "Publish");
}

/** Schedule a publish for a future time. */
export async function scheduleVideo(
  request: ScheduleRequest,
  transport: FetchLike = fetch,
): Promise<ScheduleResult> {
  return postJson(VIDEO_SCHEDULE_PATH, request, transport, "Schedule");
}
