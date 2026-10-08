"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import TenderLine from "@/components/TenderLine";
import { Loading, Notice, Panel } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { Overview, TenderRow } from "@/lib/types";

interface ReviewItem { id: number; code: string; reason: string; tender: TenderRow }

function plural(n: number, one: string, many: string) {
  return `${n} ${n === 1 ? one : many}`;
}

export default function TodayView() {
  const [ov, setOv] = useState<Overview | null>(null);
  const [pursue, setPursue] = useState<TenderRow[]>([]);
  const [reviews, setReviews] = useState<ReviewItem[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      api<Overview>("/overview"),
      api<{ items: TenderRow[] }>("/tenders?decision=ACCEPTED&sort=score&page_size=8"),
      api<ReviewItem[]>("/reviews?status=OPEN"),
    ]).then(([o, p, r]) => {
      setOv(o);
      setPursue(p.items);
      setReviews(r);
    }).catch((e) => setError(e.message));
  }, []);

  if (error) return <Notice tone="error">Could not load the overview: {error}</Notice>;
  if (!ov) return <Loading />;

  const today = new Intl.DateTimeFormat("en-IN", { weekday: "long", day: "numeric", month: "long", timeZone: "Asia/Kolkata" }).format(new Date());
  const reviewTenders = new Map<number, ReviewItem>();
  reviews.forEach((r) => { if (!reviewTenders.has(r.tender.id)) reviewTenders.set(r.tender.id, r); });

  return (
    <div className="mx-auto max-w-6xl">
      <h1 className="font-serif text-3xl font-semibold tracking-tight">{today}</h1>
      <p className="mt-3 max-w-3xl text-lg leading-relaxed">
        {ov.accepted === 0 && ov.manual_review === 0 ? (
          <>No open tenders match the catalog yet. {plural(ov.total, "tender has", "tenders have")} been screened so far.</>
        ) : (
          <>
            <Link href="/tenders?decision=ACCEPTED" className="font-semibold text-teal hover:underline">{plural(ov.accepted, "tender", "tenders")} worth pursuing</Link>
            {ov.hot > 0 && <>, <span className="font-semibold text-hot">{ov.hot} hot</span></>}
            {ov.high > 0 && <>, {ov.high} high</>}.{" "}
            <Link href="/reviews" className="font-semibold text-review hover:underline">{plural(ov.manual_review, "decision", "decisions")}</Link> waiting on you.{" "}
            {ov.closing_soon > 0 && <><Link href="/tenders?decision=LIVE&closing_within_days=7" className="font-semibold text-high hover:underline">{ov.closing_soon} close within a week</Link>. </>}
          </>
        )}
      </p>
      <p className="mt-2 text-sm text-muted">
        {ov.new_24h} new in the last 24 hours, {ov.rejected} screened out as not a fit.
        {ov.analysis_mode === "RULES_ONLY" && " Running in rules-only mode: set ANTHROPIC_API_KEY to enable document understanding."}
      </p>

      <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_22rem]">
        <div className="space-y-6">
          <Panel title="Pursue" aside={<Link href="/tenders?decision=ACCEPTED" className="text-sm text-teal hover:underline">All accepted</Link>}>
            {pursue.length === 0 ? (
              <p className="text-sm text-muted">Nothing has qualified yet. Qualified tenders appear here as soon as discovery finds them.</p>
            ) : (
              <ul className="-my-3 divide-y divide-line">{pursue.map((t) => <TenderLine key={t.id} t={t} />)}</ul>
            )}
          </Panel>
          <Panel title="Needs a decision" aside={<Link href="/reviews" className="text-sm text-teal hover:underline">Open queue</Link>}>
            {reviewTenders.size === 0 ? (
              <p className="text-sm text-muted">The review queue is clear.</p>
            ) : (
              <ul className="-my-3 divide-y divide-line">
                {[...reviewTenders.values()].slice(0, 6).map((r) => <TenderLine key={r.id} t={r.tender} note={r.reason} />)}
              </ul>
            )}
          </Panel>
        </div>

        <div className="space-y-6">
          <Panel title="Open opportunities by type">
            <dl className="grid grid-cols-[1fr_auto] gap-y-1.5 text-[15px]">
              <dt><Link href="/tenders?decision=LIVE&opportunity_type=SERVICE" className="hover:underline">Service</Link></dt><dd className="num text-right font-semibold">{ov.service}</dd>
              <dt><Link href="/tenders?decision=LIVE&opportunity_type=OEM" className="hover:underline">OEM / product</Link></dt><dd className="num text-right font-semibold">{ov.oem}</dd>
              <dt><Link href="/tenders?decision=LIVE&opportunity_type=HYBRID" className="hover:underline">Hybrid</Link></dt><dd className="num text-right font-semibold">{ov.hybrid}</dd>
              <dt className="text-muted"><Link href="/tenders?decision=REJECTED&include_closed=true" className="hover:underline">Screened out</Link></dt><dd className="num text-right text-muted">{ov.rejected}</dd>
            </dl>
            {ov.awaiting_documents > 0 && (
              <p className="mt-4 border-t border-line pt-3 text-sm">
                <span className="font-semibold">{plural(ov.awaiting_documents, "live tender needs", "live tenders need")}</span> documents uploaded
                before they can be fully assessed.
              </p>
            )}
          </Panel>
          <Panel title="Sources" aside={<Link href="/sources" className="text-sm text-teal hover:underline">Manage</Link>}>
            <ul className="space-y-3 text-sm">
              {ov.portals.map((p) => (
                <li key={p.code}>
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-medium">{p.name}</span>
                    <span className={p.last_status === "OK" ? "text-accept" : p.last_status ? "text-high" : "text-muted"}>
                      {p.code === "manual" ? "" : p.last_status ?? "not run yet"}
                    </span>
                  </div>
                  <p className="text-muted">
                    {p.tenders} tenders{p.last_run_at ? `, last checked ${dateTime(p.last_run_at)}` : ""}
                  </p>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      </div>
    </div>
  );
}
