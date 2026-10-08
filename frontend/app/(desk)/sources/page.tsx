"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { canEdit, useMe } from "@/components/Shell";
import { Button, Loading, Notice, Panel } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, inr } from "@/lib/format";

interface PortalOut {
  id: number; code: string; name: string; connector: string; acquisition_method: string; enabled: boolean;
  schedule_minutes: number; config: Record<string, unknown>; last_run_at: string | null; last_status: string | null;
  last_error: string | null; blocker: string | null;
}
interface CatalogOut {
  categories: Record<string, string>;
  capabilities: { id: string; name: string; category: string; offering: string; sub_capabilities: string[]; products: string[] }[];
  products: { id: string; name: string; oem: string }[];
}

const METHOD: Record<string, string> = {
  API: "Official API", FEED: "Official feed", HTML: "Public web pages", BROWSER: "Browser automation",
  BROWSER_AUTH: "Authenticated browser", MANUAL: "Entered by analysts",
};

export default function SourcesPage() {
  const me = useMe();
  const router = useRouter();
  const [portals, setPortals] = useState<PortalOut[] | null>(null);
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null);
  const [cat, setCat] = useState<CatalogOut | null>(null);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  const load = () => Promise.all([api<PortalOut[]>("/portals"), api<Record<string, unknown>>("/settings"), api<CatalogOut>("/catalog")])
    .then(([p, s, c]) => { setPortals(p); setCfg(s); setCat(c); }).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  async function run(p: PortalOut) {
    try {
      const r = await api<{ queued: boolean }>(`/portals/${p.id}/run`, { method: "POST" });
      setMsg(r.queued ? `Discovery queued for ${p.name}.` : `${p.name} is already being checked.`);
    } catch (e) { setErr((e as Error).message); }
  }
  async function toggle(p: PortalOut) {
    await api(`/portals/${p.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !p.enabled }) }).catch((e) => setErr(e.message));
    load();
  }
  async function addTender(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const value = Number(f.get("value_lakh") || 0);
    try {
      const r = await api<{ id: number }>("/tenders", {
        method: "POST",
        body: JSON.stringify({
          title: f.get("title"), organization: f.get("organization") || null, reference_number: f.get("reference") || null,
          closing_at: f.get("closing") ? new Date(String(f.get("closing"))).toISOString() : null,
          tender_value_inr: value ? Math.round(value * 100000) : null, source_url: f.get("source_url") || null,
        }),
      });
      router.push(`/tenders/${r.id}`);
    } catch (e) { setErr((e as Error).message); }
  }

  if (!portals || !cfg || !cat) return err ? <Notice tone="error">{err}</Notice> : <Loading />;
  const isAdmin = me?.role === "admin";
  const c = cfg as Record<string, number | boolean | string>;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="font-serif text-3xl font-semibold tracking-tight">Sources and rules</h1>
        <p className="mt-1 max-w-2xl text-muted">Where tenders come from, the commercial rules applied to them, and the catalog they are matched against.</p>
      </div>
      {msg && <Notice>{msg}</Notice>}
      {err && <Notice tone="error">{err}</Notice>}

      <Panel title="Tender sources">
        <ul className="divide-y divide-line">
          {portals.map((p) => (
            <li key={p.id} className="py-3 first:pt-0 last:pb-0">
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <div>
                  <p className="font-semibold">{p.name}</p>
                  <p className="text-sm text-muted">
                    {METHOD[p.acquisition_method] ?? p.acquisition_method}
                    {p.schedule_minutes > 0 && `, checked every ${p.schedule_minutes} minutes`}
                    {p.last_run_at && `, last run ${dateTime(p.last_run_at)} (${p.last_status?.toLowerCase()})`}
                    {!p.enabled && ", paused"}
                  </p>
                </div>
                {isAdmin && p.connector !== "manual" && (
                  <div className="flex gap-2">
                    <Button variant="quiet" onClick={() => toggle(p)}>{p.enabled ? "Pause" : "Resume"}</Button>
                    <Button onClick={() => run(p)} disabled={!p.enabled}>Check now</Button>
                  </div>
                )}
              </div>
              {p.blocker && <p className="mt-1.5 text-sm">{p.blocker}</p>}
              {p.last_error && <p className="mt-1 whitespace-pre-line text-sm text-high">{p.last_error}</p>}
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="Commercial and relevance rules">
        <dl className="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
          <Rule label="Service tenders" value={c.SERVICE_VALUE_RULE_ENABLED ? `Rejected above ${inr(Number(c.SERVICE_MAX_VALUE_INR))}` : "No value limit"} />
          <Rule label="Service tenders without a stated value" value={String(c.SERVICE_UNKNOWN_VALUE_ACTION).replace("_", " ").toLowerCase()} />
          <Rule label="OEM / product tenders" value={c.OEM_VALUE_LIMIT_ENABLED ? `Limited to ${inr(Number(c.OEM_MAX_VALUE_INR))}` : "No value limit"} />
          <Rule label="Hybrid tenders with unclear split" value={c.HYBRID_REVIEW_ENABLED ? "Sent to review" : "Accepted"} />
          <Rule label="Hybrid service component over the limit" value={String(c.HYBRID_SERVICE_OVER_CAP_ACTION).replace("_", " ").toLowerCase()} />
          <Rule label="Adjacent-only matches" value={c.ADJACENT_MATCH_ACTION === "SUPPRESS" ? "Suppressed" : "Sent to review"} />
          <Rule label="Priority thresholds" value={`Hot ≥ ${c.HOT_SCORE_THRESHOLD}, high ≥ ${c.HIGH_SCORE_THRESHOLD}, medium ≥ ${c.MEDIUM_SCORE_THRESHOLD}, minimum ${c.MIN_RELEVANCE_SCORE}`} />
          <Rule label="Score weights" value={`Capability ${c.WEIGHT_CAPABILITY}, technical ${c.WEIGHT_TECHNICAL}, OEM ${c.WEIGHT_OEM}, commercial ${c.WEIGHT_COMMERCIAL}, eligibility ${c.WEIGHT_ELIGIBILITY}, timeline ${c.WEIGHT_TIMELINE}, strategic ${c.WEIGHT_STRATEGIC}`} />
          <Rule label="Email alerts" value={`Hot: immediately. High: ${c.HIGH_ALERT_MODE === "digest" ? "daily digest" : "immediately"}. Medium: daily digest at ${c.DIGEST_HOUR_IST}:00 IST.`} />
          <Rule label="Analysis model" value={`${c.LLM_MODEL} (effort ${c.LLM_EFFORT})`} />
        </dl>
        <p className="mt-4 text-xs text-muted">Rules are set through environment configuration and apply to every new analysis.</p>
      </Panel>

      {canEdit(me) && (
        <Panel title="Add a tender by hand">
          <p className="mb-4 text-sm text-muted">For tenders from portals without a connector, partner emails or newspapers. It goes through the same analysis.</p>
          <form onSubmit={addTender} className="grid gap-3 text-sm sm:grid-cols-2">
            <Field label="Title" name="title" required className="sm:col-span-2" />
            <Field label="Organisation" name="organization" />
            <Field label="Reference number" name="reference" />
            <Field label="Bid closing" name="closing" type="datetime-local" />
            <Field label="Value (₹ lakh)" name="value_lakh" type="number" step="0.01" min="0" />
            <Field label="Source link" name="source_url" type="url" className="sm:col-span-2" />
            <div><Button type="submit">Add and analyse</Button></div>
          </form>
        </Panel>
      )}

      <Panel title="Capability catalog" aside={<span className="text-sm text-muted">{cat.capabilities.length} capabilities, {cat.products.length} products</span>}>
        <div className="space-y-5">
          {Object.entries(cat.categories).map(([code, name]) => {
            const caps = cat.capabilities.filter((x) => x.category === code);
            const names = new Map(cat.products.map((p) => [p.id, p.name]));
            return (
              <div key={code}>
                <h3 className="font-semibold">{name}</h3>
                <ul className="mt-1 space-y-1 text-sm">
                  {caps.map((x) => (
                    <li key={x.id}>
                      {x.name} <span className="text-muted">({x.offering === "SERVICE" ? "service" : "product"})</span>
                      {x.products.length > 0 && <span className="text-muted">: {x.products.map((p) => names.get(p)).join(", ")}</span>}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      </Panel>
    </div>
  );
}

function Rule({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-muted">{label}</dt><dd className="font-medium">{value}</dd></div>;
}

function Field({ label, className = "", ...props }: React.InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  return (
    <label className={`block ${className}`}>
      <span className="text-muted">{label}</span>
      <input {...props} className="mt-1 w-full rounded-md border border-line bg-surface px-2.5 py-1.5" />
    </label>
  );
}
