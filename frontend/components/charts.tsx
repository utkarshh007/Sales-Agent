"use client";

import { useState } from "react";

/**
 * Small, dependency-free charts following the dataviz method: one validated hue (--chart-1) for
 * single-series magnitude, thin marks with 4px rounded data ends, 2px surface gaps, recessive axes,
 * values always reachable without hover (labels / table), and hover + keyboard-focus tooltips.
 */

export function BarList({ rows, unit = "", total }: {
  rows: { label: string; count: number; hint?: string }[]; unit?: string; total?: number;
}) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  if (rows.length === 0) return <p className="text-sm text-muted">Nothing to show yet.</p>;
  return (
    <ul className="space-y-2.5" role="list">
      {rows.map((r) => {
        const share = total ? ` (${Math.round((r.count / total) * 100)}%)` : "";
        return (
          <li key={r.label} className="grid grid-cols-[minmax(0,13rem)_1fr_auto] items-center gap-x-3 text-sm"
            title={`${r.label}: ${r.count.toLocaleString("en-IN")}${unit}${share}${r.hint ? ` — ${r.hint}` : ""}`}>
            <span className="truncate">{r.label}</span>
            <span className="relative h-2.5 rounded-r-[4px] bg-sunken" aria-hidden>
              <span className="absolute inset-y-0 left-0 rounded-r-[4px] bg-chart-1" style={{ width: `${Math.max(1.5, (r.count / max) * 100)}%` }} />
            </span>
            <span className="num w-16 text-right text-muted">{r.count.toLocaleString("en-IN")}{unit}</span>
          </li>
        );
      })}
    </ul>
  );
}

export function Columns({ data, label, formatX }: {
  data: { x: string; y: number }[]; label: string; formatX: (x: string) => string;
}) {
  const [active, setActive] = useState<number | null>(null);
  const max = Math.max(1, ...data.map((d) => d.y));
  const plotH = 96;
  return (
    <figure className="min-w-0">
      <figcaption className="mb-2 text-sm font-medium">{label}</figcaption>
      <div className="relative">
        <div className="flex items-end gap-[2px] border-b border-line" style={{ height: plotH }}
          role="img" aria-label={`${label}: ${data.map((d) => `${formatX(d.x)} ${d.y}`).join(", ")}`}>
          {data.map((d, i) => (
            <button key={d.x} type="button" className="group relative flex h-full flex-1 items-end focus:outline-none"
              onMouseEnter={() => setActive(i)} onMouseLeave={() => setActive(null)} onFocus={() => setActive(i)} onBlur={() => setActive(null)}
              aria-label={`${formatX(d.x)}: ${d.y}`}>
              <span className={`w-full rounded-t-[4px] ${active === i ? "bg-teal" : "bg-chart-1"}`}
                style={{ height: d.y ? `${Math.max(3, (d.y / max) * 100)}%` : 0 }} />
            </button>
          ))}
        </div>
        {active !== null && (
          <div className="pointer-events-none absolute -top-2 z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-md border border-line bg-surface px-2 py-1 text-xs shadow-sm"
            style={{ left: `${((active + 0.5) / data.length) * 100}%` }}>
            <span className="text-muted">Week of {formatX(data[active].x)}: </span><span className="num font-semibold">{data[active].y.toLocaleString("en-IN")}</span>
          </div>
        )}
        <div className="mt-1 flex justify-between text-[11px] text-muted">
          <span>{data.length ? formatX(data[0].x) : ""}</span>
          <span className="num">max {max.toLocaleString("en-IN")}</span>
          <span>{data.length ? formatX(data[data.length - 1].x) : ""}</span>
        </div>
      </div>
    </figure>
  );
}

export function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-md border border-line bg-surface px-4 py-3">
      <p className="text-sm text-muted">{label}</p>
      <p className="num mt-0.5 text-3xl font-semibold tracking-tight">{value}</p>
      {note && <p className="mt-0.5 text-sm text-muted">{note}</p>}
    </div>
  );
}
