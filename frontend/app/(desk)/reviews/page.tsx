"use client";

import { useEffect, useState } from "react";
import TenderLine from "@/components/TenderLine";
import { Loading, Notice, Panel } from "@/components/ui";
import { api } from "@/lib/api";
import type { TenderRow } from "@/lib/types";

interface ReviewItem { id: number; code: string; reason: string; resolution: string | null; resolved_by: string | null; tender: TenderRow }

const GROUPS: Record<string, string> = {
  SERVICE_VALUE_UNKNOWN: "Service value not stated",
  HYBRID_REVIEW_REQUIRED: "Hybrid: service and product split unclear",
  HYBRID_SERVICE_COMPONENT_OVER_CAP: "Hybrid: service component above the service limit",
  ADJACENT_ONLY: "Only an adjacent capability match",
  LLM_DECLINED: "The LLM declined to analyse",
  CLASSIFICATION_UNKNOWN: "Opportunity type unclear",
};

export default function ReviewsPage() {
  const [result, setResult] = useState<{ status: string; items: ReviewItem[] } | null>(null);
  const [status, setStatus] = useState<"OPEN" | "RESOLVED">("OPEN");
  const [error, setError] = useState("");
  const items = result?.status === status ? result.items : null;

  useEffect(() => {
    let live = true;
    api<ReviewItem[]>(`/reviews?status=${status}`).then((r) => live && setResult({ status, items: r }))
      .catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [status]);

  const groups = new Map<string, ReviewItem[]>();
  (items ?? []).forEach((r) => groups.set(r.code, [...(groups.get(r.code) ?? []), r]));

  return (
    <div className="mx-auto max-w-4xl">
      <h1 className="font-serif text-3xl font-semibold tracking-tight">Review queue</h1>
      <p className="mt-1 max-w-2xl text-muted">
        Tenders the rules could not decide on their own. Open one to read the evidence, then choose to pursue or reject; every decision is recorded.
      </p>
      <div className="mt-5 flex gap-1 text-sm" role="tablist">
        {(["OPEN", "RESOLVED"] as const).map((s) => (
          <button key={s} role="tab" aria-selected={status === s} onClick={() => setStatus(s)}
            className={`rounded-md px-3 py-1.5 ${status === s ? "bg-teal-soft font-semibold text-teal" : "hover:bg-sunken"}`}>
            {s === "OPEN" ? "Waiting" : "Resolved"}
          </button>
        ))}
      </div>
      {error && <div className="mt-4"><Notice tone="error">{error}</Notice></div>}
      {!items && !error && <Loading />}
      {items && items.length === 0 && (
        <p className="mt-10 text-muted">{status === "OPEN" ? "Nothing is waiting for a decision." : "No resolved reviews yet."}</p>
      )}
      <div className="mt-6 space-y-6">
        {[...groups.entries()].map(([code, rows]) => (
          <Panel key={code} title={GROUPS[code] ?? code} aside={<span className="num text-sm text-muted">{rows.length}</span>}>
            <ul className="-my-3 divide-y divide-line">
              {rows.map((r) => (
                <TenderLine key={r.id} t={r.tender}
                  note={status === "RESOLVED" ? `${r.resolution === "ACCEPTED" ? "Pursued" : "Rejected"} by ${r.resolved_by}` : r.reason} />
              ))}
            </ul>
          </Panel>
        ))}
      </div>
    </div>
  );
}
