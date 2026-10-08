"use client";

import { useState, type ReactNode } from "react";
import { Panel } from "@/components/ui";
import { dateTime } from "@/lib/format";
import type { Brief, Contacts } from "@/lib/types";

/** The requirement in plain words, highlighted pink so it is the first thing read on every tender. */
export function WhatTheyNeed({ brief }: { brief: Brief }) {
  return (
    <section className="rounded-md border border-pink-edge/40 border-l-[4px] border-l-pink-edge bg-pink-soft px-5 py-4"
      aria-labelledby="what-they-need">
      <h2 id="what-they-need" className="text-sm font-semibold text-pink">What they need, in plain words</h2>
      <p className="mt-1.5 font-serif text-[19px] leading-snug">{brief.headline}</p>
      {(brief.stage || brief.kind) && (
        <p className="mt-2 leading-relaxed">{[brief.stage, brief.kind].filter(Boolean).join(" ")}</p>
      )}
      {brief.fit && <p className="mt-1 leading-relaxed">{brief.fit}</p>}
      {brief.points.length > 0 && (
        <dl className="mt-3 grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-[auto_1fr]">
          {brief.points.map((p) => (
            <div key={p.label} className="contents">
              <dt className="font-medium">{p.label}</dt>
              <dd>{p.value}</dd>
            </div>
          ))}
        </dl>
      )}
      <p className="mt-3 text-xs text-muted">
        {brief.source === "llm" ? "Written by the AI from the tender text." : "Put together from the title and what the analysis found. Nothing is added that the tender doesn't say."}
      </p>
    </section>
  );
}

function CopyButton({ value }: { value: string }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button" className="ml-2 text-xs text-teal hover:underline"
      onClick={() => navigator.clipboard?.writeText(value).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); })}>
      {done ? "Copied" : "Copy"}
    </button>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return <div><dt className="text-muted">{label}</dt><dd className="break-words">{children}</dd></div>;
}

function Source({ source, evidence }: { source?: string; evidence?: string }) {
  if (!source) return null;
  return (
    <details className="mt-1 text-xs text-muted">
      <summary className="cursor-pointer">From {source}</summary>
      {evidence && <p className="mt-1 italic leading-relaxed">“{evidence}”</p>}
    </details>
  );
}

/** Who the client is, who to contact, and where and how the response goes. Only what the tender states. */
export function RespondPanel({ contacts, closingAt }: { contacts: Contacts; closingAt: string | null }) {
  const c = contacts.client;
  const s = contacts.submission;
  const readDocs = contacts.read_documents.length > 0;
  return (
    <Panel title="Client & how to respond">
      <div className="space-y-4 text-sm">
        <div>
          <h3 className="font-semibold">Where to submit</h3>
          {s.mode === "ONLINE_PORTAL" && (
            <p className="mt-1">
              Online, on {s.portal_name ?? "the e-procurement portal"}
              {s.url && <>: <a href={s.url} target="_blank" rel="noopener noreferrer" className="break-all text-teal hover:underline">{s.url}</a></>}
            </p>
          )}
          {s.mode === "EMAIL" && s.email && (
            <p className="mt-1">By email to <a href={`mailto:${s.email}`} className="font-semibold text-teal hover:underline">{s.email}</a><CopyButton value={s.email} /></p>
          )}
          {s.mode === "PHYSICAL" && (
            <p className="mt-1">On paper, in a sealed cover{s.address ? ` to ${s.address}` : ""}. Check the exact address in the tender document.</p>
          )}
          {s.mode === "UNKNOWN" && (
            <p className="mt-1 text-muted">
              Not stated in what the agent has read{readDocs ? "" : " (no tender documents yet)"}. The tender document will say.
            </p>
          )}
          {closingAt && <p className="mt-1">Deadline: <b>{dateTime(closingAt)}</b></p>}
          <Source source={s.source} evidence={s.evidence} />
        </div>

        {c && (
          <div className="border-t border-line pt-4">
            <h3 className="font-semibold">Client</h3>
            <dl className="mt-1 space-y-1.5">
              {c.organization && <Row label="Organisation">{c.organization}</Row>}
              {c.department && <Row label="Department">{c.department}</Row>}
              {c.name && <Row label="Contact person">{c.name}{c.designation ? `, ${c.designation}` : ""}</Row>}
              {c.email && (
                <Row label="Email">
                  <a href={`mailto:${c.email}`} className="text-teal hover:underline">{c.email}</a><CopyButton value={c.email} />
                </Row>
              )}
              {c.phone && <Row label="Phone"><a href={`tel:${c.phone.replace(/[^\d+]/g, "")}`} className="text-teal hover:underline">{c.phone}</a></Row>}
              {c.address && <Row label="Address">{c.address}</Row>}
            </dl>
            {!c.name && !c.email && !c.phone && (
              <p className="mt-1.5 text-muted">No contact person is given in what the agent has read{readDocs ? "" : " yet"}.</p>
            )}
            <Source source={c.source} evidence={c.evidence} />
          </div>
        )}

        {contacts.helpdesk && (
          <div className="border-t border-line pt-4">
            <h3 className="font-semibold">Portal helpdesk</h3>
            <p className="mt-1 text-muted">For problems uploading the bid, not for questions about the tender.</p>
            <dl className="mt-1 space-y-1.5">
              {contacts.helpdesk.email && <Row label="Email"><a href={`mailto:${contacts.helpdesk.email}`} className="text-teal hover:underline">{contacts.helpdesk.email}</a></Row>}
              {contacts.helpdesk.phone && <Row label="Phone">{contacts.helpdesk.phone}</Row>}
            </dl>
            <Source source={contacts.helpdesk.source} evidence={contacts.helpdesk.evidence} />
          </div>
        )}

        {contacts.other_emails.length > 0 && (
          <div className="border-t border-line pt-4">
            <h3 className="font-semibold">Other emails in the documents</h3>
            <ul className="mt-1 space-y-2">
              {contacts.other_emails.map((o) => (
                <li key={o.email}>
                  <a href={`mailto:${o.email}`} className="text-teal hover:underline">{o.email}</a>
                  <p className="text-xs italic text-muted">“{o.context}”</p>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Panel>
  );
}
