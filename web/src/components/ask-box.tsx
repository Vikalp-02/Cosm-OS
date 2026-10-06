"use client";

import Link from "next/link";
import { type FormEvent, useRef, useState } from "react";

type Citation = { reference: string; headline: string };
type Status = "answered" | "listed" | "no_match" | "off_topic" | "unavailable";
type Turn = { question: string; status: Status; text: string; citations: Citation[] };
type StreamEvent =
  | { type: "progress"; text: string }
  | { type: "answer"; status: Status; text: string; citations: Citation[] };

const MAX_QUESTION = 500;
// Earlier turns sent along so a follow-up such as "and on Zepto?" makes sense.
const REMEMBERED_TURNS = 4;

/**
 * A question box over the tenant's leaks. Answers arrive as a stream of
 * progress lines followed by the answer, so the wait is never silent.
 */
export function AskBox({
  reference,
  suggestions,
}: {
  /** The leak this page is about, when the box sits on a report. */
  reference?: string;
  suggestions: string[];
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<{ question: string; progress: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);

  async function submit(text: string) {
    const asked = text.trim();
    if (asked.length < 2 || pending) return;
    setError(null);
    setQuestion("");
    setPending({ question: asked, progress: "Reading your question" });
    const controller = new AbortController();
    request.current = controller;

    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "content-type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          question: asked,
          reference: reference ?? null,
          history: turns
            .filter((turn) => turn.status === "answered" || turn.status === "listed")
            .slice(-REMEMBERED_TURNS)
            .map((turn) => ({
              question: turn.question,
              answer: turn.text,
              references: turn.citations.map((citation) => citation.reference),
            })),
        }),
      });
      if (!response.ok || !response.body) {
        const body: unknown = await response.json().catch(() => null);
        const detail = (body as { detail?: unknown } | null)?.detail;
        throw new Failure(
          typeof detail === "string" ? detail : "That didn't work. Please try again.",
        );
      }

      let answered = false;
      for await (const event of events(response.body)) {
        if (event.type === "progress") {
          setPending({ question: asked, progress: event.text });
        } else {
          answered = true;
          setTurns((earlier) => [
            ...earlier,
            { question: asked, status: event.status, text: event.text, citations: event.citations },
          ]);
        }
      }
      if (!answered) throw new Failure("The answer was cut off. Please try again.");
    } catch (failure) {
      if (controller.signal.aborted) {
        setQuestion(asked); // stopped by the reader: hand the question back
      } else {
        setQuestion(asked);
        setError(
          failure instanceof Failure
            ? failure.message
            : "We couldn't reach the service. Check your connection and try again.",
        );
      }
    } finally {
      request.current = null;
      setPending(null);
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submit(question);
  }

  return (
    <section className="rounded-xl border border-edge bg-surface p-4 sm:p-5">
      {turns.length > 0 && (
        <ol className="mb-4 space-y-5">
          {turns.map((turn, index) => (
            <li key={index}>
              <p className="text-sm font-medium">{turn.question}</p>
              <p
                className={`mt-1 whitespace-pre-line text-base leading-relaxed ${
                  turn.status === "answered" || turn.status === "listed" ? "" : "text-ink-2"
                }`}
              >
                {turn.text}
              </p>
              {turn.citations.length > 0 && (
                <ul className="mt-2 flex flex-col gap-1 text-sm">
                  {turn.citations.map((citation) => (
                    <li key={citation.reference}>
                      <Link
                        href={`/leaks/${citation.reference}`}
                        className="text-ink-2 underline decoration-line underline-offset-4 hover:text-ink hover:decoration-ink-3"
                      >
                        {citation.reference} · {citation.headline}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
      )}

      <div aria-live="polite">
        {pending && (
          <div className="mb-4">
            <p className="text-sm font-medium">{pending.question}</p>
            <p className="mt-1 flex items-center gap-2 text-sm text-ink-2">
              <span
                aria-hidden
                className="inline-block h-2 w-2 animate-pulse rounded-full bg-series"
              />
              {pending.progress}…
            </p>
          </div>
        )}
      </div>

      <form onSubmit={onSubmit} className="flex gap-2">
        <label htmlFor="ask-question" className="sr-only">
          {reference ? "Ask about this leak" : "Ask about your leaks"}
        </label>
        <input
          id="ask-question"
          type="text"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          maxLength={MAX_QUESTION}
          disabled={pending !== null}
          autoComplete="off"
          placeholder={reference ? "Ask about this leak" : "Ask about your leaks"}
          className="min-w-0 flex-1 rounded-lg border border-edge bg-page px-3 py-2 text-base placeholder:text-ink-3 focus-visible:outline-offset-0 disabled:opacity-60"
        />
        {pending ? (
          <button
            type="button"
            onClick={() => request.current?.abort()}
            className="rounded-lg border border-edge px-4 py-2 text-sm font-medium hover:bg-wash"
          >
            Stop
          </button>
        ) : (
          <button
            type="submit"
            disabled={question.trim().length < 2}
            className="rounded-lg bg-ink px-4 py-2 text-sm font-medium text-page hover:opacity-90 disabled:opacity-40"
          >
            Ask
          </button>
        )}
      </form>

      <p role="alert" className="mt-2 text-sm text-danger empty:hidden">
        {error}
      </p>

      {turns.length === 0 && !pending && (
        <ul className="mt-3 flex flex-wrap gap-2">
          {suggestions.map((suggestion) => (
            <li key={suggestion}>
              <button
                type="button"
                onClick={() => void submit(suggestion)}
                className="rounded-full border border-edge px-3 py-1 text-sm text-ink-2 hover:bg-wash hover:text-ink"
              >
                {suggestion}
              </button>
            </li>
          ))}
        </ul>
      )}

      <p className="mt-3 text-xs text-ink-3">
        Answers are written by AI from your leak reports. Every figure is checked against the
        report it came from.
      </p>
    </section>
  );
}

/** A problem with a message that is safe to show as it is. */
class Failure extends Error {}

/** Read a response body as one JSON value per line. */
async function* events(body: ReadableStream<Uint8Array>): AsyncGenerator<StreamEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      buffered += decoder.decode(value, { stream: !done });
      const lines = buffered.split("\n");
      buffered = lines.pop() ?? "";
      for (const line of lines) {
        if (line.trim()) yield JSON.parse(line) as StreamEvent;
      }
      if (done) break;
    }
    if (buffered.trim()) yield JSON.parse(buffered) as StreamEvent;
  } finally {
    reader.releaseLock();
  }
}
