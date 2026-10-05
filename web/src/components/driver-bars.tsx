"use client";

import { useState } from "react";

import { inr } from "@/lib/format";
import type { DriverLoss } from "@/lib/types";

type Row = {
  key: string;
  label: string;
  value: number;
  sd: number;
  kind: "driver" | "unexplained";
};

/**
 * What each driver cost, as bars from a shared zero line: losses to the right,
 * gains to the left. Whatever the drivers leave unaccounted for sits last, in
 * a neutral colour, because it is not a driver.
 */
export function DriverBars({
  drivers,
  unexplained,
}: {
  drivers: DriverLoss[];
  unexplained: number | null;
}) {
  const [hovered, setHovered] = useState<string | null>(null);

  const rows: Row[] = [...drivers]
    .sort((a, b) => b.loss_gmv - a.loss_gmv)
    .map((driver) => ({
      key: driver.key,
      label: driver.label,
      value: driver.loss_gmv,
      sd: driver.loss_gmv_sd,
      kind: "driver",
    }));
  if (unexplained !== null) {
    rows.push({
      key: "unexplained",
      label: "Not explained",
      value: unexplained,
      sd: 0,
      kind: "unexplained",
    });
  }

  const low = Math.min(0, ...rows.map((row) => row.value));
  const high = Math.max(0, ...rows.map((row) => row.value));
  const span = high - low || 1;
  const zero = ((0 - low) / span) * 100;
  const totalLost = rows.reduce((sum, row) => sum + Math.max(0, row.value), 0);
  const anyGain = rows.some((row) => row.kind === "driver" && row.value < -span * 0.01);

  return (
    <div>
      {anyGain && (
        <ul className="mb-3 flex flex-wrap gap-x-5 gap-y-1 text-sm text-ink-2">
          <li className="flex items-center gap-2">
            <span aria-hidden className="h-3 w-3 rounded-sm bg-loss" />
            Cost sales
          </li>
          <li className="flex items-center gap-2">
            <span aria-hidden className="h-3 w-3 rounded-sm bg-gain" />
            Added sales
          </li>
        </ul>
      )}
      <ul>
        {rows.map((row) => {
          const width = (Math.abs(row.value) / span) * 100;
          const lost = row.value >= 0;
          const colour =
            row.kind === "unexplained" ? "bg-context" : lost ? "bg-loss" : "bg-gain";
          const share = totalLost > 0 && row.value > 0 ? row.value / totalLost : null;
          return (
            <li
              key={row.key}
              tabIndex={0}
              onPointerEnter={() => setHovered(row.key)}
              onPointerLeave={() => setHovered(null)}
              onFocus={() => setHovered(row.key)}
              onBlur={() => setHovered(null)}
              className={`relative grid grid-cols-[7.5rem_1fr] items-center gap-x-3 rounded-md px-2 py-2 sm:grid-cols-[9rem_1fr_9.5rem] ${
                hovered === row.key ? "bg-wash" : ""
              }`}
            >
              <span className="text-sm">{row.label}</span>
              <span aria-hidden className="relative block h-4">
                <span
                  className={`absolute inset-y-0 ${colour} ${
                    lost ? "rounded-r" : "rounded-l"
                  }`}
                  style={
                    lost
                      ? { left: `${zero}%`, width: `${width}%` }
                      : { right: `${100 - zero}%`, width: `${width}%` }
                  }
                />
                <span
                  className="absolute -inset-y-2 w-px bg-[var(--axis)]"
                  style={{ left: `${zero}%` }}
                />
              </span>
              <span className="col-span-2 text-sm tabular-nums sm:col-span-1 sm:text-right">
                <span className="font-semibold">{inr(Math.abs(row.value))}</span>{" "}
                <span className="text-ink-3">{verb(row)}</span>
              </span>

              {hovered === row.key && (
                <span
                  role="status"
                  className="pointer-events-none absolute bottom-full left-[7.5rem] z-10 mb-1 w-64 rounded-lg border border-edge bg-surface p-3 text-sm shadow-lg sm:left-[9.75rem]"
                >
                  <span className="block font-semibold tabular-nums">
                    {inr(Math.abs(row.value))} {verb(row)}
                    {row.sd > 0 && (
                      <span className="font-normal text-ink-2"> ± {inr(row.sd)}</span>
                    )}
                  </span>
                  <span className="mt-1 block text-ink-2">{explain(row, share)}</span>
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function verb(row: Row): string {
  if (row.kind === "unexplained") return row.value >= 0 ? "short" : "over";
  return row.value >= 0 ? "lost" : "gained";
}

function explain(row: Row, share: number | null): string {
  if (row.kind === "unexplained") {
    return row.value >= 0
      ? "Sales came in this far below what the drivers together predict."
      : "Sales came in this far above what the drivers together predict.";
  }
  if (row.value < 0) return `${row.label} moved in your favour over these days.`;
  const part = share === null ? "" : ` About ${Math.round(share * 100)}% of everything lost.`;
  const doubt =
    row.sd > 0
      ? " The range shows how precisely the effect is known."
      : " Worked out directly, with no estimate involved.";
  return `What ${row.label.toLowerCase()} cost.${part}${doubt}`;
}
