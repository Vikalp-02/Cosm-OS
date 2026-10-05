const rupees = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});
const compactRupees = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  notation: "compact",
  maximumFractionDigits: 1,
});
const whole = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const percent = new Intl.NumberFormat("en-IN", { style: "percent", maximumFractionDigits: 0 });
const oneDecimal = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** ₹1,23,456 */
export function inr(value: number): string {
  return rupees.format(Math.round(value));
}

/** ₹1.2L, for axis ticks and other tight spots. */
export function inrCompact(value: number): string {
  return compactRupees.format(value);
}

export function count(value: number): string {
  return whole.format(Math.round(value));
}

export function reading(value: number, unit: "percent" | "rank" | "currency" | "count"): string {
  if (unit === "percent") return percent.format(value);
  if (unit === "rank") return oneDecimal.format(value);
  if (unit === "currency") return inr(value);
  return count(value);
}

type Ymd = { year: number; month: number; day: number };

// API dates are calendar days with no time zone. Parsing them by hand keeps
// them from shifting a day in the viewer's zone.
function parse(isoDay: string): Ymd {
  const [year = 0, month = 1, day = 1] = isoDay.slice(0, 10).split("-").map(Number);
  return { year, month, day };
}

/** 20 Jul */
export function shortDay(isoDay: string): string {
  const { month, day } = parse(isoDay);
  return `${day} ${MONTHS[month - 1]}`;
}

/** 20–27 Jul 2026, or 28 Jul – 3 Aug 2026 */
export function dayRange(start: string, end: string): string {
  const a = parse(start);
  const b = parse(end);
  if (start === end) return `${a.day} ${MONTHS[a.month - 1]} ${a.year}`;
  if (a.year === b.year && a.month === b.month) {
    return `${a.day}–${b.day} ${MONTHS[b.month - 1]} ${b.year}`;
  }
  return `${a.day} ${MONTHS[a.month - 1]} – ${b.day} ${MONTHS[b.month - 1]} ${b.year}`;
}

export function dayCount(start: string, end: string): number {
  const a = parse(start);
  const b = parse(end);
  const span = Date.UTC(b.year, b.month - 1, b.day) - Date.UTC(a.year, a.month - 1, a.day);
  return Math.round(span / 86_400_000) + 1;
}

export function plural(value: number, singular: string, many = `${singular}s`): string {
  return `${count(value)} ${value === 1 ? singular : many}`;
}

export function listed(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}
