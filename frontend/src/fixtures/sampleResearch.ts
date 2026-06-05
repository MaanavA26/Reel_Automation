/**
 * A sample serialized `ResearchState` for rendering the Deep Research surface
 * without a live backend (the submit route lands in a sibling PR). It is wired
 * behind a "Load sample result" affordance on `ResearchPage` so the rendering
 * can be exercised offline.
 *
 * The fixture deliberately spans the honest-grounding cases CLAUDE.md §11 asks
 * the UI to distinguish: a corroborated finding, a single-source finding, and a
 * disputed (contradicted) finding, plus a sub-question left uncovered by the
 * synthesis and surfaced by the critique.
 */

import type { ResearchResult } from "../types/research";

export const sampleResearch: ResearchResult = {
  id: "job_0a1b2c3d4e5f6071",
  topic: "How effective are four-day work weeks?",
  status: "completed",
  created_at: "2026-06-05T09:00:00Z",
  updated_at: "2026-06-05T09:04:12Z",
  error: null,
  revision_iteration: 1,
  plan: {
    id: "plan_11223344aabbccdd",
    goal: "Assess productivity, wellbeing, and adoption trade-offs of a four-day work week.",
    created_at: "2026-06-05T09:00:05Z",
    sub_questions: [
      {
        id: "sq_aaa1",
        text: "Does a four-day work week affect employee productivity?",
        rationale: "Productivity is the core business objection to adoption.",
      },
      {
        id: "sq_bbb2",
        text: "What is the impact on employee wellbeing and burnout?",
        rationale: null,
      },
      {
        id: "sq_ccc3",
        text: "How do four-day-week outcomes differ across industries?",
        rationale: "Sector mix likely moderates the headline effect.",
      },
    ],
  },
  acquisition: {
    sources: [
      {
        id: "src_web01",
        url: "https://example.org/four-day-week-uk-trial",
        type: "web",
        discovered_via: "search:fake",
        title: "UK four-day week trial results",
        discovered_at: "2026-06-05T09:01:10Z",
        raw_metadata: {},
      },
      {
        id: "src_pap02",
        url: "https://example.org/papers/compressed-schedules-wellbeing",
        type: "paper",
        discovered_via: "search:fake",
        title: "Compressed schedules and wellbeing: a longitudinal study",
        discovered_at: "2026-06-05T09:01:12Z",
        raw_metadata: {},
      },
      {
        id: "src_yt03",
        url: "https://example.org/watch?v=fourdaywk",
        type: "youtube",
        discovered_via: "search:fake",
        title: null,
        discovered_at: "2026-06-05T09:01:14Z",
        raw_metadata: {},
      },
    ],
  },
  reasoning: {
    synthesis: {
      findings: [
        {
          id: "fnd_001",
          statement:
            "Multiple trials report stable or higher output despite fewer hours.",
          detail:
            "Large pilots in the UK and Iceland recorded maintained or improved productivity over the trial window.",
          sub_question_ids: ["sq_aaa1"],
          supporting_verdict_ids: ["vd_a", "vd_b"],
          disputed: false,
          weakest_support: "corroborated",
          synthesized_at: "2026-06-05T09:03:40Z",
          synthesized_via: "synthesis:fake",
        },
        {
          id: "fnd_002",
          statement:
            "Self-reported burnout and stress fall under a compressed schedule.",
          detail: null,
          sub_question_ids: ["sq_bbb2"],
          supporting_verdict_ids: ["vd_c"],
          disputed: false,
          weakest_support: "single_source",
          synthesized_at: "2026-06-05T09:03:41Z",
          synthesized_via: "synthesis:fake",
        },
        {
          id: "fnd_003",
          statement:
            "Whether output gains persist long-term is contested across studies.",
          detail:
            "One longitudinal source reports decay after the novelty period; another finds durable gains.",
          sub_question_ids: ["sq_aaa1"],
          supporting_verdict_ids: ["vd_d", "vd_e"],
          disputed: true,
          weakest_support: "contradicted",
          synthesized_at: "2026-06-05T09:03:42Z",
          synthesized_via: "synthesis:fake",
        },
      ],
    },
    critiques: [
      {
        id: "crit_001",
        decision: "revise",
        uncovered_sub_question_ids: ["sq_ccc3"],
        issues: [
          {
            kind: "imbalanced",
            detail:
              "Productivity is answered from a single perspective; the contested long-term view is under-weighted.",
            finding_ids: ["fnd_001"],
            sub_question_ids: ["sq_aaa1"],
          },
        ],
        rationale:
          "Coverage gap on industry variation and an imbalanced productivity answer warrant a revision pass.",
        critiqued_at: "2026-06-05T09:04:00Z",
        critiqued_via: "critic:fake",
      },
    ],
  },
};
