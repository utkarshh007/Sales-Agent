# Tender Desk — Cybersecurity Tender Intelligence Agent

Tender Desk watches public tender portals, screens every tender against the company's capability catalog, and shows the team only the opportunities it can realistically pursue. Every decision is explained: which capability matched, which evidence supports it, how the score was built and which commercial rule applied.

The design follows three priorities: **relevance over volume, precision over count, explainability over black-box decisions.**

## How it works

```
Scheduler ─► Portal connector ─► tenders (+versions) ─► Document processor ─► Rule engine ─► LLM analysis ─► Matching ─► Scoring ─► Decision ─► Alerts
  (worker)    (CPPP, GePNIC, buyer pages; HTTP or headless browser)    PDF/DOCX/XLSX/ZIP/OCR    no tokens      only if needed    catalog-bound   explainable   audit log    email/digest
```

| Stage | Where | What it does |
|---|---|---|
| Discovery | `app/connectors/` | Walks listing pages newest-first, stops after pages with nothing new. Respects robots.txt, rate-limits, retries, and **stops at any CAPTCHA** (never bypassed). |
| Dedupe / updates | `app/pipeline.py` | Stable fingerprint per portal (`tender_sources`). The same tender listed on several portals is merged into one record (tender ID confirmed by reference number or title). A material change (closing date, corrigendum, value, category, description) creates a new `tender_versions` row and triggers re-analysis; unchanged tenders are never re-queued. |
| Documents | `app/documents/` | Magic-byte type detection, executable rejection, optional ClamAV, bounded ZIP handling, text extraction, OCR of scanned pages (tesseract), section detection, deterministic INR value parsing (lakh/crore). |
| Pre-filter | `app/rules/prefilter.py` | Closed tenders, non-cyber tenders (civil works, guards, CCTV…), and service-only tenders whose stated value exceeds ₹30 lakh are rejected **without calling the LLM**. On a live 452-tender CPPP sample, all 452 were screened out at zero token cost. |
| LLM analysis | `app/llm/` | Claude (`claude-opus-5-5` by default) extracts the 28 section-11 fields with value/confidence/evidence, maps requirements to the catalog, splits SERVICE/PRODUCT components. Structured outputs constrain capability and product IDs to the catalog, so the model cannot invent an offering. Only targeted document sections are sent. |
| Matching | `app/matching.py`, `app/semantic.py` | Merges explicit OEM mentions, competitor-OEM mentions, the deterministic lexicon, local semantic (embedding) similarity and the LLM's mapping. Functional requirements expand to the supported portfolio (e.g. "security event correlation" → SIEM → Splunk / QRadar / ArcSight / XSIAM). Each match records how it was found. |
| Commercial rules | `app/rules/commercial.py` | ₹30 lakh cap for SERVICE only; no cap for OEM/product; HYBRID evaluated per component; all configurable. |
| Scoring | `app/scoring.py` | 0–100 with configurable weights; every component carries a reason. Components that don't apply (OEM match on a service tender) are excluded and the score normalised. Each tender also gets a list of next actions, with how many points each could add. |
| Eligibility | `app/rules/eligibility.py` | Reads pre-qualification criteria (turnover, profitability, experience, references, certifications, CERT-In, local-supplier class, OEM authorisation…) from the tender and checks them against the **company profile**. Each criterion is met, not met, unknown or relaxed (startups/MSEs), with the tender's wording as evidence. |
| Hybrid and value analysis | `app/rules/hybrid.py`, `app/rules/commercial.py` | Splits service and product from a priced bill of quantities, estimates a value range from the EMD when none is stated (GFR 2–5%, always labelled as an estimate), and accepts a hybrid whose total is within the service limit without a split. |
| Alerts | `app/alerts/email.py` | HOT immediately, HIGH immediately or in the digest, MEDIUM in the daily digest, LOW never. |
| Audit | `audit_logs` table | Every decision with classification, values, matches, OEMs, score breakdown, reasons and model version. |

### Tender sources

None of these portals publishes an API or RSS feed, so both connectors read public HTML. Neither solves or bypasses CAPTCHAs.

| Source | Connector | What is read automatically | What needs a person |
|---|---|---|---|
| **CPPP** — Central Public Procurement Portal (eprocure.gov.in/cppp) | `cppp_html` | Listing: title, reference, tender ID, organisation, dates, corrigendum (`cpppdata` central; `mmpdata` states optional) | Detail page **and** documents (CAPTCHA) |
| **GePNIC** — NIC eProcurement: central CPSEs, central ministries, defence, PMGSY and 27 state/UT portals | `gepnic_html` | Organisation lists **and the tender detail page**: tender value, EMD, product category, work description, pre-qualification note, period of work, location, critical dates, inviting authority, document names | Documents only (CAPTCHA) |

GePNIC instances share one platform, so one connector serves all 31 of them (`GEPNIC_PORTALS` in `app/connectors/registry.py`, each verified live). The two central portals and defence are on by default. State portals start switched off; admins turn on the states the team bids in under **Sources & rules**.

How a GePNIC run stays light on the portal: it reads the organisation index and each organisation's tender list, one request per organisation. It opens a tender's detail page only when the title shows a possible capability or security signal **and** the tender is new, changed, or has never been detailed. On the live central portal, 2,138 tenders cost 85 listing requests and 7 detail requests. GePNIC links are session-bound, so tenders carry a "How to find it" hint (portal → organisation → tender ID) instead of a permalink.

Because the portal publishes the tender value, the ₹30 lakh service rule and category screening run on real data before anyone touches a document.

When documents are behind a CAPTCHA:

1. Tenders are screened on the listing and, for GePNIC, the detail page. Clear matches surface immediately, marked "Documents needed".
2. An analyst opens the portal, completes the CAPTCHA, downloads the documents, and uploads them on the tender page.
3. The full pipeline then runs on the documents (requirements, eligibility, BOQ values, LLM analysis) and the tender is re-scored.

### Buyer tender pages (Phase 3)

Many banks, regulators and agencies publish tenders on their own websites, usually as a table that links each tender's notice PDF, with no CAPTCHA. The `buyer_page` connector reads these pages, by plain HTTP or, for pages built by JavaScript, a headless Chromium browser (`render: "browser"`). It maps table columns by their headers, parses Indian date formats (including ranges like "28 Sept – 10 Nov"), and pulls references out of titles. For likely matches it **downloads the linked documents**, which then go through the full document pipeline automatically.

| Source | Status | Notes |
|---|---|---|
| State Bank of India (procurement news) | on | ~200 open tenders; the live run surfaced 2 cybersecurity tenders from SBI's Information Security Department and read their RFPs automatically |
| C-DAC tenders | on | Opens each tender's detail page for its document |
| ISRO tenders | off | Rows show only advert numbers, so `screen: "documents"` reads every new notice; heavier, so admins choose |

**Adding a page needs no code.** Under **Sources & rules → Add a buyer's tender page**, an admin enters the page address and runs **Preview**, a dry run showing what would be extracted. Saving requires confirming that the site's terms allow automated reading, plus a note on how that was confirmed; both are kept in the audit log. Pages whose robots.txt disallows them are refused.

Checked and **not added**, because automated access isn't permitted or the page was unreachable from the development network:

| Site | Reason |
|---|---|
| NCRB, IRDAI | robots.txt disallows all |
| MeitY, NPCI, NIC | Return HTTP 403 to automated clients (treated as not permitted) |
| NHAI, BSNL, NABARD, several banks, GeM | Unreachable from the development network |

Browser automation follows the same rules as plain HTTP:
- robots.txt is honoured and requests are rate-limited.
- Any CAPTCHA, or an HTTP 401/403/407/429 response, stops the run. The browser is never used to get past a block a plain client hits.
- Images, fonts and media are not loaded.
- Requests from page scripts to private addresses are aborted.

**Cross-portal merging.** CPPP aggregates tenders that are published on GePNIC portals, so the same tender is often seen twice. Tender IDs are only unique within a portal, so a match on tender ID must be confirmed by the reference number or title; otherwise the tenders stay separate. A merged tender keeps every source, and the richer GePNIC details enrich the CPPP listing.

**Not yet covered.** GeM (bidplus.gem.gov.in) blocks connections from outside India, and this build was developed from a non-Indian network, so no GeM connector was written or verified. Build and verify it from an Indian network. Gujarat (nProcure), Karnataka (KPPP), Telangana, Andhra Pradesh, Bihar and Chhattisgarh use other platforms or were unreachable, and each needs its own connector.

Analysts can also add tenders from any other source (partner emails, portals without a connector) under **Sources & rules → Add a tender by hand**. These go through the same analysis.

## Semantic matching and measured quality (Phase 4)

The lexicon only recognises wording it has been taught. Tenders often describe the same need in other words: "recording administrator sessions" is PAM, and "centralised collection of logs with real-time correlation" is SIEM. Phase 4 adds three things, each measured before it was kept.

- **Semantic matching** (`app/semantic.py`): a small local embedding model (`BAAI/bge-small-en-v1.5`, run with ONNX; no tender text leaves the server) compares the tender title, plus the portal's work description when available, with each capability's name, description and sub-capabilities.
  - A match must clear a similarity threshold **and** beat the closest non-cyber reference text (CCTV, IT hardware AMC, ERP, construction, non-IT audits…) by a margin.
  - Catalog exclusions always veto it.
  - Very short generic titles aren't judged.
  - It's fallback evidence only: when the lexicon or a named product already gives strong evidence, embeddings don't reclassify the tender.
  - If the model can't load, the engine continues with lexicon matching.
- **Competitor OEMs** (`competitors:` in the catalog): a tender naming CyberArk, Qualys, CrowdStrike and so on still states a requirement your portfolio can meet. It's matched to the capability, flagged as a bid risk, and checked for an "or equivalent" clause.
- **An evaluation harness** (`app/evaluation/`): labelled cases from real live titles and realistic paraphrases, plus about 2,745 real non-cyber titles from the live portals as negatives.

```bash
python -m app.evaluation.run --split holdout2 --errors   # precision / recall and every error
python -m app.evaluation.sweep                           # threshold sweep (dev split only)
```

Rules-only engine, title-level evidence only (the hardest case):

| Split | Title-screen recall | Precision | Recall |
|---|---|---|---|
| Before Phase 4 (dev) | 39% | 92% | 31% |
| Dev (thresholds tuned here) | 97% | 97% | 97% |
| Holdout, first look before fixes | 84% | 100% (0 false positives in 1,378 real titles) | 80% |
| **Holdout2: written before the final fixes, scored once** | **100%** | **100%** | **94%** |

Holdout2 is the honest estimate for the final version. On the live database, re-analysis recovered a real missed opportunity, the "Next Generation Security Operation Centre" EOI from SLDC Uttarakhand, with no new false positives across about 2,950 real tenders.

`tests/test_phase4_semantic.py` includes a quality gate that fails if precision or recall regresses on these splits. With an `ANTHROPIC_API_KEY`, `--llm` runs the same evaluation through the LLM analyser. This costs API credits and hasn't been run yet.

## Scoring v2, eligibility and hybrid analysis (Phase 5)

- **Company profile** (dashboard → Company profile; admins edit, everyone can view): turnover, profitable years, net worth, years in business, citable similar projects, largest similar order, certifications, empanelments, Make in India local-supplier class, Indian registration, debarment status, DPIIT-startup and MSE status, and the OEMs that will issue authorisations. **Empty fields are unknown, never assumed to be met.** Saving re-scores all open tenders. Every change is audited.
- **Eligibility check.** Criteria are read from a window after every eligibility heading in the documents: the clause, the appendix table, the table of contents. Section boundaries are unreliable here: in real RFPs the column header "Documents to be submitted" ends the section early. Definitions ("'Class-I local supplier' means…") are not mistaken for requirements, and a startup or MSE relaxation clause is honoured when the profile qualifies. Verified on the real SBI RFPs (CSCoE empanelment, 127 pages; TTX tool EOI, 37 pages). A gap sets eligibility to 0 and flags `ELIGIBILITY_GAP`. Unknowns lower the score only partially and generate a "complete the company profile" action.
- **Value estimation.** When a service tender states no value but has an EMD, the EMD implies a value of about EMD/5% to EMD/2%:
  - Entirely above ₹30 lakh: `SERVICE_LIKELY_OVER_CAP`, sent to review by default (`SERVICE_EMD_OVER_CAP_ACTION`).
  - Entirely within: `SERVICE_LIKELY_WITHIN_CAP`, accepted by default (`SERVICE_EMD_WITHIN_CAP_ACTION`).
  - Straddling the limit: stays "value unknown".
  - A stated value always wins.
- **Hybrid analysis.**
  - A hybrid whose total is within the service limit is accepted; its service part can't exceed the total.
  - A priced BOQ (XLSX/CSV) is split line by line into service and product, so the hybrid rules can run without the LLM. Blank price-bid formats are ignored.
- **Scoring changes.**
  - Commercial fit reflects how certain the value is.
  - Timeline is judged against the preparation each type needs (`LEAD_DAYS_SERVICE/OEM/HYBRID`, default 7/14/21 days).
  - Strategic value comes from the **buyer segment** (`buyer_segments:` in the catalog): regulator, bank, defence and law enforcement, government IT, critical infrastructure, state, PSU, ministry.
- **Next actions.** Each tender lists what would change its score, with estimated points: get the documents, confirm the value, find the hybrid split, complete the profile, eligibility gaps, OEM authorisation, competitor-OEM checks, and the pre-bid meeting.

## Business rules

| Opportunity | Rule |
|---|---|
| **SERVICE** | Value ≤ `SERVICE_MAX_VALUE_INR` (₹30,00,000) → eligible; above it → rejected. Value unknown → `SERVICE_UNKNOWN_VALUE_ACTION` (default: manual review). |
| **OEM / product** | Never rejected for value. An optional limit exists (`OEM_VALUE_LIMIT_ENABLED`), off by default. |
| **HYBRID** | Total value never rejects it. If the service component is stated: ≤ cap → accept; above cap → `HYBRID_SERVICE_OVER_CAP_ACTION` (default: review). If the split is unknown and `HYBRID_REVIEW_ENABLED` → `HYBRID_REVIEW_REQUIRED`. |
| **Relevance** | Requires a DIRECT or SEMANTIC match with confidence ≥ `STRONG_MATCH_MIN_CONFIDENCE`. Adjacent-only matches → review (or suppressed). Generic "IT/cyber security" wording alone → rejected. Score below `MIN_RELEVANCE_SCORE` → suppressed. |

Priorities: HOT ≥ 90, HIGH ≥ 75, MEDIUM ≥ 60, LOW ≥ 40, SUPPRESS < 40. Default weights: capability 30, technical 20, OEM 20, commercial 10, eligibility 10, timeline 5, strategic 5.

In rules-only mode (no API key), technical coverage is capped until the LLM itemises requirements. Listing-only tenders therefore top out around HIGH; HOT needs document-backed evidence.

### The capability catalog

`backend/app/catalog/catalog.yaml` is the single source of truth: 50 capabilities across categories A–T, 41 OEM products, the synonym lexicon (direct / semantic / adjacent), exclusion phrases (physical security, civil works…) and generic signals. Acronyms that have common non-cyber meanings in Indian tenders need cyber context: *DLP* is usually Defect Liability Period, *MDR* is Major District Road. The live-data regression tests in `tests/test_phase2_portals.py` guard these. Edit the YAML and run `python -m app.cli seed`. The LLM prompt and output schema are generated from it automatically.

## Running locally

Requirements: Python 3.12+, Node 22+. PostgreSQL is optional locally (SQLite is the default for development).

```bash
# backend
cd backend
python -m venv .venv && .venv/Scripts/activate      # Windows; use .venv/bin/activate on macOS/Linux
pip install -r requirements-dev.txt
python -m app.cli init-db
TENDER_USER_PASSWORD='Choose-A-Strong-Pass1' python -m app.cli create-user you@company.com admin
uvicorn app.api.main:app --port 8000              # API
python -m app.cli worker                           # scheduler + jobs (separate terminal)

# frontend
cd frontend
npm install
npm run dev                                        # http://localhost:3000 (proxies /api to :8000)
```

Run one discovery pass on demand: `python -m app.cli discover cppp --max-pages 3` or `python -m app.cli discover gepnic_central`.

Set `ANTHROPIC_API_KEY` to enable LLM analysis. Without it the system runs in **rules-only mode**: deterministic matching, clearly labelled in the UI and audit log.

## Deploying (Docker)

```bash
cp .env.example .env        # set SITE_ADDRESS, POSTGRES_PASSWORD, SECRET_KEY, SMTP_*, ANTHROPIC_API_KEY, BOOTSTRAP_ADMIN_*
docker compose up --build -d
```

Services: `db` (PostgreSQL 17), `api` (runs `alembic upgrade head` and seeds the catalog on start), `worker`, `web` (Next.js), `caddy` (automatic HTTPS for `SITE_ADDRESS`). Documents are stored on the `documents` volume under content-addressed names.

On first start, the admin account comes from `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD`, which is only used when no users exist. Add more users with `docker compose exec api python -m app.cli create-user EMAIL analyst`. Roles: `viewer` reads; `analyst` uploads, re-runs and resolves reviews; `admin` also manages portals and users.

Scaling: run more `worker` replicas. Jobs are claimed with `SELECT … FOR UPDATE SKIP LOCKED`.

## Configuration

All rules, thresholds, weights, schedules and integrations are environment variables. `.env.example` lists every one. The dashboard's **Sources & rules** page shows the active values; secrets are never exposed.

## Security

- Passwords hashed with Argon2id; sessions are signed JWTs in `HttpOnly`, `SameSite=Lax` cookies (`Secure` in production) with a double-submit CSRF token on every mutation.
- Role-based access, login and API rate limiting, strict security headers, HSTS via Caddy, API docs disabled in production.
- No credentials in code. Portal credentials are referenced by env-var name in `portal_credentials_metadata`; any stored secret is Fernet-encrypted with `FERNET_KEY`. Emails never contain secrets.
- **SSRF protection.** Every fetch, including each redirect hop, document links found on pages, and requests made by scripts inside the headless browser, is refused if it targets a private, loopback, link-local or metadata address. Admin-supplied buyer-page URLs go through the same check.
- **Browser isolation.** Chromium runs headless as the container's unprivileged user. As is Playwright's default, it runs without Chromium's own sandbox, so the container is the isolation boundary. Pages can't download files, and page-initiated requests are filtered as above.
- Documents are treated as hostile: type is detected from content, executables are refused, archives are bounded (member count, total size, nesting) and never extracted to archive-supplied paths, macros are never executed, optional ClamAV scanning, and files are stored under hash names.
- Tender text is passed to the LLM as untrusted data, and the prompt instructs the model to ignore instructions inside it.
- Portal access respects robots.txt and rate limits. CAPTCHA, anti-bot and login walls stop the connector and are logged as blockers.

## Adding a portal

1. Subclass `PortalConnector` (`app/connectors/base.py`). Implement `list_page()`, and `fetch_detail()` / `download_document()` if the portal exposes them without human intervention. For organisations' own tender pages you usually need no code: add a `buyer_page` source (see "Buyer tender pages").
2. Register it in `app/connectors/registry.py` and add a `portals` row (or a `DEFAULT_PORTALS` entry).
3. Add a parser test against a saved page in `tests/fixtures/`.

Nothing in the engine, scoring, alerts or dashboard changes.

## Tests

```bash
cd backend && pytest -q        # 157 tests (one launches headless Chromium, the quality gate loads the embedding model)
cd frontend && npm run lint && npm run build
```

- `tests/test_required_cases.py`: the 12 cases from the specification (₹18 L red team → accept, ₹31 L AppSec → reject, ₹2 Cr Splunk → OEM accept, hybrid variants, civil construction → reject without LLM, SIEM/PAM/CSPM/DLP/BAS portfolio matching) plus semantic-matching examples.
- `tests/test_pipeline_e2e.py`: discovery → dedupe → analysis → persistence → alerts → update/versioning → document upload → re-qualification, using a real CPPP page captured from the live site.
- `tests/test_llm.py`: request shape (caching, structured output, fallbacks), catalog-bound IDs, refusal → manual review, truncation → retry.
- `tests/test_phase2_portals.py`: GePNIC parsing against live captures, a simulated multi-organisation crawl, detail fetched only for candidates, no false updates on reruns, closing-date extensions, detail caps, cross-portal merging and ID collisions, CAPTCHA blockers, and false-friend acronyms taken from live data.
- `tests/test_phase3_buyer_pages.py`: SBI, C-DAC and ISRO parsing against live captures, date formats, documents fetched only for candidates and then qualified end to end from the PDF, document-screening mode, robots.txt and SSRF refusals, and a real headless-browser test showing JavaScript-built tables are read where plain HTTP sees nothing.
- `tests/test_phase4_semantic.py`: semantic thresholds, the non-cyber margin, title segments, exclusion vetoes, fallback-only behaviour, competitor OEMs, and the matching-quality gate on the real model.
- `tests/test_phase5_scoring.py`: eligibility extraction on the real SBI RFP appendices, checks against the profile (met, not met, unknown, relaxed, CMMI levels, OEM authorisation), EMD bands, the hybrid shortcut, the priced-BOQ split, buyer segments, timeline by type, next actions, and an end-to-end eligibility gap.
- `tests/test_documents.py`, `tests/test_connectors.py`, `tests/test_api.py`: extraction and safety, parsing/robots/CAPTCHA handling, auth, CSRF, roles, upload, review flow.

## Known limitations and next phases

- **Documents need a human** on CPPP and GePNIC because of the CAPTCHA (see above). GePNIC detail pages close most of the gap for screening.
- **GeM** is not yet connected (see "Not yet covered").
- A full GePNIC sweep is one request per organisation, about 4 minutes for the central portal at the polite 2-second delay. With many state portals turned on, run more than one worker so discovery doesn't delay analysis.
- Legacy `.doc` / `.xls` files are flagged for conversion; DOCX/XLSX/PDF are fully supported.
- The rate limiter is per-process; with several API replicas, rate-limit at the proxy as well.
- Semantic search over historical tenders (pgvector) and win/loss analytics belong to Phases 4–6. The schema already keeps every version, score and decision needed for them.
