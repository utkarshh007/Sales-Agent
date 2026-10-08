"use client";

import { useEffect, useState } from "react";
import { BarList, Columns, Stat } from "@/components/charts";
import { Loading, Notice, Panel } from "@/components/ui";
import { api } from "@/lib/api";
import { inr, pct, REASON_LABEL, TYPE_LABEL } from "@/lib/format";
import type { Analytics } from "@/lib/types";

const WINDOWS = [30, 90, 365] as const;
const weekFmt = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });
const REVIEW_LABEL: Record<string, string> = {
  SERVICE_VALUE_UNKNOWN: "Service value not stated", HYBRID_REVIEW_REQUIRED: "Hybrid split unclear",
  HYBRID_SERVICE_COMPONENT_OVER_CAP: "Hybrid service part over limit", ADJACENT_ONLY: "Adjacent match only",
  LLM_DECLINED: "LLM declined", CLASSIFICATION_UNKNOWN: "Type unclear",
};

export default function InsightsPage() {
  const [days, setDays] = useState<number>(90);
  const [result, setResult] = useState<Analytics | null>(null);
  const [error, setError] = useState("");
  const a = result?.window_days === days ? result : null;

  useEffect(() => {
    let live = true;
    api<Analytics>(`/analytics?days=${days}`).then((r) => live && setResult(r)).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [days]);

  return (
    <div className="mx-auto max-w-6xl">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-semibold tracking-tight">Insights</h1>
          <p className="mt-1 max-w-2xl text-muted">
            How many tenders the agent reads, how few it puts in front of you, where the relevant ones come from, and what happened to the bids.
          </p>
        </div>
        <div className="flex gap-1 text-sm" role="tablist" aria-label="Time window">
          {WINDOWS.map((w) => (
            <button key={w} role="tab" aria-selected={days === w} onClick={() => setDays(w)}
              className={`rounded-md px-3 py-1.5 ${days === w ? "bg-teal-soft font-semibold text-teal" : "hover:bg-sunken"}`}>
              Last {w} days
            </button>
          ))}
        </div>
      </div>

      {error && <div className="mt-4"><Notice tone="error">{error}</Notice></div>}
      {!a && !error && <Loading />}
      {a && <Body a={a} />}
    </div>
  );
}

function Body({ a }: { a: Analytics }) {
  const f = a.funnel;
  const s = a.screening;
  const totalRejected = s.rejection_reasons.reduce((n, r) => n + r.count, 0);
  const pv = a.pipeline_value;
  return (
    <div className="mt-6 space-y-6">
      {a.history_days < a.window_days && (
        <Notice>
          The agent has been collecting for {Math.max(1, Math.round(a.history_days))} day{Math.round(a.history_days) === 1 ? "" : "s"}, so these figures cover that period rather than the full {a.window_days}.
        </Notice>
      )}

      <section aria-label="From discovery to award" className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Tenders read" value={f.discovered.toLocaleString("en-IN")} note="across all portals" />
        <Stat label="Surfaced to you" value={f.surfaced.toLocaleString("en-IN")} note={`${pct(f.surfaced_rate, 1)} of those read`} />
        <Stat label="Pursued" value={f.pursued.toLocaleString("en-IN")} note="considering or bidding" />
        <Stat label="Bids submitted" value={f.submitted.toLocaleString("en-IN")} />
        <Stat label="Won" value={f.won.toLocaleString("en-IN")}
          note={f.win_rate_sample ? `${pct(f.win_rate)} of ${f.win_rate_sample} decided` : "no decided bids yet"} />
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="Why tenders were set aside" aside={<span className="num text-sm text-muted">{totalRejected.toLocaleString("en-IN")} rejected</span>}>
          <p className="mb-4 text-sm text-muted">
            {pct(s.decided_without_llm_rate, 1)} of tenders were decided by the rules alone, before any AI analysis.
            {" "}{s.analysed_by_llm > 0 ? `${s.analysed_by_llm} went to the LLM.` : "None needed the LLM in this period."}
          </p>
          <BarList rows={s.rejection_reasons.slice(0, 8)} total={totalRejected} />
        </Panel>

        <Panel title="Open opportunities">
          <dl className="grid grid-cols-2 gap-4 text-sm">
            <div><dt className="text-muted">Open and surfaced</dt><dd className="num text-2xl font-semibold">{pv.open_opportunities}</dd></div>
            <div><dt className="text-muted">Preparing a bid</dt><dd className="num text-2xl font-semibold">{pv.bidding_now}</dd></div>
            <div className="col-span-2">
              <dt className="text-muted">Stated value</dt>
              <dd><span className="num text-2xl font-semibold">{inr(pv.stated_value_inr || null)}</span>
                <span className="ml-2 text-muted">from {pv.with_stated_value} of {pv.open_opportunities} that publish a value</span></dd>
            </div>
          </dl>
          {Object.keys(pv.stated_value_by_type).length > 0 && (
            <ul className="mt-4 space-y-1 border-t border-line pt-3 text-sm">
              {Object.entries(pv.stated_value_by_type).map(([k, v]) => (
                <li key={k} className="flex justify-between"><span>{TYPE_LABEL[k] ?? k}</span><span className="num text-muted">{inr(v)}</span></li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <Panel title="Where relevant tenders come from" aside={<span className="text-sm text-muted">sorted by tenders read per relevant one</span>}>
        <div className="-mx-5 overflow-x-auto">
          <table className="w-full min-w-[36rem] text-sm">
            <thead className="text-left text-muted">
              <tr className="border-b border-line">
                <th className="px-5 py-2 font-medium">Source</th>
                <th className="px-3 py-2 text-right font-medium">Read</th>
                <th className="px-3 py-2 text-right font-medium">Surfaced</th>
                <th className="px-3 py-2 text-right font-medium">Yield</th>
                <th className="px-5 py-2 text-right font-medium">Read per relevant</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {[...a.sources].sort((x, y) => (x.tenders_per_relevant ?? 1e9) - (y.tenders_per_relevant ?? 1e9)).map((r) => (
                <tr key={r.code} className={r.seen === 0 ? "text-muted" : ""}>
                  <td className="px-5 py-2">{r.name}{!r.enabled && <span className="ml-2 text-xs text-muted">(off)</span>}</td>
                  <td className="num px-3 py-2 text-right">{r.seen.toLocaleString("en-IN")}</td>
                  <td className="num px-3 py-2 text-right">{r.surfaced}</td>
                  <td className="num px-3 py-2 text-right">{pct(r.yield, 1)}</td>
                  <td className="num px-5 py-2 text-right">{r.tenders_per_relevant ? `1 in ${r.tenders_per_relevant.toLocaleString("en-IN")}` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Weekly volume" aside={<span className="text-sm text-muted">last 12 weeks, each on its own scale</span>}>
        <div className="grid gap-8 md:grid-cols-2">
          <Columns label="Tenders read" data={a.trend.map((w) => ({ x: w.week, y: w.discovered }))} formatX={(x) => weekFmt.format(new Date(x))} />
          <Columns label="Surfaced to you" data={a.trend.map((w) => ({ x: w.week, y: w.surfaced }))} formatX={(x) => weekFmt.format(new Date(x))} />
        </div>
      </Panel>

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="Surfaced tenders by capability">
          <BarList rows={a.mix.by_capability} total={f.surfaced} />
        </Panel>
        <Panel title="Surfaced tenders by buyer">
          <BarList rows={a.mix.by_segment} total={f.surfaced} />
          {a.mix.by_type.length > 0 && (
            <p className="mt-4 border-t border-line pt-3 text-sm text-muted">
              {a.mix.by_type.map((x) => `${x.label} ${x.count}`).join(", ")}
            </p>
          )}
        </Panel>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="Review queue">
          <dl className="grid grid-cols-3 gap-4 text-sm">
            <div><dt className="text-muted">Waiting</dt><dd className="num text-2xl font-semibold">{a.reviews.open}</dd></div>
            <div><dt className="text-muted">Decided</dt><dd className="num text-2xl font-semibold">{a.reviews.resolved}</dd></div>
            <div><dt className="text-muted">Median time to decide</dt>
              <dd className="num text-2xl font-semibold">{a.reviews.median_hours_to_decide === null ? "—" : `${Math.round(a.reviews.median_hours_to_decide)} h`}</dd></div>
          </dl>
          {a.reviews.by_reason.length > 0 && (
            <div className="mt-4 border-t border-line pt-4">
              <BarList rows={a.reviews.by_reason.map((r) => ({ label: REVIEW_LABEL[r.code] ?? r.code, count: r.count }))} />
            </div>
          )}
        </Panel>
        <Outcomes a={a} />
      </div>
    </div>
  );
}

function Outcomes({ a }: { a: Analytics }) {
  const o = a.outcomes;
  const rateRows = (rows: Analytics["outcomes"]["win_rate_by_capability"]) => rows.filter((r) => r.won + r.lost > 0);
  return (
    <Panel title="Bid results" aside={<span className="num text-sm text-muted">{o.decided} decided</span>}>
      {!o.enough_data && (
        <p className="text-sm text-muted">
          Win rates appear once 10 bids have a result; {o.decided === 0 ? "none have one yet" : `${o.decided} so far`}.
          Record results on each tender&apos;s Bid status panel. Portals keep award results behind a CAPTCHA, so the agent cannot fetch them for you.
        </p>
      )}
      {o.enough_data && (
        <div className="space-y-4 text-sm">
          {[["By capability", o.win_rate_by_capability], ["By buyer", o.win_rate_by_segment], ["By type", o.win_rate_by_type]].map(([title, rows]) => (
            <div key={title as string}>
              <h3 className="font-semibold">{title as string}</h3>
              <ul className="mt-1 space-y-0.5">
                {rateRows(rows as Analytics["outcomes"]["win_rate_by_capability"]).map((r) => (
                  <li key={r.label} className="flex justify-between gap-4"><span className="truncate">{r.label}</span>
                    <span className="num text-muted">{pct(r.win_rate)} ({r.won} of {r.won + r.lost})</span></li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
      {(o.competitors.length > 0 || o.loss_reasons.length > 0 || o.no_bid_reasons.length > 0) && (
        <div className="mt-4 grid gap-4 border-t border-line pt-4 text-sm sm:grid-cols-2">
          {o.competitors.length > 0 && (
            <div>
              <h3 className="font-semibold">Who beat us</h3>
              <ul className="mt-1 space-y-0.5">{o.competitors.map((c) => (
                <li key={c.name} className="flex justify-between gap-3"><span className="truncate">{c.name}</span><span className="num text-muted">{c.wins_against_us}</span></li>
              ))}</ul>
              {o.median_price_gap_when_lost !== null && (
                <p className="mt-2 text-muted">On lost bids we priced a median {pct(Math.abs(o.median_price_gap_when_lost))} {o.median_price_gap_when_lost >= 0 ? "above" : "below"} the winner.</p>
              )}
            </div>
          )}
          {[["Why we lost", o.loss_reasons], ["Why we did not bid", o.no_bid_reasons]].map(([title, rows]) => (rows as { reason: string; count: number }[]).length > 0 && (
            <div key={title as string}>
              <h3 className="font-semibold">{title as string}</h3>
              <ul className="mt-1 space-y-0.5">{(rows as { reason: string; count: number }[]).map((r) => (
                <li key={r.reason} className="flex justify-between gap-3"><span>{REASON_LABEL[r.reason] ?? r.reason}</span><span className="num text-muted">{r.count}</span></li>
              ))}</ul>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
