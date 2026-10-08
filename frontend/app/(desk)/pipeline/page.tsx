"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Loading, Notice, ScoreMark } from "@/components/ui";
import { api } from "@/lib/api";
import { closesIn, daysUntil, inr, REASON_LABEL, STAGE_LABEL, TYPE_LABEL } from "@/lib/format";
import type { Outcome, TenderRow } from "@/lib/types";

type Row = TenderRow & { outcome: Outcome | null };
type Columns = Record<string, Row[]>;

const ACTIVE = ["NOT_STARTED", "CONSIDERING", "BIDDING", "SUBMITTED"] as const;
const CLOSED = ["WON", "LOST", "NO_BID", "CANCELLED"] as const;
const HINT: Record<string, string> = {
  NOT_STARTED: "Surfaced and still open; nobody has picked it up",
  CONSIDERING: "Being looked at",
  BIDDING: "Bid in preparation",
  SUBMITTED: "Waiting for the result",
};

export default function PipelinePage() {
  const [cols, setCols] = useState<Columns | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Columns>("/pipeline").then(setCols).catch((e) => setError(e.message));
  }, []);

  return (
    <div className="mx-auto max-w-[84rem]">
      <h1 className="font-serif text-3xl font-semibold tracking-tight">Bid pipeline</h1>
      <p className="mt-1 max-w-2xl text-muted">
        Every surfaced tender that is still open, and every tender the team has acted on. Change a tender&apos;s stage from its Bid status panel.
      </p>
      {error && <div className="mt-4"><Notice tone="error">{error}</Notice></div>}
      {!cols && !error && <Loading />}
      {cols && (
        <>
          <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {ACTIVE.map((s) => (
              <section key={s} className="min-w-0 rounded-md border border-line bg-sunken/50" aria-labelledby={`col-${s}`}>
                <header className="flex items-baseline justify-between gap-2 border-b border-line px-4 py-3">
                  <div>
                    <h2 id={`col-${s}`} className="font-semibold">{STAGE_LABEL[s]}</h2>
                    <p className="text-xs text-muted">{HINT[s]}</p>
                  </div>
                  <span className="num text-sm text-muted">{cols[s].length}</span>
                </header>
                <ul className="space-y-2 p-2">
                  {cols[s].length === 0 && <li className="px-2 py-3 text-sm text-muted">None.</li>}
                  {cols[s].map((r) => <Card key={r.id} r={r} />)}
                </ul>
              </section>
            ))}
          </div>

          <h2 className="mt-10 font-serif text-xl font-semibold">Closed</h2>
          <div className="mt-3 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {CLOSED.map((s) => (
              <section key={s} className="min-w-0" aria-labelledby={`col-${s}`}>
                <h3 id={`col-${s}`} className="flex justify-between border-b border-line pb-1.5 text-sm font-semibold">
                  {STAGE_LABEL[s]}<span className="num font-normal text-muted">{cols[s].length}</span>
                </h3>
                <ul className="divide-y divide-line text-sm">
                  {cols[s].length === 0 && <li className="py-2 text-muted">None.</li>}
                  {cols[s].map((r) => (
                    <li key={r.id} className="py-2">
                      <Link href={`/tenders/${r.id}`} className="line-clamp-2 hover:text-teal hover:underline">{r.title}</Link>
                      <p className="text-muted">{closedNote(r)}</p>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function Card({ r }: { r: Row }) {
  const d = daysUntil(r.closing_at);
  return (
    <li className="rounded-md border border-line bg-surface px-3 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <Link href={`/tenders/${r.id}`} className="line-clamp-3 font-serif leading-snug hover:text-teal hover:underline">{r.title}</Link>
        <ScoreMark score={r.score} priority={r.priority} stacked />
      </div>
      <p className="mt-1 truncate text-sm text-muted">{r.organization ?? "Unknown organisation"}</p>
      <p className="mt-1 text-sm">
        <span className={d !== null && d < 7 ? "font-medium text-high" : "text-muted"}>{closesIn(r.closing_at)}</span>
        <span className="text-muted">, {TYPE_LABEL[r.opportunity_type]}{r.tender_value_inr ? `, ${inr(r.tender_value_inr)}` : ""}</span>
      </p>
      {r.decision === "MANUAL_REVIEW" && <p className="mt-1 text-xs text-review">Needs a review decision</p>}
    </li>
  );
}

function closedNote(r: Row): string {
  const o = r.outcome;
  if (!o) return "";
  if (o.stage === "WON") return o.award_value_inr ? `Awarded at ${inr(o.award_value_inr)}` : "Awarded";
  if (o.stage === "LOST") return [o.winner && `to ${o.winner}`, o.loss_reason && REASON_LABEL[o.loss_reason]].filter(Boolean).join(", ") || "Lost";
  if (o.stage === "NO_BID") return o.no_bid_reason ? REASON_LABEL[o.no_bid_reason] : "";
  return r.organization ?? "";
}
