"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { canEdit, useMe } from "@/components/Shell";
import { Button, DecisionTag, Loading, MatchType, Notice, Panel, ScoreBar, ScoreMark } from "@/components/ui";
import { api } from "@/lib/api";
import { closesIn, date, dateTime, inr, TYPE_LABEL } from "@/lib/format";
import { SCORE_LABEL, SCORE_ORDER, type TenderDetail } from "@/lib/types";

const FIELD_LABEL: Record<string, string> = {
  tender_title: "Title", tender_reference: "Reference", procuring_organization: "Procuring organisation", department: "Department",
  tender_type: "Tender type", publication_date: "Published", closing_date: "Bid closing", tender_value: "Tender value", emd: "EMD",
  estimated_contract_value: "Estimated contract value", contract_duration: "Contract duration", location: "Location",
  eligibility_criteria: "Eligibility criteria", technical_requirements: "Technical requirements", scope_of_work: "Scope of work",
  required_products: "Required products", required_oems: "Required OEMs", required_certifications: "Required certifications",
  required_manpower: "Required manpower", experience_requirements: "Experience", turnover_requirements: "Turnover",
  security_requirements: "Security requirements", implementation_requirements: "Implementation", support_amc_requirements: "Support / AMC",
  service_components: "Service components", product_oem_components: "Product / OEM components", important_deadlines: "Important deadlines",
  mandatory_documents: "Mandatory documents",
};

export default function TenderView() {
  const { id } = useParams<{ id: string }>();
  const me = useMe();
  const [t, setT] = useState<TenderDetail | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [showUnknown, setShowUnknown] = useState(false);

  const load = useCallback(() => {
    api<TenderDetail>(`/tenders/${id}`).then(setT).catch((e) => setError(e.message));
  }, [id]);
  useEffect(load, [load]);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!t) return <Loading />;

  const editable = canEdit(me);
  const fields = Object.entries(t.extracted ?? {}).filter(([k]) => !k.startsWith("_")) as [string, { value: string; confidence: number; evidence: string; source: string }][];
  const known = fields.filter(([, f]) => f.value && f.value !== "UNKNOWN");
  const unknown = fields.filter(([, f]) => !f.value || f.value === "UNKNOWN");
  const parts = t.score_breakdown
    ? Object.fromEntries(Object.entries(t.score_breakdown).map(([k, v]) => [k, [v.points, v.max] as [number | null, number]]))
    : null;
  const openReviews = t.reviews.filter((r) => r.status === "OPEN");
  const va = t.value_analysis ?? {};

  async function reanalyze() {
    await api(`/tenders/${t!.id}/reanalyze`, { method: "POST" });
    setMessage("Re-analysis queued. Refresh in a minute to see the result.");
  }

  return (
    <div className="mx-auto max-w-[84rem]">
      <Link href="/tenders" className="text-sm text-teal hover:underline">Back to tenders</Link>

      <header className="mt-3 grid gap-6 border-b border-line pb-6 lg:grid-cols-[1fr_auto]">
        <div className="min-w-0">
          <h1 className="font-serif text-[28px] font-semibold leading-tight tracking-tight">{t.title}</h1>
          <p className="mt-2 text-muted">
            {t.organization ?? t.location ?? "Unknown organisation"}
            {t.reference_number && <>, ref. <span className="text-ink">{t.reference_number}</span></>}
            {t.portal_tender_id && <>, tender ID <span className="text-ink">{t.portal_tender_id}</span></>}
          </p>
          {t.sources.length > 1 && (
            <p className="mt-2 text-sm">Listed on {t.sources.length} portals: {t.sources.map((s) => s.portal_name).join(", ")}.</p>
          )}
        </div>
        <div className="flex items-start gap-6 lg:flex-col lg:items-end lg:gap-2">
          <ScoreMark score={t.score} priority={t.priority} size="lg" />
          <div className="flex items-center gap-3">
            <DecisionTag decision={t.decision} />
            <span className="text-sm">{TYPE_LABEL[t.opportunity_type]}</span>
          </div>
          <p className="text-sm text-muted">{closesIn(t.closing_at)}</p>
        </div>
      </header>

      <div className="mt-6 space-y-3">
        {t.decision === "REJECTED" && t.rejection_reason && <Notice tone="error"><b>Rejected.</b> {t.rejection_reason}</Notice>}
        {t.documents_status === "BLOCKED_HUMAN_REQUIRED" && t.decision !== "REJECTED" && (
          <Notice tone="warn">
            <b>Documents needed.</b> {t.blocker_note}{" "}
            {t.portal_text ? "The assessment below uses the portal’s published tender details." : "The assessment below is based on the listing only."}
          </Notice>
        )}
        {message && <Notice>{message}</Notice>}
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-6">
          <Panel title="Why this tender">
            <p className="leading-relaxed">{t.relevance_reason ?? "No capability match was recorded."}</p>
            {t.summary && <p className="mt-3 leading-relaxed text-muted">{t.summary}</p>}
            {t.recommendation && (
              <p className="mt-3 border-l-[3px] border-teal pl-3 leading-relaxed"><b>Recommendation.</b> {t.recommendation}</p>
            )}
            <p className="mt-4 text-xs text-muted">
              Analysed {dateTime(t.last_analyzed_at)} using {t.analysis_mode === "LLM" ? `the LLM (${t.model_version})` : t.analysis_mode === "RULES_ONLY" ? "deterministic rules only" : "deterministic pre-filters"}, version {t.version}.
            </p>
          </Panel>

          {t.score_breakdown && (
            <Panel title="Score breakdown" aside={<span className="num text-sm text-muted">{t.score} of 100</span>}>
              <ScoreBar parts={parts} height={14} showLegend />
              <table className="mt-5 w-full text-sm">
                <tbody className="divide-y divide-line">
                  {SCORE_ORDER.filter((k) => t.score_breakdown![k]).map((k) => {
                    const c = t.score_breakdown![k];
                    return (
                      <tr key={k} className="align-top">
                        <th className="w-44 py-2 pr-4 text-left font-medium">{SCORE_LABEL[k]}</th>
                        <td className="num w-24 whitespace-nowrap py-2 pr-4 text-right">{c.applicable ? `${c.points} / ${c.max}` : "n/a"}</td>
                        <td className="py-2 text-muted">{c.reason}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </Panel>
          )}

          {t.portal_text && (
            <Panel title="Published on the portal" aside={<span className="text-sm text-muted">from the tender&apos;s detail page</span>}>
              <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[11rem_1fr]">
                {t.portal_text.split("\n").map((line, i) => {
                  const at = line.indexOf(": ");
                  return at < 0 ? <dd key={i} className="sm:col-span-2">{line}</dd> : (
                    <div key={i} className="contents">
                      <dt className="text-muted">{line.slice(0, at)}</dt>
                      <dd className="whitespace-pre-line">{line.slice(at + 2)}</dd>
                    </div>
                  );
                })}
              </dl>
            </Panel>
          )}

          <Panel title="Capability and OEM matches">
            {t.matches.length === 0 ? <p className="text-muted">No requirement maps to the capability catalog.</p> : (
              <ol className="space-y-5">
                {t.matches.map((m) => (
                  <li key={m.capability_id}>
                    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                      <span className="font-semibold">{m.capability_name}</span>
                      <MatchType type={m.match_type} />
                      <span className="num text-sm text-muted">confidence {m.confidence}</span>
                    </div>
                    {m.sub_capability && <p className="text-sm text-muted">{m.sub_capability}</p>}
                    {m.evidence && <blockquote className="mt-2 border-l-2 border-line pl-3 font-serif text-[15px] italic text-muted">{m.evidence}</blockquote>}
                    {m.explanation && <p className="mt-2 text-sm">{m.explanation}</p>}
                    {m.products.length > 0 && (
                      <p className="mt-2 text-sm">
                        <span className="text-muted">{m.explicit_oem ? "Named in tender: " : "Supported portfolio: "}</span>
                        {m.products.map((p, i) => <span key={p.id}>{i > 0 && ", "}{p.name}</span>)}
                      </p>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </Panel>

          <Panel title="Value analysis">
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              {[["Tender value", va.total_value_inr], ["Service component", va.service_value_inr], ["Product component", va.product_value_inr], ["EMD", va.emd_inr ?? t.emd_inr]].map(([l, v]) => (
                <div key={l as string}><dt className="text-sm text-muted">{l}</dt><dd className="num text-xl font-semibold">{inr(v as number | null)}</dd></div>
              ))}
            </dl>
            {(va.deterministic_evidence || va.llm_evidence) && (
              <div className="mt-4 space-y-1 text-sm text-muted">
                {va.deterministic_evidence && <p>{va.deterministic_evidence === "portal listing field" ? "Stated on the portal listing." : <>Parsed from documents: “{va.deterministic_evidence}”</>}</p>}
                {va.llm_evidence && <p>Read by the LLM: “{va.llm_evidence}”</p>}
              </div>
            )}
            {va.components && va.components.length > 0 && (
              <table className="mt-4 w-full text-sm">
                <thead className="text-left text-muted"><tr><th className="py-1 font-medium">Component</th><th className="py-1 font-medium">Kind</th><th className="py-1 text-right font-medium">Value</th></tr></thead>
                <tbody className="divide-y divide-line">
                  {va.components.map((c, i) => (
                    <tr key={i}><td className="py-1.5 pr-3">{c.description}</td><td className="py-1.5 pr-3">{c.kind === "PRODUCT" ? "Product / OEM" : "Service"}</td><td className="num py-1.5 text-right">{inr(c.value_inr)}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>

          <Panel title="Extracted requirements" aside={unknown.length > 0 && (
            <button className="text-sm text-teal hover:underline" onClick={() => setShowUnknown(!showUnknown)}>
              {showUnknown ? "Hide" : "Show"} {unknown.length} not found
            </button>)}>
            {known.length === 0 && !showUnknown ? <p className="text-muted">Nothing extracted yet.</p> : (
              <table className="w-full text-sm">
                <tbody className="divide-y divide-line">
                  {[...known, ...(showUnknown ? unknown : [])].map(([k, f]) => (
                    <tr key={k} className="align-top">
                      <th className="w-48 py-2 pr-4 text-left font-medium">{FIELD_LABEL[k] ?? k}</th>
                      <td className="py-2">
                        <p className={`whitespace-pre-line ${f.value === "UNKNOWN" ? "text-muted" : ""}`}>{f.value === "UNKNOWN" ? "Not stated" : /^\d{4}-\d\d-\d\dT/.test(f.value) ? dateTime(f.value) : f.value}</p>
                        {f.evidence && <p className="mt-1 text-xs text-muted">Evidence: {f.evidence}</p>}
                      </td>
                      <td className="num w-16 py-2 text-right text-muted" title="Confidence">{f.value === "UNKNOWN" ? "" : f.confidence}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {(t.extracted?._eligibility?.issues?.length || t.extracted?._risks?.length) ? (
              <div className="mt-5 border-t border-line pt-4">
                <h3 className="font-semibold">Risks and eligibility issues</h3>
                <p className="text-sm text-muted">Eligibility assessed as {t.extracted._eligibility?.assessment?.toLowerCase() ?? "unknown"}.</p>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
                  {[...(t.extracted._eligibility?.issues ?? []), ...(t.extracted._risks ?? [])].map((x, i) => <li key={i}>{x}</li>)}
                </ul>
              </div>
            ) : null}
          </Panel>
        </div>

        <aside className="min-w-0 space-y-6">
          <Panel title="Dates">
            <dl className="space-y-2 text-sm">
              <div><dt className="text-muted">Published</dt><dd>{dateTime(t.published_at)}</dd></div>
              <div><dt className="text-muted">Bid submission closes</dt><dd className="font-semibold">{dateTime(t.closing_at)}</dd></div>
              <div><dt className="text-muted">Bids open</dt><dd>{dateTime(t.opening_at)}</dd></div>
              <div><dt className="text-muted">First discovered</dt><dd>{dateTime(t.first_seen_at)}</dd></div>
              {t.corrigendum && <div><dt className="text-muted">Corrigendum</dt><dd>{t.corrigendum}</dd></div>}
            </dl>
          </Panel>

          <SourcesPanel t={t} />

          {openReviews.length > 0 && <ReviewPanel reviews={openReviews} editable={editable} onDone={load} />}

          <DocumentsPanel t={t} editable={editable} onUploaded={() => { setMessage("Document uploaded. It will be processed and the tender re-analysed shortly."); load(); }} />

          {editable && (
            <div><Button variant="quiet" onClick={reanalyze}>Re-run analysis</Button></div>
          )}

          <Panel title="History">
            <ol className="space-y-3 text-sm">
              {t.audit.slice(0, 15).map((a, i) => (
                <li key={i}>
                  <p><span className="font-medium">{a.action.replaceAll("_", " ").toLowerCase()}</span>{a.decision && <>: {a.decision.toLowerCase().replace("_", " ")}</>}</p>
                  <p className="text-muted">{dateTime(a.created_at)}, {a.actor}</p>
                  {a.reason && <p className="mt-0.5 text-muted">{a.reason}</p>}
                </li>
              ))}
            </ol>
            {t.alerts.length > 0 && (
              <div className="mt-4 border-t border-line pt-3 text-sm">
                <h3 className="font-semibold">Alerts</h3>
                {t.alerts.map((a, i) => <p key={i} className="mt-1 text-muted">{a.subject} ({a.status.toLowerCase().replace("_", " ")})</p>)}
              </div>
            )}
          </Panel>
        </aside>
      </div>
    </div>
  );
}

function ReviewPanel({ reviews, editable, onDone }: { reviews: TenderDetail["reviews"]; editable: boolean; onDone: () => void }) {
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function resolve(id: number, resolution: "ACCEPTED" | "REJECTED") {
    setBusy(true);
    setErr("");
    try {
      await api(`/reviews/${id}/resolve`, { method: "POST", body: JSON.stringify({ resolution, notes }) });
      onDone();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Decision needed" className="border-review">
      <ul className="space-y-3 text-sm">
        {reviews.map((r) => <li key={r.id}>{r.reason}</li>)}
      </ul>
      {editable ? (
        <div className="mt-4">
          <label className="block text-sm">
            <span className="text-muted">Note for the record</span>
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} maxLength={4000}
              className="mt-1 w-full rounded-md border border-line bg-surface px-2 py-1.5" />
          </label>
          {err && <p className="mt-2 text-sm text-hot">{err}</p>}
          <div className="mt-3 flex gap-2">
            <Button disabled={busy} onClick={() => resolve(reviews[0].id, "ACCEPTED")}>Pursue</Button>
            <Button disabled={busy} variant="danger" onClick={() => resolve(reviews[0].id, "REJECTED")}>Reject</Button>
          </div>
        </div>
      ) : <p className="mt-3 text-sm text-muted">An analyst needs to resolve this.</p>}
    </Panel>
  );
}

function DocumentsPanel({ t, editable, onUploaded }: { t: TenderDetail; editable: boolean; onUploaded: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    setErr("");
    try {
      for (const f of Array.from(files)) {
        const body = new FormData();
        body.append("file", f);
        await api(`/tenders/${t.id}/documents`, { method: "POST", body });
      }
      onUploaded();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  const statusText: Record<string, string> = {
    EXTRACTED: "read", PENDING: "queued", PENDING_DOWNLOAD: "waiting to download", FAILED: "could not be read",
    UNSUPPORTED: "unsupported format", REJECTED_UNSAFE: "rejected as unsafe", CONTAINER: "archive", BLOCKED_HUMAN_REQUIRED: "needs manual download",
  };

  return (
    <Panel title="Documents">
      {t.documents.length === 0 ? <p className="text-sm text-muted">No documents yet.</p> : (
        <ul className="space-y-2.5 text-sm">
          {t.documents.map((d) => (
            <li key={d.id}>
              <p className="break-words font-medium">{d.filename}</p>
              <p className={d.status === "EXTRACTED" ? "text-muted" : "text-high"}>
                {statusText[d.status] ?? d.status}
                {d.pages ? `, ${d.pages} pages` : ""}
                {d.method?.includes("ocr") ? ", OCR" : ""}
                {d.sections.length > 0 ? `, ${d.sections.length} sections found` : ""}
              </p>
              {d.error && <p className="text-xs text-muted">{d.error}</p>}
            </li>
          ))}
        </ul>
      )}
      {editable && (
        <div className="mt-4">
          <input ref={input} type="file" multiple className="sr-only" id="doc-upload"
            accept=".pdf,.doc,.docx,.xls,.xlsx,.csv,.txt,.zip,.png,.jpg,.jpeg,.tif,.tiff" onChange={(e) => upload(e.target.files)} />
          <label htmlFor="doc-upload" className={`inline-block cursor-pointer rounded-md border border-line px-3.5 py-1.5 text-sm font-medium hover:bg-sunken ${busy ? "pointer-events-none opacity-50" : ""}`}>
            {busy ? "Uploading…" : "Upload tender documents"}
          </label>
          <p className="mt-2 text-xs text-muted">PDF, Word, Excel, CSV, text, images or ZIP, up to 50 MB each.</p>
          {err && <p className="mt-2 text-sm text-hot">{err}</p>}
        </div>
      )}
      <p className="mt-3 text-xs text-muted">Last updated {date(t.last_analyzed_at)}</p>
    </Panel>
  );
}

const BASIS: Record<string, string> = {
  PRIMARY: "first found here", "TENDER_ID+REF": "same tender ID and reference", "TENDER_ID+TITLE": "same tender ID and title",
};

function SourcesPanel({ t }: { t: TenderDetail }) {
  if (t.sources.length === 0 && !t.source_url) return null;
  const sources = t.sources.length ? t.sources : [{ portal_code: t.portal_code ?? "", portal_name: t.portal_code ?? "Source", match_basis: "PRIMARY", source_url: t.source_url, lookup_hint: null, first_seen_at: t.first_seen_at, last_seen_at: null }];
  return (
    <Panel title="Where it’s listed">
      <ul className="space-y-4 text-sm">
        {sources.map((s) => (
          <li key={s.portal_code}>
            <p className="font-medium">{s.portal_name}</p>
            <p className="text-muted">{BASIS[s.match_basis] ?? s.match_basis}{s.last_seen_at ? `, last seen ${dateTime(s.last_seen_at)}` : ""}</p>
            {s.lookup_hint ? (
              <p className="mt-1 [overflow-wrap:anywhere]"><span className="text-muted">How to find it: </span>{s.lookup_hint}</p>
            ) : null}
            {s.source_url && (
              <a href={s.source_url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-block text-teal hover:underline">
                {s.lookup_hint ? "Open the portal" : "Open the tender on the portal"}
              </a>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
