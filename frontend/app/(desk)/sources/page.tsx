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

      <Panel title="Active tender sources" aside={<span className="text-sm text-muted">{portals.filter((p) => p.enabled).length} of {portals.length} on</span>}>
        <ul className="divide-y divide-line">
          {portals.filter((p) => p.enabled).map((p) => (
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
                    <Button variant="quiet" onClick={() => toggle(p)}>Turn off</Button>
                    <Button onClick={() => run(p)} disabled={!p.enabled}>Check now</Button>
                  </div>
                )}
              </div>
              {p.blocker && <p className="mt-1.5 text-sm">{p.blocker}</p>}
              {p.last_error && <p className="mt-1 line-clamp-3 whitespace-pre-line text-sm text-high">{p.last_error}</p>}
            </li>
          ))}
        </ul>
      </Panel>

      {portals.some((p) => !p.enabled) && (
        <Panel title="Available portals, switched off">
          <p className="mb-3 text-sm text-muted">
            State and sector eProcurement portals on the same NIC platform. Turn on the ones where your team bids;
            each adds roughly one request per organisation on the portal every two hours.
          </p>
          <ul className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
            {portals.filter((p) => !p.enabled).map((p) => (
              <li key={p.id} className="flex items-baseline justify-between gap-3">
                <span>{p.name}</span>
                {isAdmin && (
                  <button onClick={() => toggle(p)} className="shrink-0 text-teal hover:underline">Turn on</button>
                )}
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {isAdmin && <BuyerPageForm onSaved={(name) => { setMsg(`${name} added. It will be checked on its schedule; use “Check now” to run it immediately.`); load(); }} />}

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

interface PreviewOut {
  open_tenders: number;
  blockers: string[];
  errors: string[];
  items: { title: string; reference: string | null; closing_at: string | null; documents: number; detail_url: string | null }[];
}

function BuyerPageForm({ onSaved }: { onSaved: (name: string) => void }) {
  const [form, setForm] = useState({ name: "", url: "", organization: "", render: "http", follow_detail: false, screen: "title" });
  const [preview, setPreview] = useState<PreviewOut | null>(null);
  const [permission, setPermission] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const set = (k: string, v: string | boolean) => { setForm({ ...form, [k]: v }); setPreview(null); };

  async function runPreview(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(""); setPreview(null);
    try {
      setPreview(await api<PreviewOut>("/portals/preview", { method: "POST", body: JSON.stringify(form) }));
    } catch (e2) { setErr((e2 as Error).message); } finally { setBusy(false); }
  }

  async function save() {
    setBusy(true); setErr("");
    try {
      await api("/portals", { method: "POST", body: JSON.stringify({ ...form, permission_confirmed: permission, permission_note: note }) });
      onSaved(form.name);
      setForm({ name: "", url: "", organization: "", render: "http", follow_detail: false, screen: "title" });
      setPreview(null); setPermission(false); setNote("");
    } catch (e2) { setErr((e2 as Error).message); } finally { setBusy(false); }
  }

  return (
    <Panel title="Add a buyer’s tender page">
      <p className="mb-4 max-w-3xl text-sm text-muted">
        Many banks, regulators and agencies publish tenders on their own websites. Add the page that lists them; the
        system reads the table, downloads linked tender documents for likely matches, and analyses them like any other tender.
      </p>
      <form onSubmit={runPreview} className="grid gap-3 text-sm sm:grid-cols-2">
        <label className="block"><span className="text-muted">Source name</span>
          <input required minLength={3} value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="e.g. Canara Bank tenders"
            className="mt-1 w-full rounded-md border border-line bg-surface px-2.5 py-1.5" /></label>
        <label className="block"><span className="text-muted">Organisation</span>
          <input required minLength={2} value={form.organization} onChange={(e) => set("organization", e.target.value)}
            className="mt-1 w-full rounded-md border border-line bg-surface px-2.5 py-1.5" /></label>
        <label className="block sm:col-span-2"><span className="text-muted">Tender page address</span>
          <input required type="url" value={form.url} onChange={(e) => set("url", e.target.value)} placeholder="https://"
            className="mt-1 w-full rounded-md border border-line bg-surface px-2.5 py-1.5" /></label>
        <label className="block"><span className="text-muted">How the page is built</span>
          <select value={form.render} onChange={(e) => set("render", e.target.value)} className="mt-1 w-full rounded-md border border-line bg-surface px-2 py-1.5">
            <option value="http">Ordinary web page</option>
            <option value="browser">Built by JavaScript (read with a headless browser)</option>
          </select></label>
        <label className="block"><span className="text-muted">Which tenders to read documents for</span>
          <select value={form.screen} onChange={(e) => set("screen", e.target.value)} className="mt-1 w-full rounded-md border border-line bg-surface px-2 py-1.5">
            <option value="title">Only titles that look relevant</option>
            <option value="documents">All new tenders (titles carry no subject)</option>
          </select></label>
        <label className="flex items-center gap-2 sm:col-span-2">
          <input type="checkbox" checked={form.follow_detail} onChange={(e) => set("follow_detail", e.target.checked)} />
          Tender documents are on a separate page per tender (open each listed tender to find them)
        </label>
        <div className="sm:col-span-2"><Button type="submit" variant="quiet" disabled={busy}>{busy && !preview ? "Reading the page…" : "Preview"}</Button></div>
      </form>

      {err && <p className="mt-3 text-sm text-hot" role="alert">{err}</p>}

      {preview && (
        <div className="mt-5 border-t border-line pt-4">
          {preview.blockers.concat(preview.errors).map((m, i) => <p key={i} className="text-sm text-high">{m}</p>)}
          <p className="text-sm"><b>{preview.open_tenders}</b> open tender{preview.open_tenders === 1 ? "" : "s"} found{preview.items.length < preview.open_tenders ? `; first ${preview.items.length} shown` : ""}.</p>
          {preview.items.length > 0 && (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[36rem] text-sm">
                <thead className="text-left text-muted"><tr><th className="py-1 pr-3 font-medium">Title</th><th className="py-1 pr-3 font-medium">Reference</th><th className="py-1 pr-3 font-medium">Closes</th><th className="py-1 text-right font-medium">Documents</th></tr></thead>
                <tbody className="divide-y divide-line">
                  {preview.items.map((it, i) => (
                    <tr key={i} className="align-top"><td className="py-1.5 pr-3">{it.title}</td><td className="py-1.5 pr-3 text-muted">{it.reference ?? "—"}</td>
                      <td className="whitespace-nowrap py-1.5 pr-3">{dateTime(it.closing_at)}</td><td className="num py-1.5 text-right">{it.documents || (it.detail_url ? "on detail page" : 0)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {preview.open_tenders > 0 && (
            <div className="mt-4 space-y-3 text-sm">
              <label className="flex items-start gap-2">
                <input type="checkbox" className="mt-1" checked={permission} onChange={(e) => setPermission(e.target.checked)} />
                <span>I have checked that this site&apos;s terms allow automated reading of this page. (robots.txt was checked and allows it.)</span>
              </label>
              <label className="block"><span className="text-muted">How you confirmed it (kept in the audit log)</span>
                <input value={note} onChange={(e) => setNote(e.target.value)} minLength={10} placeholder="e.g. Terms of use reviewed on 8 Oct 2026; no restriction on automated access"
                  className="mt-1 w-full rounded-md border border-line bg-surface px-2.5 py-1.5" /></label>
              <Button onClick={save} disabled={busy || !permission || note.trim().length < 10}>Add source</Button>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}
