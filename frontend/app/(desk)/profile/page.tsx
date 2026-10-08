"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useMe } from "@/components/Shell";
import { Button, Loading, Notice, Panel } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, inr } from "@/lib/format";

type Value = number | boolean | string | string[] | null;
interface ProfileOut {
  data: Record<string, Value>;
  updated_by: string | null;
  updated_at: string | null;
  fields: { key: string; label: string; type: "number" | "list" | "text" | "bool" }[];
  products: { id: string; name: string }[];
}

const MONEY = new Set(["average_turnover_inr", "net_worth_inr", "largest_similar_order_inr"]);

export default function ProfilePage() {
  const me = useMe();
  const [p, setP] = useState<ProfileOut | null>(null);
  const [form, setForm] = useState<Record<string, Value>>({});
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const editable = me?.role === "admin";

  useEffect(() => {
    api<ProfileOut>("/company-profile").then((r) => { setP(r); setForm(r.data ?? {}); }).catch((e) => setErr(e.message));
  }, []);

  if (!p) return err ? <Notice tone="error">{err}</Notice> : <Loading />;

  const set = (k: string, v: Value) => setForm({ ...form, [k]: v });
  const filled = p.fields.filter((f) => form[f.key] !== null && form[f.key] !== undefined && form[f.key] !== "").length;

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(""); setMsg("");
    try {
      const r = await api<{ changed: string[]; requeued: number }>("/company-profile", { method: "PUT", body: JSON.stringify(form) });
      setMsg(r.changed.length ? `Saved. ${r.requeued} open tender${r.requeued === 1 ? "" : "s"} will be re-scored.` : "No changes.");
    } catch (e2) { setErr((e2 as Error).message); } finally { setBusy(false); }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="font-serif text-3xl font-semibold tracking-tight">Company profile</h1>
        <p className="mt-1 text-muted">
          The facts tenders check before you can bid: turnover, experience, certifications, empanelments. Each tender&apos;s
          eligibility criteria are compared with these. Empty fields count as unknown and are never assumed to be met.
        </p>
        <p className="mt-2 text-sm text-muted">
          {filled} of {p.fields.length} filled{p.updated_by ? `, last updated ${dateTime(p.updated_at)} by ${p.updated_by}` : ""}.
        </p>
      </div>
      {msg && <Notice>{msg}</Notice>}
      {err && <Notice tone="error">{err}</Notice>}
      {!editable && <Notice>Only administrators can change the company profile.</Notice>}

      <form onSubmit={save} className="space-y-6">
        <Panel title="Financials and experience">
          <div className="grid gap-4 sm:grid-cols-2">
            {p.fields.filter((f) => f.type === "number").map((f) => (
              <label key={f.key} className="block text-sm">
                <span className="text-muted">{f.label}</span>
                <input type="number" min={0} disabled={!editable} value={form[f.key] === null || form[f.key] === undefined ? "" : String(form[f.key])}
                  onChange={(e) => set(f.key, e.target.value === "" ? null : Number(e.target.value))}
                  className="mt-1 w-full rounded-md border border-line bg-surface px-2.5 py-1.5 disabled:opacity-70" />
                {MONEY.has(f.key) && typeof form[f.key] === "number" && <span className="text-xs text-muted">{inr(form[f.key] as number)}</span>}
              </label>
            ))}
          </div>
        </Panel>

        <Panel title="Registrations and certifications">
          <div className="grid gap-4 sm:grid-cols-2">
            {p.fields.filter((f) => f.type === "list" && f.key !== "oem_authorisations").map((f) => (
              <label key={f.key} className="block text-sm sm:col-span-2">
                <span className="text-muted">{f.label}, separated by commas</span>
                <input disabled={!editable} value={Array.isArray(form[f.key]) ? (form[f.key] as string[]).join(", ") : ""}
                  onChange={(e) => set(f.key, e.target.value.trim() === "" ? null : e.target.value.split(",").map((x) => x.trim()).filter(Boolean))}
                  className="mt-1 w-full rounded-md border border-line bg-surface px-2.5 py-1.5 disabled:opacity-70" />
              </label>
            ))}
            <label className="block text-sm">
              <span className="text-muted">Make in India local supplier class</span>
              <select disabled={!editable} value={(form.local_supplier_class as string) ?? ""} onChange={(e) => set("local_supplier_class", e.target.value || null)}
                className="mt-1 w-full rounded-md border border-line bg-surface px-2 py-1.5 disabled:opacity-70">
                <option value="">Unknown</option><option>Class-I</option><option>Class-II</option><option>Non-local</option>
              </select>
            </label>
            {p.fields.filter((f) => f.type === "bool").map((f) => (
              <label key={f.key} className="block text-sm">
                <span className="text-muted">{f.label}</span>
                <select disabled={!editable} value={form[f.key] === true ? "yes" : form[f.key] === false ? "no" : ""}
                  onChange={(e) => set(f.key, e.target.value === "" ? null : e.target.value === "yes")}
                  className="mt-1 w-full rounded-md border border-line bg-surface px-2 py-1.5 disabled:opacity-70">
                  <option value="">Unknown</option><option value="yes">Yes</option><option value="no">No</option>
                </select>
              </label>
            ))}
          </div>
        </Panel>

        <Panel title="OEM authorisations" aside={<span className="text-sm text-muted">{Array.isArray(form.oem_authorisations) ? (form.oem_authorisations as string[]).length : 0} selected</span>}>
          <p className="mb-3 text-sm text-muted">Products whose OEM will issue a manufacturer&apos;s authorisation (MAF) for your bids.</p>
          <div className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
            {p.products.map((prod) => {
              const list = Array.isArray(form.oem_authorisations) ? (form.oem_authorisations as string[]) : [];
              return (
                <label key={prod.id} className="flex items-center gap-2">
                  <input type="checkbox" disabled={!editable} checked={list.includes(prod.id)}
                    onChange={(e) => set("oem_authorisations", e.target.checked ? [...list, prod.id] : list.filter((x) => x !== prod.id))} />
                  {prod.name}
                </label>
              );
            })}
          </div>
        </Panel>

        {editable && <Button type="submit" disabled={busy}>{busy ? "Saving…" : "Save profile"}</Button>}
      </form>
    </div>
  );
}
