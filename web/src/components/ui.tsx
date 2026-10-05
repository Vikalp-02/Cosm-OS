import type { ReactNode } from "react";

/** A surface that groups related content. */
export function Card({
  title,
  aside,
  children,
  className = "",
}: {
  title?: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-edge bg-surface p-5 sm:p-6 ${className}`}>
      {title && (
        <div className="mb-4 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h2 className="text-base font-semibold">{title}</h2>
          {aside}
        </div>
      )}
      {children}
    </section>
  );
}

/** One headline number with its label and an optional note. */
export function StatTile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-xl border border-edge bg-surface p-4 sm:p-5">
      <div className="text-sm text-ink-2">{label}</div>
      <div className="mt-1 text-2xl font-semibold tracking-tight">{value}</div>
      {note && <div className="mt-1 text-sm text-ink-3">{note}</div>}
    </div>
  );
}

/** What kind of leak this is. A data gap is set apart: it is not a loss of sales. */
export function CauseTag({ cause, label }: { cause: string; label: string }) {
  const style =
    cause === "data_gap"
      ? "border border-dashed border-ink-3 text-ink-2"
      : "bg-wash-strong text-ink";
  return (
    <span
      className={`inline-block whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium ${style}`}
    >
      {label}
    </span>
  );
}
