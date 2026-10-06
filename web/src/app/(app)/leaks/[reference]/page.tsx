import type { Metadata } from "next";
import Link from "next/link";

import { AskBox } from "@/components/ask-box";
import { DriverBars } from "@/components/driver-bars";
import { TrendChart } from "@/components/trend-chart";
import { Card, CauseTag, StatTile } from "@/components/ui";
import { api, getAccount } from "@/lib/api";
import { count, dayCount, dayRange, inr, listed, plural, reading, shortDay } from "@/lib/format";
import type { LeakDetail } from "@/lib/types";

type Props = { params: Promise<{ reference: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { reference } = await params;
  return { title: reference };
}

export default async function LeakPage({ params }: Props) {
  const { reference } = await params;
  const [leak, account] = await Promise.all([
    api<LeakDetail>(`/api/leaks/${encodeURIComponent(reference)}`),
    getAccount(),
  ]);

  const days = dayCount(leak.start_date, leak.end_date);
  const isGap = leak.cause === "data_gap";
  const where = [
    leak.platform_label,
    leak.category,
    leak.cities === null ? "All cities" : listed(leak.cities),
  ].filter(Boolean);
  const hero = headlineFigure(leak);
  const driverLosses = leak.drivers.reduce((sum, driver) => sum + driver.loss_gmv, 0);

  return (
    <article>
      <Link href="/leaks" className="text-sm text-ink-2 hover:text-ink">
        ← All leaks
      </Link>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <CauseTag cause={leak.cause} label={leak.cause_label} />
        <span className="text-sm text-ink-3">{leak.reference}</span>
      </div>
      <h1 className="mt-2 max-w-3xl text-2xl font-semibold tracking-tight sm:text-3xl">
        {leak.headline}
      </h1>
      <p className="mt-2 text-sm text-ink-2">
        {where.join(" · ")} · {dayRange(leak.start_date, leak.end_date)} ({plural(days, "day")})
      </p>

      <div className="mt-6 grid gap-4 lg:grid-cols-[1fr_20rem]">
        <Card>
          {leak.summary && (
            <div className="mb-5 border-b border-line pb-5">
              <h2 className="text-sm font-medium text-ink-2">Summary</h2>
              <p className="mt-1 text-base leading-relaxed">{leak.summary}</p>
              <p className="mt-2 text-xs text-ink-3">
                Written by AI from the figures on this page, and checked against them.
              </p>
            </div>
          )}
          <h2 className="text-sm font-medium text-ink-2">What happened</h2>
          <p className="mt-1 text-base leading-relaxed">{leak.what_happened}</p>
          <h2 className="mt-5 text-sm font-medium text-ink-2">
            {isGap ? "What this means" : "Why we think so"}
          </h2>
          <p className="mt-1 text-base leading-relaxed">{leak.why}</p>
        </Card>

        <Card>
          <div className="text-sm text-ink-2">{hero.label}</div>
          <div className="mt-1 text-5xl font-semibold tracking-tight">{hero.value}</div>
          {hero.range && <div className="mt-1 text-base text-ink-2">{hero.range}</div>}
          <p className="mt-4 text-sm leading-relaxed text-ink-2">{hero.note}</p>
        </Card>
      </div>

      {!isGap && leak.actual_gmv !== null && (
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <StatTile
            label="Expected sales"
            value={inr(leak.expected_gmv)}
            note="If these days had been normal"
          />
          <StatTile label="Actual sales" value={inr(leak.actual_gmv)} note="What was sold" />
          <StatTile
            label="Not explained"
            value={inr(Math.abs(leak.unexplained_gmv ?? 0))}
            note={
              leak.unexplained_is_noise
                ? "Within normal day-to-day variation"
                : (leak.unexplained_gmv ?? 0) >= 0
                  ? "Lower than the drivers predict, by more than chance"
                  : "Higher than the drivers predict, by more than chance"
            }
          />
        </div>
      )}

      {account.assistant && (
        <div className="mt-4">
          <AskBox
            reference={leak.reference}
            suggestions={["Why did this happen?", "How sure is the figure?"]}
          />
        </div>
      )}

      <Card title="Sales, day by day" className="mt-4">
        <TrendChart days={leak.daily} />
      </Card>

      {leak.drivers.length > 0 && (
        <Card title="Where the loss came from" className="mt-4">
          <DriverBars drivers={leak.drivers} unexplained={leak.unexplained_gmv} />
          {leak.actual_gmv !== null && (
            <p className="mt-4 border-t border-line pt-4 text-sm text-ink-2">
              The figures add up: {inr(leak.expected_gmv)} expected, less {inr(driverLosses)}{" "}
              across the drivers
              {leak.unexplained_gmv !== null &&
                `, ${leak.unexplained_gmv >= 0 ? "less" : "plus"} ${inr(
                  Math.abs(leak.unexplained_gmv),
                )} not explained`}
              {leak.unreported_gmv > 0 && `, less ${inr(leak.unreported_gmv)} never reported`},
              leaves {inr(leak.actual_gmv)} sold.
            </p>
          )}
        </Card>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {leak.readings.length > 0 && (
          <Card title="The evidence">
            <dl className="divide-y divide-line">
              {leak.readings.map((item) => (
                <div
                  key={item.label}
                  className="flex items-baseline justify-between gap-4 py-2.5 first:pt-0 last:pb-0"
                >
                  <dt className="text-sm text-ink-2">{item.label}</dt>
                  <dd className="shrink-0 text-right text-sm tabular-nums">
                    {item.before !== null && (
                      <>
                        <span className="text-ink-2">{reading(item.before, item.unit)}</span>
                        <span aria-label="to" className="mx-1.5 text-ink-3">
                          →
                        </span>
                      </>
                    )}
                    <span className="font-semibold">{reading(item.during, item.unit)}</span>
                  </dd>
                </div>
              ))}
            </dl>
          </Card>
        )}

        <Card title={`${plural(leak.products.length, "product")} affected`}>
          <p className="text-sm text-ink-2">
            {leak.cities === null ? "In every city" : `In ${listed(leak.cities)}`} on{" "}
            {leak.platform_label}.
          </p>
          <ul className="mt-3 max-h-64 space-y-1.5 overflow-auto pr-3 text-sm">
            {leak.products.map((product) => (
              <li key={product.sku_id} className="flex justify-between gap-4">
                <span>{product.name}</span>
                <span className="shrink-0 text-ink-3">{product.sku_id}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <details className="mt-6 text-sm text-ink-2">
        <summary className="cursor-pointer select-none font-medium hover:text-ink">
          How this is worked out
        </summary>
        <div className="mt-3 max-w-3xl space-y-2 leading-relaxed">
          <p>
            Expected sales come from a model of each product in each city, fitted only on the
            days before this period. It knows the product&apos;s usual level, the weekly rhythm,
            and how its sales respond to availability, your price, competitor prices, search
            position and ads.
          </p>
          <p>
            The gap between expected and actual sales is then shared out between those drivers
            according to how far each one moved from its normal level. Anything left over is
            shown as not explained, and is checked against ordinary day-to-day variation.
          </p>
          <p>
            Availability and ads are worked out directly. Price and search effects rest on
            estimates, so they carry a ± range. Found on {shortDay(leak.detected_at)}{" "}
            {leak.detected_at.slice(0, 4)}.
          </p>
        </div>
      </details>
    </article>
  );
}

function headlineFigure(leak: LeakDetail): {
  label: string;
  value: string;
  range?: string;
  note: string;
} {
  if (leak.cause === "data_gap") {
    return {
      label: "Sales not reported",
      value: `≈ ${count(leak.unreported_units)} units`,
      range: `about ${inr(leak.unreported_gmv)}`,
      note: "What these cities would normally have sold. This is not lost revenue: the sales most likely happened and were never reported.",
    };
  }
  if (leak.loss_gmv === null) {
    return {
      label: "Below expected",
      value: inr(leak.unexplained_gmv ?? 0),
      note: "The shortfall against what the measured drivers predict. No single driver accounts for it.",
    };
  }
  const estimated = leak.loss_gmv_sd > 0;
  return {
    label: "Revenue lost",
    value: inr(leak.loss_gmv),
    range: estimated ? `± ${inr(leak.loss_gmv_sd)}` : undefined,
    note: estimated
      ? "An estimate, from how sales have responded to this driver in the past. The range shows how precisely that response is known."
      : "Worked out directly from how far this driver fell, with no estimate involved.",
  };
}
