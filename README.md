# Tender Desk — Cybersecurity Tender Intelligence Agent

Tender Desk watches public tender portals, screens every tender against the company's capability catalog, and shows the team only the opportunities it can realistically pursue. Every decision is explained: which capability matched, which evidence supports it, how the score was built and which commercial rule applied.

The design follows three priorities: **relevance over volume, precision over count, explainability over black-box decisions.**

## How it works

```
Scheduler ─► Portal connector ─► tenders (+versions) ─► Document processor ─► Rule engine ─► LLM analysis ─► Matching ─► Scoring ─► Decision ─► Alerts
  (worker)    (CPPP HTML)          fingerprint/dedupe     PDF/DOCX/XLSX/ZIP/OCR    no tokens      only if needed    catalog-bound   explainable   audit log    email/digest
```

| Stage | Where | What it does |
|---|---|---|
| Discovery | `app/connectors/` | Walks listing pages newest-first, stops after pages with nothing new. Respects robots.txt, rate-limits, retries, and **stops at any CAPTCHA** (never bypassed). |
| Dedupe / updates | `app/pipeline.py` | Stable fingerprint (portal + tender ID). A material change (closing date, corrigendum, value, documents) creates a new `tender_versions` row and triggers re-analysis; unchanged tenders are never re-queued. |
| Documents | `app/documents/` | Magic-byte type detection, executable rejection, optional ClamAV, bounded ZIP handling, text extraction, OCR of scanned pages (tesseract), section detection, deterministic INR value parsing (lakh/crore). |
| Pre-filter | `app/rules/prefilter.py` | Closed tenders, non-cyber tenders (civil works, guards, CCTV…), and service-only tenders whose stated value exceeds ₹30 lakh are rejected **without calling the LLM**. On a live 452-tender CPPP sample, all 452 were screened out at zero token cost. |
| LLM analysis | `app/llm/` | Claude (`claude-opus-5-5` by default) extracts the 28 section-11 fields with value/confidence/evidence, maps requirements to the catalog, splits SERVICE/PRODUCT components. Structured outputs constrain capability and product IDs to the catalog, so the model cannot invent an offering. Only targeted document sections are sent. |
| Matching | `app/matching.py` | Merges explicit OEM mentions, LLM semantic mapping and the deterministic lexicon. Functional requirements expand to the supported portfolio (e.g. "security event correlation" → SIEM → Splunk / QRadar / ArcSight / XSIAM). |
| Commercial rules | `app/rules/commercial.py` | ₹30 lakh cap for SERVICE only; no cap for OEM/product; HYBRID evaluated per component; all configurable. |
| Scoring | `app/scoring.py` | 0–100 with configurable weights; every component carries a reason. Components that don't apply (OEM match on a service tender) are excluded and the score normalised. |
| Alerts | `app/alerts/email.py` | HOT immediately, HIGH immediately or in the digest, MEDIUM in the daily digest, LOW never. |
| Audit | `audit_logs` table | Every decision with classification, values, matches, OEMs, score breakdown, reasons and model version. |

### Tender sources

**Phase 1 portal: CPPP — Central Public Procurement Portal (eprocure.gov.in).** CPPP publishes no API or RSS feed, so the connector reads the public HTML listings (`cpppdata` for central tenders and, optionally, `mmpdata` for state tenders). These give title, reference, tender ID, organisation, published, closing and opening dates, and corrigendum status.

CPPP's tender detail pages and documents sit behind an image CAPTCHA. The system **does not solve or bypass it**. Instead:

1. Tenders are screened on their listing data. Clear matches surface immediately, marked "Documents needed".
2. An analyst opens the source link, completes the CAPTCHA, downloads the documents, and uploads them on the tender page.
3. The full pipeline then runs on the documents (value extraction, requirements, eligibility, LLM analysis) and the tender is re-scored.

Analysts can also add tenders from any other source (partner emails, portals without a connector) under **Sources & rules → Add a tender by hand**. These go through the same analysis.

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

`backend/app/catalog/catalog.yaml` is the single source of truth: 50 capabilities across categories A–T, 41 OEM products, the synonym lexicon (direct / semantic / adjacent), exclusion phrases (physical security, civil works…) and generic signals. Edit the YAML and run `python -m app.cli seed`. The LLM prompt and output schema are generated from it automatically.

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

Run one discovery pass on demand: `python -m app.cli discover cppp --max-pages 3`.

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
- Documents are treated as hostile: type is detected from content, executables are refused, archives are bounded (member count, total size, nesting) and never extracted to archive-supplied paths, macros are never executed, optional ClamAV scanning, and files are stored under hash names.
- Tender text is passed to the LLM as untrusted data, and the prompt instructs the model to ignore instructions inside it.
- Portal access respects robots.txt and rate limits. CAPTCHA, anti-bot and login walls stop the connector and are logged as blockers.

## Adding a portal

1. Subclass `PortalConnector` (`app/connectors/base.py`). Implement `list_page()`, and `fetch_detail()` / `download_document()` if the portal exposes them without human intervention. Use `BrowserPortalConnector` for JavaScript-only portals where automation is permitted.
2. Register it in `app/connectors/registry.py` and add a `portals` row (or a `DEFAULT_PORTALS` entry).
3. Add a parser test against a saved page in `tests/fixtures/`.

Nothing in the engine, scoring, alerts or dashboard changes.

## Tests

```bash
cd backend && pytest -q        # 76 tests
cd frontend && npm run lint && npm run build
```

- `tests/test_required_cases.py`: the 12 cases from the specification (₹18 L red team → accept, ₹31 L AppSec → reject, ₹2 Cr Splunk → OEM accept, hybrid variants, civil construction → reject without LLM, SIEM/PAM/CSPM/DLP/BAS portfolio matching) plus semantic-matching examples.
- `tests/test_pipeline_e2e.py`: discovery → dedupe → analysis → persistence → alerts → update/versioning → document upload → re-qualification, using a real CPPP page captured from the live site.
- `tests/test_llm.py`: request shape (caching, structured output, fallbacks), catalog-bound IDs, refusal → manual review, truncation → retry.
- `tests/test_documents.py`, `tests/test_connectors.py`, `tests/test_api.py`: extraction and safety, parsing/robots/CAPTCHA handling, auth, CSRF, roles, upload, review flow.

## Known limitations and next phases

- **CPPP documents need a human** because of the CAPTCHA (see above). Phase 2 candidates with better machine access: GeM bid listings (also shown on CPPP), state NIC eProcurement instances (same CAPTCHA model), and organisation-specific portals.
- Legacy `.doc` / `.xls` files are flagged for conversion; DOCX/XLSX/PDF are fully supported.
- The rate limiter is per-process; with several API replicas, rate-limit at the proxy as well.
- Semantic search over historical tenders (pgvector) and win/loss analytics belong to Phases 4–6. The schema already keeps every version, score and decision needed for them.
