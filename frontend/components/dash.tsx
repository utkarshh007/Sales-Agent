"use client";

import { useId, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

/**
 * Executive-dashboard charts, dependency-free SVG/HTML. Dataviz rules: one validated hue (--chart-1) for any
 * single series; the three opportunity types use validated categorical slots; never a dual axis; thin marks with
 * 4px rounded data ends; 2px surface gaps; recessive axes; every value reachable without hover (labels or tables);
 * hover and keyboard focus show the same tooltip; clickable marks cross-filter the page.
 */

const nf = new Intl.NumberFormat("en-IN");

// ------------------------------------------------------------------ sparkline
export function Sparkline({ values, label }: { values: number[]; label: string }) {
  if (values.length < 2 || values.every((v) => v === 0)) return null;
  const w = 100, h = 24, max = Math.max(...values, 1);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - 2 - (v / max) * (h - 4)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="block h-6 w-full overflow-visible" role="img" aria-label={`${label} trend: ${values.join(", ")}`}>
      <polyline points={pts} fill="none" stroke="var(--chart-1)" strokeWidth={2} vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// ------------------------------------------------------------------ KPI card
export function KpiCard({ label, value, note, delta, lowerIsBetter, spark, emphasis = false }: {
  label: string; value: string; note?: string | null; delta?: number | null; lowerIsBetter?: boolean; spark?: number[] | null; emphasis?: boolean;
}) {
  const good = delta == null || delta === 0 ? null : (delta > 0) !== !!lowerIsBetter;
  return (
    <div className={`flex min-w-0 flex-col rounded-md border bg-surface px-4 py-3.5 ${emphasis ? "border-teal" : "border-line"}`}>
      <p className="text-sm text-muted">{label}</p>
      <p className="num mt-1 text-[28px] font-semibold leading-none tracking-tight">{value}</p>
      <div className="mt-2 flex flex-wrap items-baseline gap-x-2 text-sm">
        {delta != null && (
          <span className={`num font-medium ${good === null ? "text-muted" : good ? "text-accept" : "text-hot"}`}>
            <span aria-hidden>{delta > 0 ? "▲" : delta < 0 ? "▼" : "■"}</span> {Math.abs(delta * 100).toFixed(0)}%
            <span className="sr-only"> {delta > 0 ? "up" : "down"} on the previous period</span>
          </span>
        )}
        {note && <span className="text-muted">{note}</span>}
      </div>
      {spark && <div className="mt-auto pt-2"><Sparkline values={spark} label={label} /></div>}
    </div>
  );
}

// ------------------------------------------------------------------ line + area with crosshair
export function TrendLine({ data, label, formatX }: { data: { x: string; y: number }[]; label: string; formatX: (x: string) => string }) {
  const [active, setActive] = useState<number | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const gid = useId();
  // the plot stretches to its box (paths only); every label is HTML text at a fixed size, so nothing scales down
  const max = niceMax(Math.max(1, ...data.map((d) => d.y)));
  const xp = (i: number) => (data.length <= 1 ? 0 : (i / (data.length - 1)) * 100);
  const yp = (v: number) => (1 - v / max) * 100;
  const line = data.map((d, i) => `${i ? "L" : "M"}${xp(i)},${yp(d.y)}`).join(" ");
  const area = data.length ? `${line} L${xp(data.length - 1)},100 L0,100 Z` : "";

  function move(e: PointerEvent<HTMLDivElement>) {
    const r = ref.current!.getBoundingClientRect();
    const i = Math.round(((e.clientX - r.left) / r.width) * (data.length - 1));
    setActive(Math.max(0, Math.min(data.length - 1, i)));
  }
  function key(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "ArrowRight") setActive((a) => Math.min(data.length - 1, (a ?? -1) + 1));
    if (e.key === "ArrowLeft") setActive((a) => Math.max(0, (a ?? data.length) - 1));
  }
  return (
    <figure className="min-w-0">
      <figcaption className="mb-2 flex items-baseline justify-between text-sm">
        <span className="font-medium">{label}</span>
        <span className="num text-muted">total {nf.format(data.reduce((a, d) => a + d.y, 0))}</span>
      </figcaption>
      <div className="grid grid-cols-[auto_1fr] gap-x-2">
        <div className="num flex h-32 flex-col justify-between text-right text-xs leading-none text-muted" aria-hidden>
          <span>{compact(max)}</span><span>{compact(max / 2)}</span><span>0</span>
        </div>
        <div ref={ref} className="relative h-32 touch-none outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus)]" tabIndex={0}
          role="img" aria-label={`${label}: ${data.map((d) => `${formatX(d.x)} ${d.y}`).join(", ")}`}
          onPointerMove={move} onPointerLeave={() => setActive(null)} onFocus={() => setActive(data.length - 1)} onBlur={() => setActive(null)} onKeyDown={key}>
          <div className="absolute inset-x-0 top-0 border-t border-dashed border-line" aria-hidden />
          <div className="absolute inset-x-0 top-1/2 border-t border-dashed border-line" aria-hidden />
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 h-full w-full overflow-visible" aria-hidden>
            <defs>
              <linearGradient id={gid} x1="0" x2="0" y1="0" y2="1">
                <stop offset="0" stopColor="var(--chart-1)" stopOpacity="0.22" />
                <stop offset="1" stopColor="var(--chart-1)" stopOpacity="0.02" />
              </linearGradient>
            </defs>
            <path d={area} fill={`url(#${gid})`} />
            <path d={line} fill="none" stroke="var(--chart-1)" strokeWidth={2} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
          </svg>
          <div className="absolute inset-x-0 bottom-0 border-t border-line" aria-hidden />
          {active !== null && data[active] && (
            <>
              <div className="pointer-events-none absolute inset-y-0 w-px bg-ink/35" style={{ left: `${xp(active)}%` }} />
              <div className="pointer-events-none absolute h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface bg-chart-1"
                style={{ left: `${xp(active)}%`, top: `${yp(data[active].y)}%` }} />
              <div className="pointer-events-none absolute -top-2 z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs shadow-sm"
                style={{ left: `${Math.min(85, Math.max(15, xp(active)))}%` }}>
                <p className="num text-sm font-semibold">{nf.format(data[active].y)}</p>
                <p className="flex items-center gap-1.5 text-muted"><span className="inline-block h-0.5 w-3 bg-chart-1" />{formatX(data[active].x)}</p>
              </div>
            </>
          )}
        </div>
        <span />
        <div className="mt-1 flex justify-between text-xs text-muted" aria-hidden>
          <span>{data.length ? formatX(data[0].x) : ""}</span>
          <span>{data.length > 1 ? formatX(data[data.length - 1].x) : ""}</span>
        </div>
      </div>
    </figure>
  );
}

function niceMax(v: number): number {
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

function compact(v: number): string {
  return v >= 1e5 ? `${+(v / 1e5).toFixed(1)}L` : v >= 1000 ? `${+(v / 1000).toFixed(1)}k` : String(+v.toFixed(1));
}

// ------------------------------------------------------------------ donut
export interface Slice { key: string; label: string; value: number; color: string; detail?: string }

export function Donut({ slices, centerLabel, active, onSelect }: {
  slices: Slice[]; centerLabel: string; active?: string | null; onSelect?: (key: string | null) => void;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const total = slices.reduce((a, s) => a + s.value, 0);
  const R = 70, r = 46, C = 90;
  const nonzero = slices.filter((s) => s.value > 0);
  const starts = nonzero.map((_, i) => nonzero.slice(0, i).reduce((a, s) => a + s.value, 0));
  const arcs = nonzero.map((s, i) => {
    const a0 = -Math.PI / 2 + (starts[i] / total) * Math.PI * 2;
    const a1 = a0 + (s.value / total) * Math.PI * 2;
    return { ...s, d: arc(C, C, R, r, a0, a1) };
  });
  const shown = slices.find((s) => s.key === (hover ?? active));
  return (
    <div className="flex flex-wrap items-center gap-6">
      <svg viewBox="0 0 180 180" width={180} height={180} role="img" aria-label={slices.map((s) => `${s.label} ${s.value}`).join(", ")}>
        {total === 0 && <circle cx={C} cy={C} r={(R + r) / 2} fill="none" stroke="var(--sunken)" strokeWidth={R - r} />}
        {arcs.map((s) => (
          <path key={s.key} d={s.d} fill={s.color} stroke="var(--surface)" strokeWidth={2}
            opacity={active && active !== s.key ? 0.35 : 1}
            className={onSelect ? "cursor-pointer" : ""}
            onPointerEnter={() => setHover(s.key)} onPointerLeave={() => setHover(null)}
            onClick={() => onSelect?.(active === s.key ? null : s.key)} />
        ))}
        <text x={C} y={C - 2} textAnchor="middle" fontSize="26" fontWeight="600" fill="var(--ink)" className="num">
          {nf.format(shown ? shown.value : total)}
        </text>
        <text x={C} y={C + 18} textAnchor="middle" fontSize="11" fill="var(--muted)">{shown ? shown.label : centerLabel}</text>
      </svg>
      <ul className="min-w-[10rem] flex-1 space-y-1.5 text-sm">
        {slices.map((s) => (
          <li key={s.key}>
            <button type="button" disabled={!onSelect || s.value === 0}
              onClick={() => onSelect?.(active === s.key ? null : s.key)}
              onFocus={() => setHover(s.key)} onBlur={() => setHover(null)}
              aria-pressed={active === s.key}
              className={`grid w-full grid-cols-[0.75rem_1fr_auto] items-center gap-2 rounded px-1.5 py-1 text-left enabled:hover:bg-sunken ${active === s.key ? "bg-sunken font-semibold" : ""}`}>
              <span className="h-3 w-3 rounded-sm" style={{ background: s.color }} aria-hidden />
              <span>{s.label}{s.detail && <span className="block text-xs font-normal text-muted">{s.detail}</span>}</span>
              <span className="num">{nf.format(s.value)} <span className="text-muted">{total ? `${Math.round((s.value / total) * 100)}%` : ""}</span></span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function arc(cx: number, cy: number, R: number, r: number, a0: number, a1: number): string {
  const big = a1 - a0 > Math.PI ? 1 : 0;
  const p = (rad: number, a: number) => `${cx + rad * Math.cos(a)},${cy + rad * Math.sin(a)}`;
  if (a1 - a0 >= Math.PI * 2 - 0.001) {  // full ring
    return `M${p(R, a0)} A${R},${R} 0 1 1 ${p(R, a0 + Math.PI)} A${R},${R} 0 1 1 ${p(R, a0)} M${p(r, a0)} A${r},${r} 0 1 0 ${p(r, a0 + Math.PI)} A${r},${r} 0 1 0 ${p(r, a0)} Z`;
  }
  return `M${p(R, a0)} A${R},${R} 0 ${big} 1 ${p(R, a1)} L${p(r, a1)} A${r},${r} 0 ${big} 0 ${p(r, a0)} Z`;
}

// ------------------------------------------------------------------ horizontal bars (clickable = drill-down)
export function Bars({ rows, active, onSelect, format = (v) => nf.format(v), sub }: {
  rows: { label: string; value: number; hint?: string }[]; active?: string | null; onSelect?: (label: string | null) => void;
  format?: (v: number) => string; sub?: (row: { label: string; value: number; hint?: string }) => string | null;
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  if (!rows.length) return <p className="py-6 text-center text-sm text-muted">Nothing in this slice.</p>;
  return (
    <ul className="space-y-1">
      {rows.map((r) => {
        const on = active === r.label;
        const body = (
          <>
            <span className="min-w-0">
              <span className="block truncate">{r.label}</span>
              {sub?.(r) && <span className="block truncate text-xs text-muted">{sub(r)}</span>}
            </span>
            <span className="relative h-2.5 self-center rounded-r-[4px]" aria-hidden>
              <span className={`absolute inset-y-0 left-0 rounded-r-[4px] ${active && !on ? "bg-chart-1/35" : "bg-chart-1"}`}
                style={{ width: `${Math.max(1.5, (r.value / max) * 100)}%` }} />
            </span>
            <span className="num min-w-[2.5rem] text-right">{format(r.value)}</span>
          </>
        );
        const cls = `grid w-full grid-cols-[minmax(0,5fr)_minmax(3rem,4fr)_auto] items-center gap-x-3 rounded px-1.5 py-1 text-left text-sm ${on ? "bg-sunken font-semibold" : ""}`;
        return (
          <li key={r.label} title={`${r.label}: ${format(r.value)}${r.hint ? ` (${r.hint})` : ""}`}>
            {onSelect ? (
              <button type="button" className={`${cls} hover:bg-sunken`} aria-pressed={on} onClick={() => onSelect(on ? null : r.label)}>{body}</button>
            ) : <div className={cls}>{body}</div>}
          </li>
        );
      })}
    </ul>
  );
}

// ------------------------------------------------------------------ funnel (ordinal, one hue)
export function Funnel({ steps }: { steps: { stage: string; count: number }[] }) {
  const max = Math.max(1, steps[0]?.count ?? 1);
  return (
    <ol className="space-y-2">
      {steps.map((s, i) => {
        const prev = i ? steps[i - 1].count : null;
        const conv = prev ? s.count / prev : null;
        return (
          <li key={s.stage} className="grid grid-cols-[7.5rem_1fr_auto] items-center gap-3 text-sm"
            title={`${s.stage}: ${nf.format(s.count)}${conv !== null ? `, ${(conv * 100).toFixed(1)}% of the step before` : ""}`}>
            <span>{s.stage}</span>
            <span className="relative h-5" aria-hidden>
              <span className="absolute inset-y-0 left-0 rounded-r-[4px] bg-chart-1" style={{ width: `${Math.max(0.6, (s.count / max) * 100)}%` }} />
            </span>
            <span className="num w-24 text-right">
              <b className="font-semibold">{nf.format(s.count)}</b>
              {conv !== null && <span className="ml-1.5 text-xs text-muted">{prev ? `${(conv * 100).toFixed(conv < 0.1 ? 1 : 0)}%` : "—"}</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

// ------------------------------------------------------------------ category columns
export function CategoryColumns({ rows, format = (v) => nf.format(v), highlight }: {
  rows: { label: string; value: number; sub?: string }[]; format?: (v: number) => string; highlight?: string;
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="grid items-start gap-[2px]" style={{ gridTemplateColumns: `repeat(${rows.length}, minmax(0, 1fr))` }}>
      {rows.map((r) => (
        <div key={r.label} className="flex flex-col items-center" title={`${r.label}: ${format(r.value)}${r.sub ? `, ${r.sub}` : ""}`}>
          <span className="num mb-1 text-sm font-semibold">{format(r.value)}</span>
          <div className="flex h-24 w-full items-end px-2">
            <div className={`w-full rounded-t-[4px] ${highlight === r.label ? "bg-hot" : "bg-chart-1"}`}
              style={{ height: r.value ? `${Math.max(4, (r.value / max) * 100)}%` : 2, opacity: r.value ? 1 : 0.3 }} />
          </div>
          <span className="mt-1.5 border-t border-line pt-1 text-center text-xs leading-tight text-muted">{r.label}</span>
          {r.sub && <span className="num text-center text-[11px] text-muted">{r.sub}</span>}
        </div>
      ))}
    </div>
  );
}
