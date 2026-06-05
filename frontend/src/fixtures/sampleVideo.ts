/**
 * A sample `VideoJob` for rendering the Studio surface without a live backend
 * (the video routes land on sibling branches). Wired behind a "Load sample job"
 * affordance on `StudioPage` so the whole surface — staged pipeline, report /
 * packet / script preview, publish + schedule panels — can be exercised offline.
 *
 * It reuses `sampleResearch` verbatim as the underlying research state (the
 * Studio composes the existing Deep Research preview components against it), then
 * adds the media-tail artifacts. The job is **completed** so every preview panel
 * has finished artifacts to render and all four stages read "done"; the §11
 * disputed/weak-support warnings from the packet carry forward into the publish
 * gate.
 */

import type { VideoJob } from "../types/video";

import { sampleResearch } from "./sampleResearch";

export const sampleVideo: VideoJob = {
  id: "vid_job_0a1b2c3d4e5f",
  topic: sampleResearch.topic,
  status: "completed",
  created_at: "2026-06-05T09:00:00Z",
  updated_at: "2026-06-05T09:07:30Z",
  error: null,
  stages: [
    {
      id: "research",
      label: "Deep research",
      status: "completed",
      detail: "3 sources, 3 findings synthesized.",
    },
    {
      id: "packet",
      label: "Creator packet",
      status: "completed",
      detail: "Hooks, angles, and a narrative arc produced.",
    },
    {
      id: "script",
      label: "Script",
      status: "completed",
      detail: "Narrative selected and split into narration beats.",
    },
    {
      id: "render",
      label: "Render",
      status: "completed",
      detail: "Vertical 1080×1920 short-form file assembled.",
    },
  ],
  research: sampleResearch,
  script: {
    narrative_title: "The honest case for a shorter week",
    source_packet_id: "pkt_001",
    script_segments: [
      "Open on the productivity surprise.",
      "Acknowledge the contested durability of the gains.",
      "Close on what to watch for before adopting.",
    ],
  },
  video: {
    id: "vid_render_77aa88bb",
    video_uri: "file:///tmp/reel-automation/four-day-week.mp4",
    duration_ms: 42_000,
    width: 1080,
    height: 1920,
    produced_via: "composition:ffmpeg",
  },
};
