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
 *
 * The `publishing` block exercises the M11/M12 surface the same way: a report
 * whose code-derived `caveats` carry the disputed/weak-support/uncovered cases
 * forward, and a creator packet whose code-derived `warnings` cross-link (by
 * shared `finding_ids`) to a hook and a narrative resting on a flagged finding.
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
  publishing: {
    reports: [
      {
        id: "rpt_001",
        title: "Four-day work weeks: productivity holds, durability contested",
        abstract:
          "Across multiple trials, output was maintained or improved on a compressed schedule and self-reported burnout fell. Whether the gains persist beyond the novelty period remains contested, and sector-level variation was not covered.",
        published_at: "2026-06-05T09:04:30Z",
        published_via: "publisher:fake",
        sections: [
          {
            id: "sec_001",
            heading: "Productivity under a compressed schedule",
            narrative:
              "Large pilots in the UK and Iceland recorded maintained or improved productivity over the trial window, though one longitudinal source reports decay after the initial period.",
            finding_ids: ["fnd_001", "fnd_003"],
            sub_question_ids: ["sq_aaa1"],
          },
          {
            id: "sec_002",
            heading: "Wellbeing and burnout",
            narrative:
              "Self-reported burnout and stress fell under a compressed schedule, though this rests on a single source.",
            finding_ids: ["fnd_002"],
            sub_question_ids: ["sq_bbb2"],
          },
        ],
        citations: [
          {
            id: "cit_001",
            source_id: "src_web01",
            source_url: "https://example.org/four-day-week-uk-trial",
            source_type: "web",
            title: "UK four-day week trial results",
            evidence_ids: ["ev_a"],
            verdict_ids: ["vd_a"],
          },
          {
            id: "cit_002",
            source_id: "src_pap02",
            source_url:
              "https://example.org/papers/compressed-schedules-wellbeing",
            source_type: "paper",
            title: "Compressed schedules and wellbeing: a longitudinal study",
            evidence_ids: ["ev_c"],
            verdict_ids: ["vd_c"],
          },
        ],
        caveats: [
          {
            kind: "disputed_finding",
            detail:
              "A finding rests on contradictory sources: whether output gains persist long-term is contested across studies.",
            finding_ids: ["fnd_003"],
            sub_question_ids: ["sq_aaa1"],
            critique_id: null,
          },
          {
            kind: "weak_support",
            detail:
              "A finding is supported by a single source: self-reported burnout and stress fall under a compressed schedule.",
            finding_ids: ["fnd_002"],
            sub_question_ids: ["sq_bbb2"],
            critique_id: null,
          },
          {
            kind: "uncovered_sub_question",
            detail:
              "A sub-question was left uncovered by the synthesis: how do four-day-week outcomes differ across industries?",
            finding_ids: [],
            sub_question_ids: ["sq_ccc3"],
            critique_id: "crit_001",
          },
        ],
      },
    ],
    packets: [
      {
        id: "pkt_001",
        report_id: "rpt_001",
        created_at: "2026-06-05T09:05:00Z",
        published_via: "strategist:fake",
        hooks: [
          {
            text: "What if you could work one less day a week and get more done?",
            finding_ids: ["fnd_001"],
          },
          {
            text: "The four-day week boosted output — but does the effect actually last?",
            finding_ids: ["fnd_003"],
          },
        ],
        angles: [
          {
            angle: "Productivity is not about hours in the seat.",
            rationale:
              "Trials maintained output on fewer hours, reframing productivity around focus over time.",
            finding_ids: ["fnd_001"],
          },
        ],
        narratives: [
          {
            title: "The honest case for a shorter week",
            script_outline:
              "Open on the productivity surprise, acknowledge the contested durability, close on what to watch for.",
            finding_ids: ["fnd_001", "fnd_003"],
          },
        ],
        key_facts: [
          {
            statement:
              "Multiple trials report stable or higher output despite fewer hours.",
            finding_id: "fnd_001",
            disputed: false,
            weakest_support: "corroborated",
          },
          {
            statement:
              "Whether output gains persist long-term is contested across studies.",
            finding_id: "fnd_003",
            disputed: true,
            weakest_support: "contradicted",
          },
        ],
        warnings: [
          {
            kind: "disputed_finding",
            detail:
              "An unsafe claim: durability of output gains is contested across sources — do not present it as settled.",
            finding_ids: ["fnd_003"],
          },
          {
            kind: "weak_support",
            detail:
              "A thinly-supported claim: the burnout reduction rests on a single source.",
            finding_ids: ["fnd_002"],
          },
        ],
      },
    ],
  },
};
