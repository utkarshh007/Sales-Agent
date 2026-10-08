import type { ReactNode } from "react";
import { DECISION_LABEL, TYPE_LABEL } from "@/lib/format";
import { SCORE_LABEL, SCORE_ORDER, type Priority } from "@/lib/types";

const PRIORITY_CLASS: Record<string, string> = {
  HOT: "text-hot", HIGH: "text-high", MEDIUM: "text-medium", LOW: "text-low", SUPPRESS: "text-reject",
};
const PRIORITY_LABEL: Record<string, string> = { HOT: "Hot", HIGH: "High", MEDIUM: "Medium", LOW: "Low", SUPPRESS: "Suppressed" };

/** Score number with its priority word — the colour lives in the type, not in a pill. */
export function ScoreMark({ score, priority, size = "md", stacked = false }: { score: number | null; priority: Priority | null; size?: "md" | "lg"; stacked?: boolean }) {
  const cls = PRIORITY_CLASS[priority ?? ""] ?? "text-muted";
  if (score === null) return <span className="text-muted">—</span>;
  return (
    <span className={`inline-flex ${stacked ? "flex-col leading-tight" : "items-baseline gap-1.5"} ${cls}`}>
      <span className={`num font-semibold ${size === "lg" ? "text-4xl tracking-tight" : "text-lg"}`}>{Math.round(score)}</span>
      <span className={size === "lg" ? "text-base font-medium" : "text-xs font-medium"}>{PRIORITY_LABEL[priority ?? ""] ?? ""}</span>
    </span>
  );
}

/**
 * The signature element: one bar, seven segments sized by scoring weight. The filled part of each
 * segment is the points earned; hatched segments do not apply (e.g. OEM match on a service tender).
 */
export function ScoreBar({ parts, height = 8, showLegend = false }: {
  parts: Record<string, [number | null, number]> | null | undefined; height?: number; showLegend?: boolean;
}) {
  if (!parts) return <div className="h-2 w-full rounded-sm bg-sunken" aria-hidden />;
  const keys = SCORE_ORDER.filter((k) => parts[k]);
  const summary = keys.map((k) => {
    const [p, m] = parts[k];
    return p === null ? `${SCORE_LABEL[k]}: not applicable` : `${SCORE_LABEL[k]}: ${p} of ${m}`;
  }).join("; ");
  return (
    <div>
      <div className="flex w-full gap-[2px]" style={{ height }} role="img" aria-label={summary} title={summary}>
        {keys.map((k) => {
          const [p, m] = parts[k];
          const na = p === null;
          return (
            <div key={k} className="relative overflow-hidden rounded-[2px] bg-sunken" style={{ flexGrow: m, flexBasis: 0 }}>
              {na ? (
                <div className="absolute inset-0 opacity-60"
                  style={{ backgroundImage: "repeating-linear-gradient(135deg, var(--line) 0 2px, transparent 2px 5px)" }} />
              ) : (
                <div className="absolute inset-y-0 left-0 bg-teal" style={{ width: `${Math.max(0, Math.min(100, (p / m) * 100))}%` }} />
              )}
            </div>
          );
        })}
      </div>
      {showLegend && (
        <div className="mt-1.5 flex w-full gap-[2px] text-[11px] leading-tight text-muted">
          {keys.map((k) => (
            <div key={k} className="truncate" style={{ flexGrow: parts[k][1], flexBasis: 0 }}>{SCORE_LABEL[k]}</div>
          ))}
        </div>
      )}
    </div>
  );
}

const DECISION_CLASS: Record<string, string> = {
  ACCEPTED: "text-accept border-accept", MANUAL_REVIEW: "text-review border-review",
  REJECTED: "text-reject border-reject", PENDING: "text-muted border-line",
};

export function DecisionTag({ decision }: { decision: string }) {
  return (
    <span className={`inline-block whitespace-nowrap rounded-sm border px-1.5 py-px text-xs font-medium ${DECISION_CLASS[decision] ?? ""}`}>
      {DECISION_LABEL[decision] ?? decision}
    </span>
  );
}

export function TypeTag({ type }: { type: string }) {
  return <span className="whitespace-nowrap text-sm text-muted">{TYPE_LABEL[type] ?? type}</span>;
}

export function MatchType({ type }: { type: string }) {
  const label = { DIRECT: "Direct", SEMANTIC: "Semantic", ADJACENT: "Adjacent" }[type] ?? type;
  const cls = type === "DIRECT" ? "bg-teal text-surface" : type === "SEMANTIC" ? "bg-teal-soft text-teal" : "border border-line text-muted";
  return <span className={`rounded-sm px-1.5 py-px text-xs font-medium ${cls}`}>{label}</span>;
}

export function Panel({ title, aside, children, className = "" }: { title?: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-md border border-line bg-surface ${className}`}>
      {title && (
        <header className="flex items-baseline justify-between gap-4 border-b border-line px-5 py-3">
          <h2 className="text-[15px] font-semibold">{title}</h2>
          {aside}
        </header>
      )}
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "error"; children: ReactNode }) {
  const cls = tone === "error" ? "border-hot text-hot" : tone === "warn" ? "border-high" : "border-teal";
  return <div className={`border-l-[3px] bg-surface px-4 py-3 text-sm ${cls}`}>{children}</div>;
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return <p className="py-10 text-center text-sm text-muted" role="status">{label}…</p>;
}

export function Button({ variant = "primary", className = "", ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "quiet" | "danger" }) {
  const v = variant === "primary"
    ? "bg-teal text-surface hover:opacity-90"
    : variant === "danger" ? "border border-hot text-hot hover:bg-sunken" : "border border-line text-ink hover:bg-sunken";
  return <button {...props} className={`rounded-md px-3.5 py-1.5 text-sm font-medium disabled:opacity-50 ${v} ${className}`} />;
}
