import { useState, type FormEvent, type ReactElement } from "react";

import { ResearchResultView } from "../components/research/ResearchResultView";
import { sampleResearch } from "../fixtures/sampleResearch";
import { submitResearch } from "../services/research";
import type { ResearchResult } from "../types/research";

type RequestStatus = "idle" | "loading" | "success" | "error";

/**
 * Deep Research submission + results view (roadmap M13).
 *
 * Owns the request lifecycle (idle → loading → success/error) and delegates all
 * rendering to `ResearchResultView`. Backend access stays behind
 * `services/research` (CLAUDE.md §10). A "Load sample result" affordance renders
 * the in-repo fixture so the surface works before the submit route ships.
 */
export function ResearchPage(): ReactElement {
  const [topic, setTopic] = useState("");
  const [status, setStatus] = useState<RequestStatus>("idle");
  const [result, setResult] = useState<ResearchResult | null>(null);
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
      const research = await submitResearch(trimmed);
      setResult(research);
      setStatus("success");
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : "Unexpected error.",
      );
      setStatus("error");
    }
  }

  function loadSample(): void {
    setResult(sampleResearch);
    setStatus("success");
    setErrorMessage(null);
  }

  return (
    <section className="research-page">
      <p className="research-page__lead">
        Submit a topic to run the Deep Research pipeline and review its plan,
        sources, findings, editorial critique, published report, and creator
        packet.
      </p>

      <form className="research-form" onSubmit={handleSubmit}>
        <label className="research-form__label" htmlFor="research-topic">
          Research topic
        </label>
        <div className="research-form__row">
          <input
            id="research-topic"
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
            {status === "loading" ? "Researching…" : "Run research"}
          </button>
        </div>
        <button
          className="research-form__sample"
          type="button"
          onClick={loadSample}
          disabled={status === "loading"}
        >
          Load sample result
        </button>
      </form>

      {status === "error" && errorMessage ? (
        <p className="research-alert research-alert--error" role="alert">
          {errorMessage}
        </p>
      ) : null}

      {status === "loading" ? (
        <p className="research-alert research-alert--loading">
          Running the research pipeline…
        </p>
      ) : null}

      {status === "success" && result ? (
        <ResearchResultView result={result} />
      ) : null}
    </section>
  );
}
