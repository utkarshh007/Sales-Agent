"""Historical intelligence (Phase 6): similar past tenders, buyer history, recurring tenders.

Every analysed tender gets an embedding of its title (plus the portal's work description), from the
same local model as semantic matching. Similar-tender search runs in memory over all stored vectors,
which is fast for tens of thousands of tenders; beyond that, pgvector on PostgreSQL is the drop-in path.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from datetime import timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import as_utc
from app.models import BidOutcome, Tender, TenderEmbedding

log = logging.getLogger(__name__)

RECURRENCE_MIN_DAYS = 300  # a "similar tender from the same buyer" this far apart looks periodic (annual)
RECURRENCE_MIN_SIMILARITY = 0.90


# Procurement boilerplate that opens most Indian tender titles. Left in, it dominates the embedding and
# makes every "EOI Document for Selection of partner for …" look alike, whatever the subject.
_BOILERPLATE = [re.compile(p, re.IGNORECASE) for p in (
    r"^(?:e-?)?tender(?: notice| document)?\s*(?:no\.?\s*\S+\s*)?(?:for|of|:|-)?\s*",
    r"^(?:notice inviting (?:e-?)?tender|NIT|RFP|RFQ|RFE|EOI|request for (?:proposal|quotation|expression of interest|eoi)|"
    r"(?:global |pre-?bid )?expression of interest(?: \(EOI\))?|pre-?bid eoi)(?: document)?\s*(?:for|of|to|:|-)?\s*",
    r"^(?:selection|engagement|empanelment|appointment|hiring|onboarding)\s+of\s+(?:an?\s+|the\s+)?"
    r"(?:(?:implementation|consortium|pre-?bid|back-?end|technology|jv|system integrator)\s+)?"
    r"(?:partners?|agenc(?:y|ies)|vendors?|consultants?|firms?|bidders?|service providers?|subcontractors?)?\s*(?:for|to|of)?\s*(?:the\s+)?",
    r"^(?:participat\w+ in [^,]{0,80}? (?:tender )?for\s+)",
    r"^(?:supply,?\s*(?:installation,?\s*)?(?:testing,?\s*)?(?:(?:and|&)\s*)?(?:commissioning|maintenance)?|SITC|procurement|purchase)\s+of\s+",
)]


_LOCATION_TAIL = re.compile(r"\s+(?:at|in)\s+(?:various|different|all|the|its|our|\d|[A-Z]).*$")


def subject_of(title: str) -> str:
    """The tender's subject without its procurement boilerplate or trailing location ("… at Slapper")
    — place names are rare words and would otherwise make unrelated tenders from one site look alike.
    For similarity only, never for display."""
    text = re.sub(r"\s+", " ", title or "").strip()
    for _ in range(4):  # boilerplate first: "…participation in BSNL Tender for…" is not a location
        before = text
        for rx in _BOILERPLATE:
            text = rx.sub("", text).strip(" ,.-:")
        if text == before:
            break
    without_place = _LOCATION_TAIL.sub("", text)
    text = without_place if len(without_place) >= 8 else text
    return text if len(text) >= 4 else (title or "")


def tender_text(t: Tender) -> str:
    """Subject (title without boilerplate) plus the portal's work description, when a detail page was read."""
    from app.rules.prefilter import semantic_text
    return semantic_text(subject_of(t.title), (t.raw or {}).get("portal_text", ""))


def embed_tender(session: Session, t: Tender, matcher) -> bool:
    """Store/refresh the tender's vector. Returns False when no matcher is available."""
    if matcher is None:
        return False
    text = tender_text(t)
    h = hashlib.sha256(text.encode()).hexdigest()
    row = session.get(TenderEmbedding, t.id)
    if row is not None and row.text_hash == h and row.model == matcher.settings.EMBEDDING_MODEL:
        return True
    vec = matcher.vector(text).astype(np.float16).tobytes()
    if row is None:
        session.add(TenderEmbedding(tender_id=t.id, model=matcher.settings.EMBEDDING_MODEL, text_hash=h, vector=vec))
    else:
        row.model, row.text_hash, row.vector = matcher.settings.EMBEDDING_MODEL, h, vec
    return True


class SimilarityIndex:
    """All tender vectors as one normalised matrix, rebuilt when the stored set changes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: tuple | None = None
        self._ids: np.ndarray = np.zeros(0, dtype=np.int64)
        self._matrix: np.ndarray = np.zeros((0, 0), dtype=np.float32)

    def _refresh(self, session: Session) -> None:
        key = tuple(session.execute(select(func.count(TenderEmbedding.tender_id), func.max(TenderEmbedding.created_at))).one())
        if key == self._key:
            return
        with self._lock:
            rows = session.execute(select(TenderEmbedding.tender_id, TenderEmbedding.vector)).all()
            if rows:
                ids = np.array([r[0] for r in rows], dtype=np.int64)
                m = np.stack([np.frombuffer(r[1], dtype=np.float16).astype(np.float32) for r in rows])
                m /= np.where(np.linalg.norm(m, axis=1, keepdims=True) == 0, 1, np.linalg.norm(m, axis=1, keepdims=True))
                self._ids, self._matrix = ids, m
            else:
                self._ids, self._matrix = np.zeros(0, dtype=np.int64), np.zeros((0, 0), dtype=np.float32)
            self._key = key

    def similar(self, session: Session, tender_id: int, k: int = 8, min_similarity: float = 0.80) -> list[tuple[int, float]]:
        self._refresh(session)
        if not len(self._ids):
            return []
        pos = np.where(self._ids == tender_id)[0]
        if not len(pos):
            return []
        sims = self._matrix @ self._matrix[pos[0]]
        order = np.argsort(-sims)
        out = []
        for i in order:
            if int(self._ids[i]) == tender_id:
                continue
            if sims[i] < min_similarity or len(out) >= k:
                break
            out.append((int(self._ids[i]), round(float(sims[i]), 3)))
        return out


_index = SimilarityIndex()

# A tender counts as similar only when meaning AND distinctive vocabulary agree: on short titles the
# embedding alone scores unrelated tenders ~0.8 (a SOC tender vs fibre maintenance) and related ones
# ~0.72 (two CCTV tenders), so cosine similarity cannot be thresholded by itself.
MIN_COSINE = 0.68
MIN_WORD_OVERLAP = 0.20
# Below this overlap the titles must also share a two-word phrase: "security operation centre" and
# "operations of data centre" share two words but describe different things.
STRONG_WORD_OVERLAP = 0.40
_STOP = set("of for and the to in at on with a an by as per or from under its this that be is are various different "
            "work works supply services service providing provision maintenance year years".split())


def _stem(w: str) -> str:
    w = "centre" if w == "center" else w
    return w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w


def _tokens(title: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z][a-z0-9-]{2,}", subject_of(title).lower()) if w not in _STOP]


def _words(title: str) -> set[str]:
    return set(_tokens(title))


def _bigrams(title: str) -> set[tuple[str, str]]:
    t = _tokens(title)
    return set(zip(t, t[1:]))


class _Idf:
    def __init__(self) -> None:
        self.key = None
        self.idf: dict[str, float] = {}

    def get(self, session: Session) -> dict[str, float]:
        import math
        from collections import Counter
        key = session.scalar(select(func.count(Tender.id)))
        if key != self.key:
            titles = list(session.scalars(select(Tender.title)))
            df = Counter(w for t in titles for w in _words(t))
            n = len(titles)
            # smoothed IDF: common words keep a small positive weight (an unsmoothed log goes to zero or
            # below for words present in every tender, which breaks small corpora)
            self.idf = {w: math.log((1 + n) / (1 + c)) + 1 for w, c in df.items()}
            self.key = key
        return self.idf


_idf = _Idf()


def word_overlap(a: str, b: str, idf: dict[str, float]) -> float:
    wa, wb = _words(a), _words(b)
    den = sum(idf.get(w, 1.0) for w in wa)
    return sum(idf.get(w, 1.0) for w in wa & wb) / den if den else 0.0


def similar_tenders(session: Session, t: Tender, k: int = 8) -> list[dict]:
    idf = _idf.get(session)
    scored = []
    for other_id, sim in _index.similar(session, t.id, k=60, min_similarity=MIN_COSINE):
        o = session.get(Tender, other_id)
        if o is None:
            continue
        ov = word_overlap(t.title, o.title, idf)
        if ov >= STRONG_WORD_OVERLAP or (ov >= MIN_WORD_OVERLAP and _bigrams(t.title) & _bigrams(o.title)):
            scored.append((round(0.6 * sim + 0.4 * ov, 3), sim, o))
    out = []
    for score, sim, o in sorted(scored, key=lambda x: -x[0])[:k]:
        outcome = session.get(BidOutcome, o.id)
        out.append({
            "id": o.id, "title": o.title, "organization": o.organization, "similarity": score,
            "published_at": as_utc(o.published_at).isoformat() if o.published_at else None,
            "closing_at": as_utc(o.closing_at).isoformat() if o.closing_at else None,
            "decision": o.decision, "value_inr": (o.value_analysis or {}).get("total_value_inr") or o.tender_value_inr,
            "outcome": outcome.stage if outcome else None,
            "award_value_inr": outcome.award_value_inr if outcome else None,
            "winner": outcome.winner if outcome else None,
        })
    return out


def buyer_history(session: Session, t: Tender) -> dict | None:
    if not t.organization:
        return None
    rows = session.scalars(select(Tender).where(Tender.organization == t.organization, Tender.id != t.id)).all()
    outcomes = {o.tender_id: o for o in session.scalars(select(BidOutcome).where(BidOutcome.tender_id.in_([r.id for r in rows])))} if rows else {}
    surfaced = [r for r in rows if r.decision in ("ACCEPTED", "MANUAL_REVIEW")]
    stages = [o.stage for o in outcomes.values()]
    return {
        "organization": t.organization,
        "tenders_seen": len(rows),
        "surfaced": len(surfaced),
        "bid": sum(s in ("BIDDING", "SUBMITTED", "WON", "LOST") for s in stages),
        "won": stages.count("WON"),
        "lost": stages.count("LOST"),
        "recent": [{"id": r.id, "title": r.title, "decision": r.decision,
                    "published_at": as_utc(r.published_at).isoformat() if r.published_at else None,
                    "outcome": outcomes[r.id].stage if r.id in outcomes else None}
                   for r in sorted(surfaced, key=lambda r: as_utc(r.first_seen_at), reverse=True)[:5]],
    }


def recurrence(session: Session, t: Tender) -> dict | None:
    """A near-identical tender from the same buyer about a year apart suggests a recurring (e.g. annual)
    requirement, and a likely next issue date."""
    if not t.published_at or not t.organization:
        return None
    me = as_utc(t.published_at)
    best = None
    mine = _words(t.title)
    for other_id, sim in _index.similar(session, t.id, k=20, min_similarity=RECURRENCE_MIN_SIMILARITY):
        o = session.get(Tender, other_id)
        if o is None or o.organization != t.organization or not o.published_at:
            continue
        theirs = _words(o.title)
        if not mine or len(mine & theirs) / len(mine | theirs) < 0.6:  # a re-issue repeats its wording
            continue
        gap = abs((me - as_utc(o.published_at)).days)
        if gap >= RECURRENCE_MIN_DAYS and (best is None or gap < best[1]):
            best = (o, gap, sim)
    if best is None:
        return None
    o, gap, sim = best
    latest = max(me, as_utc(o.published_at))
    return {"previous_id": o.id, "previous_title": o.title, "previous_published_at": as_utc(o.published_at).isoformat(),
            "interval_days": gap, "similarity": sim,
            "next_expected_around": (latest + timedelta(days=gap)).date().isoformat()}
