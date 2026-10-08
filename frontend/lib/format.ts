export function inr(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v >= 1e7) return `₹${trim(v / 1e7)} Cr`;
  if (v >= 1e5) return `₹${trim(v / 1e5)} L`;
  return `₹${v.toLocaleString("en-IN")}`;
}

function trim(n: number): string {
  return n.toFixed(2).replace(/\.?0+$/, "");
}

const dateFmt = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });
const dateTimeFmt = new Intl.DateTimeFormat("en-IN", {
  day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit", timeZone: "Asia/Kolkata",
});

export function date(iso: string | null | undefined): string {
  return iso ? dateFmt.format(new Date(iso)) : "—";
}

export function dateTime(iso: string | null | undefined): string {
  return iso ? `${dateTimeFmt.format(new Date(iso))} IST` : "—";
}

export function daysUntil(iso: string | null | undefined): number | null {
  if (!iso) return null;
  return (new Date(iso).getTime() - Date.now()) / 86_400_000;
}

export function closesIn(iso: string | null | undefined): string {
  const d = daysUntil(iso);
  if (d === null) return "closing date unknown";
  if (d < 0) return "closed";
  if (d < 1) return "closes today";
  const n = Math.floor(d);
  return `closes in ${n} day${n === 1 ? "" : "s"}`;
}

export const TYPE_LABEL: Record<string, string> = {
  SERVICE: "Service", OEM: "OEM / product", HYBRID: "Hybrid", UNRELATED: "Unrelated", UNKNOWN: "Unclassified",
};

export const DECISION_LABEL: Record<string, string> = {
  ACCEPTED: "Pursue", MANUAL_REVIEW: "Needs review", REJECTED: "Rejected", PENDING: "Analysing",
};

export const STAGE_LABEL: Record<string, string> = {
  NOT_STARTED: "Not started", CONSIDERING: "Considering", BIDDING: "Preparing bid", NO_BID: "Not bidding",
  SUBMITTED: "Submitted", WON: "Won", LOST: "Lost", CANCELLED: "Cancelled by buyer",
};

export const REASON_LABEL: Record<string, string> = {
  not_eligible: "Not eligible", outside_scope: "Outside our scope", value_too_low: "Value too low", value_too_high: "Value too high",
  timeline_too_short: "Not enough time", low_win_chance: "Low chance of winning", oem_not_available: "No OEM partnership",
  resource_constraint: "No team available", other: "Other", price: "Price", technical_score: "Technical score",
  disqualified: "Disqualified", incumbent: "Incumbent retained", oem_preference: "Buyer preferred another OEM",
};

export function pct(v: number | null | undefined, digits = 0): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;
}
