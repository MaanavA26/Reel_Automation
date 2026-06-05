/**
 * Service abstraction for the Deep Research endpoint (roadmap M13).
 *
 * Mirrors the existing `services/api.ts` boundary: all backend access stays
 * behind `src/services`, presentation never calls `fetch` directly. The submit
 * route (`POST /api/v1/research`) lands in a sibling PR; this client targets the
 * assumed contract (`ResearchRequest` in, serialized `ResearchState` out).
 *
 * Mockability: `submitResearch` accepts an injectable `transport` (defaulting to
 * the global `fetch`), so a caller or test can substitute a stub without a live
 * backend. The same seam lets the UI run against the in-repo sample fixture.
 */

import type { ResearchRequest, ResearchResult } from "../types/research";

import { buildApiUrl } from "./api";

/** Minimal `fetch` shape this service depends on — keeps the seam mockable. */
export type FetchLike = (
  input: string,
  init?: RequestInit,
) => Promise<Response>;

export const RESEARCH_SUBMIT_PATH = "/api/v1/research";

/** Raised when the research endpoint returns a non-2xx response. */
export class ResearchRequestError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ResearchRequestError";
    this.status = status;
  }
}

/**
 * Submit a research topic and return the resulting `ResearchState`.
 *
 * @param topic The user-supplied research topic.
 * @param transport Injectable `fetch` implementation (defaults to global fetch).
 */
export async function submitResearch(
  topic: string,
  transport: FetchLike = fetch,
): Promise<ResearchResult> {
  const body: ResearchRequest = { topic };

  const response = await transport(buildApiUrl(RESEARCH_SUBMIT_PATH), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw new ResearchRequestError(
      response.status,
      `Research request failed (${response.status} ${response.statusText}).`,
    );
  }

  return (await response.json()) as ResearchResult;
}
