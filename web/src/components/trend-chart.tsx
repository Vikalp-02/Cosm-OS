"use client";

import { type KeyboardEvent, type PointerEvent, useEffect, useRef, useState } from "react";

import { inr, inrCompact, shortDay } from "@/lib/format";
import type { Day } from "@/lib/types";

const HEIGHT = 280;
const MARGIN = { top: 20, right: 12, bottom: 30, left: 52 };

/** Expected against actual sales, day by day, with the leak period marked. */
export function TrendChart({ days }: { days: Day[] }) {
  const frame = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [active, setActive] = useState<number | null>(null);
  const [asTable, setAsTable] = useState(false);

  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.round(entry.contentRect.width));
    });
    observer.observe(element);
    return () => observer.disconnect();
    // The chart's frame is replaced when the table view is toggled.
  }, [asTable]);

  const plotWidth = Math.max(0, width - MARGIN.left - MARGIN.right);
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const ticks = axisTicks(
    Math.max(1, ...days.map((day) => Math.max(day.expected_gmv, day.actual_gmv ?? 0))),
  );
  const top = ticks[ticks.length - 1] ?? 1;
  const step = days.length > 1 ? plotWidth / (days.length - 1) : 0;
  const x = (index: number) => MARGIN.left + (days.length > 1 ? index * step : plotWidth / 2);
  const y = (value: number) => MARGIN.top + plotHeight * (1 - value / top);

  const inWindow = days.flatMap((day, index) => (day.in_window ? [index] : []));
  const windowStart = inWindow[0];
  const windowEnd = inWindow[inWindow.length - 1];
  const every = Math.max(1, Math.ceil(days.length / Math.max(2, Math.floor(plotWidth / 72))));

  function nearest(event: PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const offset = event.clientX - bounds.left - MARGIN.left;
    const index = step > 0 ? Math.round(offset / step) : 0;
    setActive(Math.min(days.length - 1, Math.max(0, index)));
  }

  function onKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    const last = days.length - 1;
    const from = active ?? windowStart ?? 0;
    const moves: Record<string, number> = {
      ArrowLeft: Math.max(0, from - 1),
      ArrowRight: Math.min(last, from + 1),
      Home: 0,
      End: last,
    };
    if (event.key === "Escape") setActive(null);
    const next = moves[event.key];
    if (next !== undefined) {
      event.preventDefault();
      setActive(next);
    }
  }

  const focus = active === null ? undefined : days[active];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
        <ul className="flex flex-wrap gap-x-5 gap-y-1 text-sm text-ink-2">
          <li className="flex items-center gap-2">
            <span aria-hidden className="h-0.5 w-5 rounded-full bg-series" />
            Actual sales
          </li>
          <li className="flex items-center gap-2">
            <span aria-hidden className="h-0.5 w-5 rounded-full bg-context" />
            Expected on a normal day
          </li>
          {windowStart !== undefined && (
            <li className="flex items-center gap-2">
              <span aria-hidden className="h-3 w-5 rounded-sm bg-wash-strong" />
              Leak period
            </li>
          )}
        </ul>
        <button
          type="button"
          onClick={() => setAsTable((shown) => !shown)}
          aria-pressed={asTable}
          className="rounded-md px-2 py-1 text-sm text-ink-2 hover:bg-wash hover:text-ink"
        >
          {asTable ? "Show chart" : "Show as table"}
        </button>
      </div>

      {asTable ? (
        <DayTable days={days} />
      ) : (
        <div ref={frame} className="relative" style={{ height: HEIGHT }}>
          {width > 0 && (
            <svg
              width={width}
              height={HEIGHT}
              role="img"
              aria-label="Line chart of actual sales against expected sales by day. Use the arrow keys to read each day."
              tabIndex={0}
              className="block touch-pan-y select-none rounded-md"
              onPointerMove={nearest}
              onPointerDown={nearest}
              onPointerLeave={() => setActive(null)}
              onKeyDown={onKeyDown}
              onBlur={() => setActive(null)}
            >
              {windowStart !== undefined && windowEnd !== undefined && (
                <rect
                  x={Math.max(MARGIN.left, x(windowStart) - step / 2)}
                  y={MARGIN.top}
                  width={
                    Math.min(MARGIN.left + plotWidth, x(windowEnd) + step / 2) -
                    Math.max(MARGIN.left, x(windowStart) - step / 2)
                  }
                  height={plotHeight}
                  fill="var(--wash-strong)"
                />
              )}

              {ticks.map((value) => (
                <g key={value}>
                  <line
                    x1={MARGIN.left}
                    x2={MARGIN.left + plotWidth}
                    y1={y(value)}
                    y2={y(value)}
                    stroke={value === 0 ? "var(--axis)" : "var(--line)"}
                    strokeWidth={1}
                  />
                  <text
                    x={MARGIN.left - 8}
                    y={y(value)}
                    dy="0.32em"
                    textAnchor="end"
                    fontSize={12}
                    fill="var(--axis-ink)"
                    style={{ fontVariantNumeric: "tabular-nums" }}
                  >
                    {inrCompact(value)}
                  </text>
                </g>
              ))}

              {days.map((day, index) =>
                index % every === 0 && x(index) < MARGIN.left + plotWidth - 28 ? (
                  <text
                    key={day.day}
                    x={x(index)}
                    y={HEIGHT - 8}
                    textAnchor={index === 0 ? "start" : "middle"}
                    fontSize={12}
                    fill="var(--axis-ink)"
                  >
                    {shortDay(day.day)}
                  </text>
                ) : null,
              )}

              <path
                d={path(days.map((day) => day.expected_gmv), x, y)}
                fill="none"
                stroke="var(--context)"
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              <path
                d={path(days.map((day) => day.actual_gmv), x, y)}
                fill="none"
                stroke="var(--series)"
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              {/* A day with data but none on either side would otherwise draw nothing. */}
              {days.map((day, index) =>
                day.actual_gmv !== null &&
                days[index - 1]?.actual_gmv == null &&
                days[index + 1]?.actual_gmv == null ? (
                  <circle key={day.day} cx={x(index)} cy={y(day.actual_gmv)} r={3} fill="var(--series)" />
                ) : null,
              )}

              {focus && active !== null && (
                <g pointerEvents="none">
                  <line
                    x1={x(active)}
                    x2={x(active)}
                    y1={MARGIN.top}
                    y2={MARGIN.top + plotHeight}
                    stroke="var(--ink-3)"
                    strokeWidth={1}
                  />
                  <circle
                    cx={x(active)}
                    cy={y(focus.expected_gmv)}
                    r={5}
                    fill="var(--context)"
                    stroke="var(--surface)"
                    strokeWidth={2}
                  />
                  {focus.actual_gmv !== null && (
                    <circle
                      cx={x(active)}
                      cy={y(focus.actual_gmv)}
                      r={5}
                      fill="var(--series)"
                      stroke="var(--surface)"
                      strokeWidth={2}
                    />
                  )}
                </g>
              )}
            </svg>
          )}

          {focus && active !== null && (
            <Readout day={focus} left={x(active)} flip={x(active) > width * 0.6} />
          )}
        </div>
      )}
    </div>
  );
}

function Readout({ day, left, flip }: { day: Day; left: number; flip: boolean }) {
  const gap =
    day.actual_gmv === null || day.expected_gmv <= 0
      ? null
      : (day.actual_gmv - day.expected_gmv) / day.expected_gmv;
  return (
    <div
      role="status"
      className="pointer-events-none absolute top-2 z-10 w-52 rounded-lg border border-edge bg-surface p-3 text-sm shadow-lg"
      style={{ left, transform: `translateX(${flip ? "calc(-100% - 12px)" : "12px"})` }}
    >
      <div className="text-ink-2">
        {shortDay(day.day)}
        {day.in_window && " · leak period"}
      </div>
      <dl className="mt-2 space-y-1">
        <div className="flex items-center justify-between gap-3">
          <dt className="flex items-center gap-2 text-ink-2">
            <span aria-hidden className="h-0.5 w-3 rounded-full bg-series" />
            Actual
          </dt>
          <dd className="font-semibold tabular-nums">
            {day.actual_gmv === null ? "No data" : inr(day.actual_gmv)}
          </dd>
        </div>
        <div className="flex items-center justify-between gap-3">
          <dt className="flex items-center gap-2 text-ink-2">
            <span aria-hidden className="h-0.5 w-3 rounded-full bg-context" />
            Expected
          </dt>
          <dd className="font-semibold tabular-nums">{inr(day.expected_gmv)}</dd>
        </div>
      </dl>
      {gap !== null && (
        <div className="mt-2 border-t border-line pt-2 text-ink-2">
          {Math.abs(gap) < 0.005
            ? "In line with expected"
            : `${Math.abs(gap * 100).toFixed(0)}% ${gap < 0 ? "below" : "above"} expected`}
        </div>
      )}
      {!day.complete && day.actual_gmv !== null && (
        <div className="mt-1 text-ink-3">Only part of this day&apos;s sales arrived.</div>
      )}
    </div>
  );
}

function DayTable({ days }: { days: Day[] }) {
  return (
    <div className="max-h-80 overflow-auto rounded-lg border border-line">
      <table className="w-full text-sm tabular-nums">
        <thead className="sticky top-0 bg-surface text-left text-ink-2">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">
              Day
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              Expected
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              Actual
            </th>
          </tr>
        </thead>
        <tbody>
          {days.map((day) => (
            <tr key={day.day} className={`border-t border-line ${day.in_window ? "bg-wash" : ""}`}>
              <th scope="row" className="px-3 py-1.5 text-left font-normal">
                {shortDay(day.day)}
                {day.in_window && <span className="ml-2 text-ink-3">leak period</span>}
              </th>
              <td className="px-3 py-1.5 text-right">{inr(day.expected_gmv)}</td>
              <td className="px-3 py-1.5 text-right">
                {day.actual_gmv === null ? "No data" : inr(day.actual_gmv)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A line through the values, lifting the pen wherever one is missing. */
function path(
  values: (number | null)[],
  x: (index: number) => number,
  y: (value: number) => number,
): string {
  let drawing = false;
  let d = "";
  values.forEach((value, index) => {
    if (value === null) {
      drawing = false;
      return;
    }
    d += `${drawing ? "L" : "M"}${x(index).toFixed(1)},${y(value).toFixed(1)}`;
    drawing = true;
  });
  return d;
}

/**
 * Round tick values from zero to just above `highest`: three to five steps of
 * a round size, whichever leaves the least empty space above the data.
 */
function axisTicks(highest: number): number[] {
  const magnitude = 10 ** Math.floor(Math.log10(highest));
  let best: { step: number; steps: number } | undefined;
  for (const scale of [0.1, 1]) {
    for (const multiple of [1, 2, 2.5, 5]) {
      const step = multiple * magnitude * scale;
      const steps = Math.ceil(highest / step);
      if (steps >= 3 && steps <= 5 && (!best || steps * step < best.steps * best.step)) {
        best = { step, steps };
      }
    }
  }
  const { step, steps } = best ?? { step: highest, steps: 1 };
  return Array.from({ length: steps + 1 }, (_, tick) => tick * step);
}
