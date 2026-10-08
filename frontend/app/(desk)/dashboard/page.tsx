"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type ReactNode } from "react";
import { Bars, CategoryColumns, Donut, Funnel, KpiCard, TrendLine } from "@/components/dash";
import { Loading, Notice, ScoreMark } from "@/components/ui";
import { api } from "@/lib/api";
import { closesIn, dateTime, daysUntil, inr, pct, STAGE_LABEL, TYPE_LABEL } from "@/lib/format";
import type { Dashboard, DashKpi, Priority } from "@/lib/types";

const RANGES = [{ days: 7, label: "7 days" }, { days: 30, label: "30 days" }, { days: 90, label: "90 days" },
  { days: 365, label: "12 months" }, { days: 0, label: "All time" }];
const TYPE_COLOR: Record<string, string> = { SERVICE: "var(--type-service)", OEM: "var(--type-oem)", HYBRID: "var(--type-hybrid)" };
const FILTER_LABEL: Record<string, string> = { portal: "Source", type: "Type", segment: "Buyer", capability: "Capability" };
const dayFmt = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });

export default function DashboardPage() {
  return <Suspense fallback={<Loading />}><DashboardView /></Suspense>;
}

function fmt(k: DashKpi): string {
  if (k.value === null) return "—";
  if (k.format === "inr") return k.value ? inr(k.value) : "₹0";
  if (k.format === "pct") return pct(k.value);
  if (k.format === "score") return k.value.toFixed(0);
  return k.value.toLocaleString("en-IN");
}

function DashboardView() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const days = Number(params.get("days") ?? 90);
  const filters = Object.fromEntries(["portal", "type", "segment", "capability"].flatMap((k) => (params.get(k) ? [[k, params.get(k)!]] : []))) as Record<string, string>;
  const query = new URLSearchParams({ days: String(days), ...filters }).toString();
  const [data, setData] = useState<{ query: string; d: Dashboard } | null>(null);
  const [error, setError] = useState("");
  const loading = data?.query !== query;

  useEffect(() => {
    let live = true;
    api<Dashboard>(`/dashboard?${query}`).then((d) => live && setData({ query, d })).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [query]);

  function set(changes: Record<string, string | number | null>) {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(changes)) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, String(v));
    }
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  }

  const d = data?.d;
  return (
    <div className="mx-auto max-w-[90rem]">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-semibold tracking-tight">Dashboard</h1>
          <p className="mt-1 text-muted">
            Everything the agent has found, in one view{d ? `. Updated ${dateTime(d.as_of)}` : ""}.
          </p>
        </div>
        <div className="flex flex-wrap gap-1 rounded-md border border-line bg-surface p-1 text-sm" role="group" aria-label="Date range">
          {RANGES.map((r) => (
            <button key={r.days} type="button" aria-pressed={days === r.days} onClick={() => set({ days: r.days })}
              className={`rounded px-3 py-1.5 ${days === r.days ? "bg-teal font-semibold text-surface" : "hover:bg-sunken"}`}>
              {r.label}
            </button>
          ))}
        </div>
      </header>

      {d && (
        <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
          <Select label="Source" value={filters.portal} onChange={(v) => set({ portal: v })}
            options={d.filters.options.portals.map((p) => ({ value: p.code, label: p.name }))} />
          <Select label="Type" value={filters.type} onChange={(v) => set({ type: v })}
            options={d.filters.options.types.map((t) => ({ value: t.code, label: t.label }))} />
          <Select label="Buyer" value={filters.segment} onChange={(v) => set({ segment: v })}
            options={d.filters.options.segments.map((s) => ({ value: s, label: s }))} />
          <Select label="Capability" value={filters.capability} onChange={(v) => set({ capability: v })}
            options={d.filters.options.capabilities.map((c) => ({ value: c, label: c }))} />
          {Object.keys(filters).length > 0 && (
            <button type="button" onClick={() => set({ portal: null, type: null, segment: null, capability: null })}
              className="rounded-md px-2.5 py-1.5 text-teal hover:bg-sunken">Clear filters</button>
          )}
        </div>
      )}
      {d && Object.keys(filters).length > 0 && (
        <p className="mt-2 text-sm text-muted">
          Showing {Object.entries(filters).map(([k, v]) => `${FILTER_LABEL[k]}: ${k === "type" ? TYPE_LABEL[v] ?? v : k === "portal" ? d.filters.options.portals.find((p) => p.code === v)?.name ?? v : v}`).join("; ")}.
          {" "}Click a chart again to remove its filter.
        </p>
      )}

      {error && <div className="mt-4"><Notice tone="error">{error}</Notice></div>}
      {!d && !error && <Loading />}
      {d && (
        <div className={`mt-5 space-y-5 transition-opacity ${loading ? "opacity-60" : ""}`} aria-busy={loading}>
          <section aria-label="Key figures" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            {d.kpis.map((k, i) => (
              <KpiCard key={k.key} label={k.label} value={fmt(k)} note={k.note} delta={k.delta}
                lowerIsBetter={k.lower_is_better} spark={k.spark} emphasis={i === 2} />
            ))}
          </section>
          {!d.window.has_previous && d.window.days > 0 && (
            <p className="-mt-2 text-xs text-muted">Growth against the previous {d.window.days} days appears once the agent has that much history.</p>
          )}

          <div className="grid gap-5 xl:grid-cols-12">
            <Card title="Key insights" className="xl:col-span-5" aside={<span className="text-xs text-muted">computed from the figures on this page</span>}>
              <Insights items={d.insights} />
            </Card>
            <Card title="Volume over time" className="xl:col-span-7"
              aside={<span className="text-xs text-muted">per {d.window.bucket}, each chart on its own scale</span>}>
              <div className="grid gap-6 md:grid-cols-2">
                <TrendLine label="Tenders read" data={d.trend.map((b) => ({ x: b.date, y: b.read }))}
                  formatX={(x) => (d.window.bucket === "week" ? "w/c " : "") + dayFmt.format(new Date(x))} />
                <TrendLine label="Surfaced to the team" data={d.trend.map((b) => ({ x: b.date, y: b.surfaced }))}
                  formatX={(x) => (d.window.bucket === "week" ? "w/c " : "") + dayFmt.format(new Date(x))} />
              </div>
              <div className="mt-6 border-t border-line pt-4">
                <h3 className="mb-3 flex items-baseline justify-between text-sm"><span className="font-medium">From discovery to award</span>
                  <span className="text-xs text-muted">% = share of the step before</span></h3>
                <Funnel steps={d.funnel} />
              </div>
            </Card>
          </div>

          <div className="grid gap-5 lg:grid-cols-2 xl:grid-cols-3">
            <Card title="Opportunity type" aside={<span className="text-xs text-muted">surfaced tenders; click a type to filter</span>}>
              <Donut centerLabel="surfaced" active={filters.type ?? null} onSelect={(k) => set({ type: k })}
                slices={d.type_mix.map((m) => ({ key: m.code, label: m.label, value: m.count, color: TYPE_COLOR[m.code],
                  detail: m.value ? `${inr(m.value)} stated` : undefined }))} />
            </Card>
            <Card title="Deadlines ahead" aside={<span className="text-xs text-muted">open, surfaced tenders</span>}>
              <CategoryColumns highlight={d.deadlines[0]?.count ? d.deadlines[0].label : undefined}
                rows={d.deadlines.map((b) => ({ label: b.label, value: b.count, sub: b.value ? inr(b.value) : undefined }))} />
            </Card>
            <Card title="Bid pipeline" aside={<Link href="/pipeline" className="text-xs text-teal hover:underline">Open the pipeline</Link>}>
              <Bars rows={d.pipeline.map((p) => ({ label: STAGE_LABEL[p.stage] ?? p.stage, value: p.count }))} />
            </Card>
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <Card title="Demand by capability" aside={<span className="text-xs text-muted">surfaced; click a bar to drill down</span>}>
              <Bars rows={d.by_capability.map((c) => ({ label: c.label, value: c.count }))} active={filters.capability ?? null}
                onSelect={(v) => set({ capability: v })}
                sub={(r) => { const g = d.by_capability.find((c) => c.label === r.label); return g ? groupSub(g) : null; }} />
            </Card>
            <Card title="Demand by buyer" aside={<span className="text-xs text-muted">surfaced; click a bar to drill down</span>}>
              <Bars rows={d.by_segment.map((c) => ({ label: c.label, value: c.count }))} active={filters.segment ?? null}
                onSelect={(v) => set({ segment: v })}
                sub={(r) => { const g = d.by_segment.find((c) => c.label === r.label); return g ? groupSub(g) : null; }} />
            </Card>
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <Card title="Tender value" aside={<span className="text-xs text-muted">surfaced tenders by stated value</span>}>
              <CategoryColumns rows={d.value_bands.map((b) => ({ label: b.label, value: b.count }))} />
            </Card>
            <Card title="Why tenders were set aside" aside={<span className="text-xs text-muted">rejected, by reason</span>}>
              <Bars rows={d.rejections.map((r) => ({ label: r.label, value: r.count }))} />
            </Card>
          </div>

          <Card title="Source performance" aside={<span className="text-xs text-muted">click a source to filter</span>}>
            <div className="-mx-5 overflow-x-auto">
              <table className="w-full min-w-[44rem] text-sm">
                <thead className="text-left text-muted">
                  <tr className="border-b border-line">
                    <th className="px-5 py-2 font-medium">Source</th>
                    <th className="px-3 py-2 text-right font-medium">Read</th>
                    <th className="px-3 py-2 text-right font-medium">Surfaced</th>
                    <th className="px-3 py-2 font-medium">Yield</th>
                    <th className="px-3 py-2 text-right font-medium">Avg score</th>
                    <th className="px-5 py-2 font-medium">Last run</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {d.sources.map((s) => {
                    const maxYield = Math.max(...d.sources.map((x) => x.yield ?? 0), 0.0001);
                    const on = filters.portal === s.code;
                    return (
                      <tr key={s.code} className={on ? "bg-sunken" : "hover:bg-paper"}>
                        <td className="px-5 py-2">
                          <button type="button" className={`text-left hover:text-teal hover:underline ${on ? "font-semibold" : ""}`}
                            aria-pressed={on} onClick={() => set({ portal: on ? null : s.code })}>{s.name}</button>
                        </td>
                        <td className="num px-3 py-2 text-right">{s.read.toLocaleString("en-IN")}</td>
                        <td className="num px-3 py-2 text-right">{s.surfaced}</td>
                        <td className="px-3 py-2">
                          <div className="flex items-center gap-2">
                            <span className="relative h-2 w-24 rounded-r-[4px] bg-sunken" aria-hidden>
                              <span className="absolute inset-y-0 left-0 rounded-r-[4px] bg-chart-1" style={{ width: `${((s.yield ?? 0) / maxYield) * 100}%` }} />
                            </span>
                            <span className="num w-12 text-right">{pct(s.yield, 1)}</span>
                          </div>
                        </td>
                        <td className="num px-3 py-2 text-right">{s.avg_score?.toFixed(0) ?? "—"}</td>
                        <td className="px-5 py-2 text-muted">
                          {s.last_run_at ? dateTime(s.last_run_at) : "manual"}
                          {s.status && s.status !== "OK" && <span className={`ml-2 text-xs font-medium ${s.status === "PARTIAL" ? "text-high" : "text-hot"}`}>{s.status.toLowerCase()}</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="Top open opportunities" aside={<Link href="/tenders?decision=LIVE" className="text-xs text-teal hover:underline">All tenders</Link>}>
            {d.top.length === 0 ? <p className="py-4 text-sm text-muted">No open opportunities in this slice.</p> : (
              <div className="-mx-5 overflow-x-auto">
                <table className="w-full min-w-[56rem] text-sm">
                  <thead className="text-left text-muted">
                    <tr className="border-b border-line">
                      <th className="px-5 py-2 font-medium">Score</th>
                      <th className="px-3 py-2 font-medium">Tender</th>
                      <th className="px-3 py-2 font-medium">Type</th>
                      <th className="px-3 py-2 text-right font-medium">Value</th>
                      <th className="px-3 py-2 font-medium">Closes</th>
                      <th className="px-5 py-2 font-medium">Bid status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line align-top">
                    {d.top.map((t) => {
                      const left = daysUntil(t.closing_at);
                      return (
                        <tr key={t.id} className="hover:bg-paper">
                          <td className="px-5 py-3"><ScoreMark score={t.score} priority={t.priority as Priority | null} stacked /></td>
                          <td className="max-w-xl px-3 py-3">
                            <Link href={`/tenders/${t.id}`} className="font-serif leading-snug hover:text-teal hover:underline">{t.title}</Link>
                            <p className="text-muted">{t.organization ?? "—"}{t.capability ? `, ${t.capability}` : ""}</p>
                            {t.plain_summary && (
                              <p className="mt-1.5 rounded-sm border-l-[3px] border-pink-edge bg-pink-soft px-2 py-1 leading-snug">{t.plain_summary}</p>
                            )}
                          </td>
                          <td className="px-3 py-3">
                            <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
                              <span className="h-2.5 w-2.5 rounded-sm" style={{ background: TYPE_COLOR[t.type] ?? "var(--low)" }} aria-hidden />
                              {TYPE_LABEL[t.type] ?? t.type}
                            </span>
                          </td>
                          <td className="num px-3 py-3 text-right">{inr(t.value)}</td>
                          <td className={`whitespace-nowrap px-3 py-3 ${left !== null && left < 7 ? "font-medium text-high" : ""}`}>{closesIn(t.closing_at)}</td>
                          <td className="whitespace-nowrap px-5 py-3">{t.stage ? STAGE_LABEL[t.stage] : t.decision === "MANUAL_REVIEW" ? "Needs review" : "Not started"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}

function groupSub(g: { value: number; avg_score: number | null }): string | null {
  const parts = [g.avg_score !== null ? `avg score ${g.avg_score.toFixed(0)}` : null, g.value ? `${inr(g.value)} stated` : null].filter(Boolean);
  return parts.length ? parts.join(", ") : null;
}

function Card({ title, aside, children, className = "" }: { title: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-md border border-line bg-surface ${className}`}>
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-5 pt-4">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        {aside}
      </header>
      <div className="px-5 pb-5 pt-3">{children}</div>
    </section>
  );
}

function Select({ label, value, options, onChange }: {
  label: string; value?: string; options: { value: string; label: string }[]; onChange: (v: string | null) => void;
}) {
  return (
    <label className={`flex items-center gap-2 rounded-md border bg-surface py-1 pl-3 pr-1 ${value ? "border-teal" : "border-line"}`}>
      <span className="text-muted">{label}</span>
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}
        className="max-w-[14rem] truncate rounded bg-transparent py-0.5 pr-1 font-medium">
        <option value="">All</option>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );
}

const KIND: Record<string, { label: string; icon: string; cls: string }> = {
  risk: { label: "Risk", icon: "!", cls: "border-hot text-hot" },
  opportunity: { label: "Opportunity", icon: "★", cls: "border-accept text-accept" },
  trend: { label: "Trend", icon: "↗", cls: "border-medium text-medium" },
  anomaly: { label: "Anomaly", icon: "◆", cls: "border-high text-high" },
  info: { label: "Note", icon: "i", cls: "border-line text-muted" },
};

function Insights({ items }: { items: Dashboard["insights"] }) {
  if (!items.length) return <p className="text-sm text-muted">Nothing stands out in this slice.</p>;
  return (
    <ul className="space-y-3">
      {items.map((i, n) => {
        const k = KIND[i.kind] ?? KIND.info;
        return (
          <li key={n} className={`border-l-[3px] pl-3 ${k.cls.split(" ")[0]}`}>
            <p className="text-sm leading-snug">
              <span className={`mr-1.5 text-xs font-semibold ${k.cls.split(" ")[1]}`}><span aria-hidden>{k.icon}</span> {k.label}</span>
              <b className="font-semibold">{i.title}</b>
            </p>
            <p className="mt-0.5 text-sm leading-snug text-muted">{i.detail}</p>
            {i.tender_ids && i.tender_ids.length > 0 && (
              <p className="mt-0.5 text-xs">{i.tender_ids.map((id, j) => (
                <span key={id}>{j > 0 && ", "}<Link href={`/tenders/${id}`} className="text-teal hover:underline">tender {id}</Link></span>
              ))}</p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
