// Shapes returned by the API. Kept in step with `cosmos/app/api.py`.

export type Account = {
  name: string;
  email: string;
  tenant_id: string;
  tenant_name: string;
  /** Whether questions can be asked. False when no language model is configured. */
  assistant: boolean;
};

export type Option = { id: string; label: string };

export type LeakSummary = {
  reference: string;
  cause: string;
  cause_label: string;
  platform: string;
  platform_label: string;
  category: string | null;
  /** Null when every city is affected. */
  cities: string[] | null;
  product_count: number;
  start_date: string;
  end_date: string;
  headline: string;
  /** Rupees lost to the named cause. Null for a data gap or an unexplained drop. */
  loss_gmv: number | null;
  loss_gmv_sd: number;
  unexplained_gmv: number | null;
  unreported_units: number;
};

export type CauseTotal = {
  cause: string;
  cause_label: string;
  leaks: number;
  loss_gmv: number;
};

export type LeakList = {
  items: LeakSummary[];
  leaks: number;
  loss_gmv: number;
  by_cause: CauseTotal[];
  platforms: Option[];
  causes: Option[];
};

export type DriverLoss = {
  key: string;
  label: string;
  loss_gmv: number;
  loss_gmv_sd: number;
};

export type Reading = {
  label: string;
  unit: "percent" | "rank" | "currency" | "count";
  before: number | null;
  during: number;
};

export type Day = {
  day: string;
  expected_gmv: number;
  actual_gmv: number | null;
  complete: boolean;
  in_window: boolean;
};

export type Product = { sku_id: string; name: string; brand: string };

export type LeakDetail = LeakSummary & {
  /** An AI-written summary, checked against the figures. Null when there is none. */
  summary: string | null;
  what_happened: string;
  why: string;
  products: Product[];
  expected_gmv: number;
  actual_gmv: number | null;
  unexplained_is_noise: boolean;
  unreported_gmv: number;
  drivers: DriverLoss[];
  readings: Reading[];
  daily: Day[];
  detected_at: string;
};
