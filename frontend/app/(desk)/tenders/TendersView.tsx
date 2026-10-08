"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { DecisionTag, Loading, Notice, ScoreBar, ScoreMark, TypeTag } from "@/components/ui";
import { api } from "@/lib/api";
import { closesIn, date, daysUntil, inr } from "@/lib/format";
import type { TenderRow } from "@/lib/types";

const FILTERS: { key: string; label: string; options: [string, string][] }[] = [
  { key: "decision", label: "Decision", options: [["LIVE", "Pursue or review"], ["ACCEPTED", "Pursue"], ["MANUAL_REVIEW", "Needs review"], ["REJECTED", "Rejected"], ["", "All"]] },
  { key: "priority", label: "Priority", options: [["", "Any"], ["HOT", "Hot"], ["HIGH", "High"], ["MEDIUM", "Medium"], ["LOW", "Low"]] },
  { key: "opportunity_type", label: "Type", options: [["", "Any"], ["SERVICE", "Service"], ["OEM", "OEM / product"], ["HYBRID", "Hybrid"], ["UNRELATED", "Unrelated"]] },
  { key: "sort", label: "Sort by", options: [["score", "Score"], ["closing_at", "Closing soonest"], ["published_at", "Newest published"], ["first_seen_at", "Recently discovered"]] },
];

export default function TendersView() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const [result, setResult] = useState<{ key: string; data: { total: number; items: TenderRow[]; page: number; page_size: number } } | null>(null);
  const [error, setError] = useState("");
  const [sources, setSources] = useState<{ code: string; name: string }[]>([]);

  useEffect(() => {
    api<{ code: string; name: string; enabled: boolean }[]>("/portals")
      .then((ps) => setSources(ps.filter((p) => p.enabled))).catch(() => {});
  }, []);

  const query = new URLSearchParams(params.toString());
  if (!query.has("decision")) query.set("decision", "LIVE");
  const key = query.toString();
  const data = result?.key === key ? result.data : null; // stale results are hidden while the new query loads

  useEffect(() => {
    let live = true;
    api<{ total: number; items: TenderRow[]; page: number; page_size: number }>(`/tenders?${key}`)
      .then((d) => live && setResult({ key, data: d })).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [key]);

  function set(key: string, value: string) {
    const next = new URLSearchParams(query.toString());
    if (value) next.set(key, value); else next.delete(key);
    if (key !== "page") next.delete("page");
    router.push(`${pathname}?${next.toString()}`);
  }

  function search(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    set("q", String(new FormData(e.currentTarget).get("q") ?? "").trim());
  }

  const page = Number(query.get("page") ?? 1);
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const decision = query.get("decision") ?? "LIVE";

  return (
    <div className="mx-auto max-w-[90rem]">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-semibold tracking-tight">Tenders</h1>
          <p className="mt-1 text-muted">
            {data ? `${data.total.toLocaleString("en-IN")} ${decision === "REJECTED" ? "screened out" : "matching"}` : " "}
            {query.get("include_closed") ? ", including closed" : ", open for bidding"}
          </p>
        </div>
        <form onSubmit={search} className="flex gap-2" role="search">
          <label className="sr-only" htmlFor="q">Search tenders</label>
          <input id="q" name="q" defaultValue={query.get("q") ?? ""} placeholder="Title, organisation or reference"
            className="w-72 max-w-full rounded-md border border-line bg-surface px-3 py-1.5 text-sm" />
          <button className="rounded-md border border-line bg-surface px-3 py-1.5 text-sm hover:bg-sunken">Search</button>
        </form>
      </div>

      <div className="mt-5 flex flex-wrap gap-x-6 gap-y-3">
        {FILTERS.map((f) => (
          <label key={f.key} className="text-sm">
            <span className="mr-2 text-muted">{f.label}</span>
            <select value={query.get(f.key) ?? (f.key === "sort" ? "score" : f.key === "decision" ? "LIVE" : "")}
              onChange={(e) => set(f.key, e.target.value)} className="rounded-md border border-line bg-surface px-2 py-1">
              {f.options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
        ))}
        {sources.length > 1 && (
          <label className="text-sm">
            <span className="mr-2 text-muted">Source</span>
            <select value={query.get("portal") ?? ""} onChange={(e) => set("portal", e.target.value)}
              className="max-w-[16rem] rounded-md border border-line bg-surface px-2 py-1">
              <option value="">All sources</option>
              {sources.map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
            </select>
          </label>
        )}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!!query.get("include_closed")} onChange={(e) => set("include_closed", e.target.checked ? "true" : "")} />
          Include closed
        </label>
      </div>

      {error && <div className="mt-4"><Notice tone="error">{error}</Notice></div>}
      {!data && !error && <Loading />}
      {data && data.items.length === 0 && (
        <p className="mt-10 text-muted">No tenders match these filters. Try widening the decision or type filter.</p>
      )}
      {data && data.items.length > 0 && (
        <div className="mt-5 overflow-x-auto rounded-md border border-line bg-surface">
          <table className="w-full min-w-[68rem] text-sm">
            <thead className="border-b border-line text-left text-muted">
              <tr>
                <th className="px-4 py-2.5 font-medium">Score</th>
                <th className="px-4 py-2.5 font-medium">Tender</th>
                <th className="px-4 py-2.5 font-medium">Type</th>
                <th className="px-4 py-2.5 font-medium">Matched capability / products</th>
                <th className="px-4 py-2.5 text-right font-medium">Tender value</th>
                <th className="px-4 py-2.5 text-right font-medium">Service value</th>
                <th className="px-4 py-2.5 font-medium">Closes</th>
                <th className="px-4 py-2.5 text-right font-medium">Match conf.</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {data.items.map((t) => {
                const d = daysUntil(t.closing_at);
                return (
                  <tr key={t.id} className="align-top hover:bg-paper">
                    <td className="w-32 px-4 py-3">
                      <ScoreMark score={t.score} priority={t.priority} />
                      <div className="mt-1.5 w-24"><ScoreBar parts={t.score_parts} height={5} /></div>
                    </td>
                    <td className="min-w-[20rem] max-w-xl px-4 py-3">
                      <Link href={`/tenders/${t.id}`} className="font-serif text-[15px] leading-snug hover:text-teal hover:underline">{t.title}</Link>
                      <p className="mt-0.5 text-muted">{t.organization ?? t.location ?? "—"}</p>
                      {t.documents_status === "BLOCKED_HUMAN_REQUIRED" && t.decision !== "REJECTED" && (
                        <p className="mt-0.5 text-xs text-high">Documents need manual download</p>
                      )}
                    </td>
                    <td className="px-4 py-3"><TypeTag type={t.opportunity_type} /></td>
                    <td className="max-w-[16rem] px-4 py-3">
                      <p>{t.matched_capability ?? "—"}</p>
                      {t.matched_products.length > 0 && <p className="text-muted">{t.matched_products.join(", ")}</p>}
                    </td>
                    <td className="num px-4 py-3 text-right">{inr(t.tender_value_inr)}</td>
                    <td className="num px-4 py-3 text-right">{inr(t.service_value_inr)}</td>
                    <td className="px-4 py-3">
                      <p className="whitespace-nowrap">{date(t.closing_at)}</p>
                      <p className={`whitespace-nowrap text-xs ${d !== null && d >= 0 && d < 7 ? "font-medium text-high" : "text-muted"}`}>{closesIn(t.closing_at)}</p>
                    </td>
                    <td className="num px-4 py-3 text-right">{t.match_confidence ?? "—"}</td>
                    <td className="px-4 py-3"><DecisionTag decision={t.decision} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {data && pages > 1 && (
        <nav className="mt-4 flex items-center gap-3 text-sm" aria-label="Pagination">
          <button disabled={page <= 1} onClick={() => set("page", String(page - 1))} className="rounded-md border border-line px-3 py-1 disabled:opacity-40">Previous</button>
          <span className="text-muted">Page {page} of {pages}</span>
          <button disabled={page >= pages} onClick={() => set("page", String(page + 1))} className="rounded-md border border-line px-3 py-1 disabled:opacity-40">Next</button>
        </nav>
      )}
    </div>
  );
}
