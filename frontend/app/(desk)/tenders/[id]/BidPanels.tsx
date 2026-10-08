"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Button, DecisionTag, Panel } from "@/components/ui";
import { api } from "@/lib/api";
import { date, dateTime, inr, REASON_LABEL, STAGE_LABEL } from "@/lib/format";
import type { HistoryOut, Outcome } from "@/lib/types";

interface OutcomeResp { outcome: Outcome | null; stages: string[]; no_bid_reasons: string[]; loss_reasons: string[] }

const toNum = (s: string) => (s.trim() === "" ? null : Number(s.replace(/[,\s]/g, "")));

/** Where the team is with this tender, and the result once known. Analysts edit; viewers read. */
export function BidStatusPanel({ tenderId, editable, onSaved }: { tenderId: number; editable: boolean; onSaved: () => void }) {
  const [data, setData] = useState<OutcomeResp | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    api<OutcomeResp>(`/tenders/${tenderId}/outcome`).then(setData).catch((e) => setErr(e.message));
  }, [tenderId]);

  if (!data) return <Panel title="Bid status"><p className="text-sm text-muted">{err || "Loading…"}</p></Panel>;
  const o = data.outcome;

  function start() {
    setForm({
      stage: o?.stage ?? "CONSIDERING", no_bid_reason: o?.no_bid_reason ?? "", loss_reason: o?.loss_reason ?? "",
      our_bid_value_inr: o?.our_bid_value_inr?.toString() ?? "", award_value_inr: o?.award_value_inr?.toString() ?? "",
      winner: o?.winner ?? "", our_rank: o?.our_rank?.toString() ?? "", notes: o?.notes ?? "",
    });
    setErr("");
    setEditing(true);
  }

  async function save() {
    setBusy(true);
    setErr("");
    const body = {
      stage: form.stage, no_bid_reason: form.stage === "NO_BID" ? form.no_bid_reason || null : null,
      loss_reason: form.stage === "LOST" ? form.loss_reason || null : null,
      our_bid_value_inr: toNum(form.our_bid_value_inr), award_value_inr: toNum(form.award_value_inr),
      winner: form.winner.trim() || null, our_rank: toNum(form.our_rank), notes: form.notes.trim() || null,
    };
    if ([body.our_bid_value_inr, body.award_value_inr, body.our_rank].some((v) => v !== null && Number.isNaN(v))) {
      setErr("Values and rank must be numbers.");
      setBusy(false);
      return;
    }
    try {
      const r = await api<{ outcome: Outcome }>(`/tenders/${tenderId}/outcome`, { method: "PUT", body: JSON.stringify(body) });
      setData({ ...data!, outcome: r.outcome });
      setEditing(false);
      onSaved();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setForm({ ...form, [k]: e.target.value });
  const field = "mt-1 w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm";
  const showResult = ["SUBMITTED", "WON", "LOST"].includes(form.stage);

  if (editing) {
    return (
      <Panel title="Bid status">
        <div className="space-y-3 text-sm">
          <label className="block">Stage
            <select className={field} value={form.stage} onChange={set("stage")}>
              {data.stages.map((s) => <option key={s} value={s}>{STAGE_LABEL[s] ?? s}</option>)}
            </select>
          </label>
          {form.stage === "NO_BID" && (
            <label className="block">Why not
              <select className={field} value={form.no_bid_reason} onChange={set("no_bid_reason")} required>
                <option value="">Choose a reason</option>
                {data.no_bid_reasons.map((r) => <option key={r} value={r}>{REASON_LABEL[r] ?? r}</option>)}
              </select>
            </label>
          )}
          {showResult && (
            <label className="block">Our bid value (₹)
              <input className={`${field} num`} inputMode="numeric" value={form.our_bid_value_inr} onChange={set("our_bid_value_inr")} />
            </label>
          )}
          {(form.stage === "WON" || form.stage === "LOST") && (
            <>
              <label className="block">Award value (₹)
                <input className={`${field} num`} inputMode="numeric" value={form.award_value_inr} onChange={set("award_value_inr")} />
              </label>
              {form.stage === "LOST" && (
                <>
                  <label className="block">Winning bidder
                    <input className={field} value={form.winner} onChange={set("winner")} maxLength={300} />
                  </label>
                  <div className="grid grid-cols-[1fr_5rem] gap-3">
                    <label className="block">Main reason
                      <select className={field} value={form.loss_reason} onChange={set("loss_reason")}>
                        <option value="">Unknown</option>
                        {data.loss_reasons.map((r) => <option key={r} value={r}>{REASON_LABEL[r] ?? r}</option>)}
                      </select>
                    </label>
                    <label className="block">Our rank
                      <input className={`${field} num`} inputMode="numeric" value={form.our_rank} onChange={set("our_rank")} placeholder="2" />
                    </label>
                  </div>
                </>
              )}
            </>
          )}
          <label className="block">Notes
            <textarea className={field} rows={2} value={form.notes} onChange={set("notes")} maxLength={4000} />
          </label>
          {err && <p className="text-hot">{err}</p>}
          <div className="flex gap-2">
            <Button onClick={save} disabled={busy || (form.stage === "NO_BID" && !form.no_bid_reason)}>Save status</Button>
            <Button variant="quiet" onClick={() => setEditing(false)} disabled={busy}>Cancel</Button>
          </div>
        </div>
      </Panel>
    );
  }

  return (
    <Panel title="Bid status" aside={editable && <button className="text-sm text-teal hover:underline" onClick={start}>{o ? "Update" : "Start tracking"}</button>}>
      {!o ? (
        <p className="text-sm text-muted">Not tracked yet.{editable ? " Start tracking when the team picks this up, and record the result when it is announced." : ""}</p>
      ) : (
        <dl className="space-y-2 text-sm">
          <div><dt className="text-muted">Stage</dt><dd className="font-semibold">{STAGE_LABEL[o.stage] ?? o.stage}</dd></div>
          {o.no_bid_reason && <div><dt className="text-muted">Reason</dt><dd>{REASON_LABEL[o.no_bid_reason]}</dd></div>}
          {o.our_bid_value_inr !== null && <div><dt className="text-muted">Our bid</dt><dd className="num">{inr(o.our_bid_value_inr)}</dd></div>}
          {o.award_value_inr !== null && <div><dt className="text-muted">Awarded at</dt><dd className="num">{inr(o.award_value_inr)}</dd></div>}
          {o.winner && <div><dt className="text-muted">Won by</dt><dd>{o.winner}{o.our_rank ? `, we ranked L${o.our_rank}` : ""}</dd></div>}
          {o.loss_reason && <div><dt className="text-muted">Main reason</dt><dd>{REASON_LABEL[o.loss_reason]}</dd></div>}
          {o.notes && <div><dt className="text-muted">Notes</dt><dd className="whitespace-pre-line">{o.notes}</dd></div>}
          <p className="pt-1 text-muted">Updated {dateTime(o.updated_at)}{o.updated_by ? `, ${o.updated_by}` : ""}</p>
        </dl>
      )}
    </Panel>
  );
}

/** Past tenders like this one, this buyer's record with us, and whether it looks like an annual re-issue. */
export function PastTendersPanel({ tenderId }: { tenderId: number }) {
  const [h, setH] = useState<HistoryOut | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    api<HistoryOut>(`/tenders/${tenderId}/history`).then(setH).catch((e) => setErr(e.message));
  }, [tenderId]);

  if (!h) return <Panel title="Past tenders like this"><p className="text-sm text-muted">{err || "Loading…"}</p></Panel>;
  const b = h.buyer;
  return (
    <Panel title="Past tenders like this">
      {h.recurrence && (
        <p className="mb-4 border-l-[3px] border-teal pl-3 text-sm leading-relaxed">
          <b>Looks like a re-issue.</b> The same buyer published a near-identical tender on {date(h.recurrence.previous_published_at)}
          {" "}({Math.round(h.recurrence.interval_days)} days earlier):{" "}
          <Link href={`/tenders/${h.recurrence.previous_id}`} className="text-teal hover:underline">{h.recurrence.previous_title}</Link>.
          {" "}If it repeats, expect the next one around {date(h.recurrence.next_expected_around)}.
        </p>
      )}

      {h.similar.length === 0 ? (
        <p className="text-sm text-muted">No earlier tender in the agent&apos;s records is close enough to this one to compare.</p>
      ) : (
        <ul className="-my-2 divide-y divide-line">
          {h.similar.map((s) => (
            <li key={s.id} className="grid grid-cols-[1fr_auto] gap-x-4 py-2 text-sm">
              <div className="min-w-0">
                <Link href={`/tenders/${s.id}`} className="hover:text-teal hover:underline">{s.title}</Link>
                <p className="text-muted">
                  {s.organization ?? "Unknown organisation"}, {date(s.published_at)}
                  {s.value_inr ? `, ${inr(s.value_inr)}` : ""}
                  {s.outcome ? `. ${STAGE_LABEL[s.outcome] ?? s.outcome}` : ""}
                  {s.outcome === "LOST" && s.winner ? ` to ${s.winner}` : ""}
                  {s.award_value_inr ? ` at ${inr(s.award_value_inr)}` : ""}
                </p>
              </div>
              <div className="flex flex-col items-end gap-1">
                <DecisionTag decision={s.decision} />
                <span className="num text-xs text-muted" title="How close the subject is, from wording and meaning">{Math.round(s.similarity * 100)}% alike</span>
              </div>
            </li>
          ))}
        </ul>
      )}

      {b && (
        <div className="mt-4 border-t border-line pt-3 text-sm">
          <h3 className="font-semibold">{b.organization}</h3>
          <p className="mt-0.5 text-muted">
            {b.tenders_seen === 0 ? "The first tender the agent has seen from this buyer." : <>
              {b.tenders_seen} other tender{b.tenders_seen === 1 ? "" : "s"} seen, {b.surfaced} surfaced
              {b.bid > 0 ? `, ${b.bid} bid on, ${b.won} won, ${b.lost} lost` : ", none bid on yet"}.
            </>}
          </p>
          {b.recent.length > 0 && (
            <ul className="mt-2 space-y-1">
              {b.recent.map((r) => (
                <li key={r.id} className="flex justify-between gap-3">
                  <Link href={`/tenders/${r.id}`} className="truncate hover:text-teal hover:underline">{r.title}</Link>
                  <span className="shrink-0 text-muted">{date(r.published_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Panel>
  );
}
