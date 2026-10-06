import type { Metadata } from "next";
import Link from "next/link";

import { AskBox } from "@/components/ask-box";
import { CauseTag, StatTile } from "@/components/ui";
import { api, getAccount } from "@/lib/api";
import { count, dayCount, dayRange, inr, listed, plural } from "@/lib/format";
import type { LeakList, LeakSummary, Option } from "@/lib/types";

export const metadata: Metadata = { title: "Revenue leaks" };

type Filters = { platform?: string; cause?: string; sort?: string };

export default async function LeaksPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const filters: Filters = {
    platform: one(params.platform),
    cause: one(params.cause),
    sort: one(params.sort) === "largest" ? "largest" : undefined,
  };
  const query = new URLSearchParams();
  if (filters.platform) query.set("platform", filters.platform);
  if (filters.cause) query.set("cause", filters.cause);
  const [data, account] = await Promise.all([
    api<LeakList>(`/api/leaks${query.size ? `?${query}` : ""}`),
    getAccount(),
  ]);

  const items =
    filters.sort === "largest"
      ? [...data.items].sort((a, b) => (b.loss_gmv ?? -1) - (a.loss_gmv ?? -1))
      : data.items;
  const largestLoss = Math.max(0, ...data.items.map((item) => item.loss_gmv ?? 0));
  const gaps = data.items.filter((item) => item.cause === "data_gap").length;
  const topCause = data.by_cause.find((total) => total.loss_gmv > 0);
  const filtered = Boolean(filters.platform || filters.cause);

  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Revenue leaks</h1>
      <p className="mt-1 text-sm text-ink-2">
        Where sales fell short of what they should have been, what caused it, and what it cost.
      </p>

      {account.assistant && (
        <div className="mt-6">
          <AskBox
            suggestions={[
              "What cost us the most?",
              "Which leaks were stockouts?",
              "What went wrong most recently?",
            ]}
          />
        </div>
      )}

      <nav aria-label="Filters" className="mt-6 flex flex-col gap-3">
        <FilterRow
          label="Platform"
          name="platform"
          options={data.platforms}
          filters={filters}
        />
        <FilterRow label="Cause" name="cause" options={data.causes} filters={filters} />
      </nav>

      <div className="mt-6 grid gap-3 sm:grid-cols-3">
        <StatTile
          label="Revenue lost"
          value={inr(data.loss_gmv)}
          note={`across ${plural(data.leaks - gaps, "leak")}`}
        />
        <StatTile
          label="Biggest cause"
          value={topCause ? topCause.cause_label : "None"}
          note={
            topCause
              ? `${inr(topCause.loss_gmv)} over ${plural(topCause.leaks, "leak")}`
              : "No sales were lost"
          }
        />
        <StatTile
          label="Data gaps"
          value={count(gaps)}
          note={gaps ? "Sales data that never arrived" : "All sales data arrived"}
        />
      </div>

      <div className="mt-8 flex items-baseline justify-between gap-4">
        <h2 className="text-base font-semibold">{plural(items.length, "finding")}</h2>
        <div className="flex items-center gap-1 text-sm">
          <span className="mr-1 text-ink-3">Sort</span>
          <Pill href={hrefWith(filters, { sort: undefined })} active={!filters.sort}>
            Newest
          </Pill>
          <Pill href={hrefWith(filters, { sort: "largest" })} active={filters.sort === "largest"}>
            Largest
          </Pill>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="mt-3 rounded-xl border border-edge bg-surface px-6 py-14 text-center">
          <p className="font-medium">
            {filtered ? "Nothing matches these filters" : "No leaks found"}
          </p>
          <p className="mt-1 text-sm text-ink-2">
            {filtered
              ? "Try a different platform or cause."
              : "Sales are tracking what availability, price, search and ads predict."}
          </p>
          {filtered && (
            <Link href="/leaks" className="mt-4 inline-block text-sm font-medium underline">
              Clear filters
            </Link>
          )}
        </div>
      ) : (
        <ul className="mt-3 overflow-hidden rounded-xl border border-edge bg-surface">
          {items.map((item) => (
            <li key={item.reference} className="border-b border-line last:border-b-0">
              <LeakRow item={item} largestLoss={largestLoss} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function LeakRow({ item, largestLoss }: { item: LeakSummary; largestLoss: number }) {
  const where = [
    item.platform_label,
    item.category,
    item.cities === null ? "All cities" : listed(item.cities),
  ].filter(Boolean);
  const days = dayCount(item.start_date, item.end_date);

  return (
    <Link
      href={`/leaks/${item.reference}`}
      className="grid gap-x-6 gap-y-2 px-4 py-4 hover:bg-wash sm:grid-cols-[1fr_11rem] sm:px-5"
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <CauseTag cause={item.cause} label={item.cause_label} />
          <span className="text-xs text-ink-3">{item.reference}</span>
        </div>
        <div className="mt-1.5 font-medium">{item.headline}</div>
        <div className="mt-0.5 text-sm text-ink-2">
          {where.join(" · ")} · {dayRange(item.start_date, item.end_date)} ({plural(days, "day")})
        </div>
      </div>
      <div className="sm:text-right">
        <Cost item={item} largestLoss={largestLoss} />
      </div>
    </Link>
  );
}

function Cost({ item, largestLoss }: { item: LeakSummary; largestLoss: number }) {
  if (item.cause === "data_gap") {
    return (
      <>
        <div className="font-semibold tabular-nums">≈ {count(item.unreported_units)} units</div>
        <div className="text-sm text-ink-3">not reported</div>
      </>
    );
  }
  if (item.loss_gmv === null) {
    return (
      <>
        <div className="font-semibold tabular-nums">{inr(item.unexplained_gmv ?? 0)}</div>
        <div className="text-sm text-ink-3">below expected</div>
      </>
    );
  }
  const share = largestLoss > 0 ? Math.max(0, item.loss_gmv) / largestLoss : 0;
  return (
    <>
      <div className="font-semibold tabular-nums">{inr(item.loss_gmv)}</div>
      <div className="text-sm text-ink-3">
        {item.loss_gmv_sd > 0 ? `± ${inr(item.loss_gmv_sd)}` : "lost"}
      </div>
      {/* Size against the largest leak in view, so the costly ones stand out at a glance. */}
      <div aria-hidden className="mt-2 flex h-1.5 sm:justify-end">
        <div
          className="h-full rounded-full bg-series"
          style={{ width: `${Math.max(2, share * 100)}%` }}
        />
      </div>
    </>
  );
}

function FilterRow({
  label,
  name,
  options,
  filters,
}: {
  label: string;
  name: "platform" | "cause";
  options: Option[];
  filters: Filters;
}) {
  if (options.length === 0) return null;
  const current = filters[name];
  return (
    <div className="flex flex-wrap items-center gap-1 text-sm">
      <span className="mr-1 w-16 shrink-0 text-ink-3">{label}</span>
      <Pill href={hrefWith(filters, { [name]: undefined })} active={!current}>
        All
      </Pill>
      {options.map((option) => (
        <Pill
          key={option.id}
          href={hrefWith(filters, { [name]: option.id })}
          active={current === option.id}
        >
          {option.label}
        </Pill>
      ))}
    </div>
  );
}

function Pill({
  href,
  active,
  children,
}: {
  href: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? "true" : undefined}
      className={`rounded-full px-3 py-1 ${
        active ? "bg-ink text-page" : "text-ink-2 hover:bg-wash-strong hover:text-ink"
      }`}
    >
      {children}
    </Link>
  );
}

function hrefWith(filters: Filters, change: Partial<Filters>): string {
  const next = { ...filters, ...change };
  const query = new URLSearchParams();
  for (const key of ["platform", "cause", "sort"] as const) {
    const value = next[key];
    if (value) query.set(key, value);
  }
  return query.size ? `/leaks?${query}` : "/leaks";
}

function one(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value || undefined;
}
