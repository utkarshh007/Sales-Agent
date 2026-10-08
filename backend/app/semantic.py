"""Semantic (embedding) matching against the capability catalog (Phase 4).

The lexicon only recognises wording it has been taught. Tenders routinely describe the same need in
other words ("recording administrator sessions" is PAM; "centralised log collection with real-time
correlation" is SIEM). A small local embedding model compares a tender's text with each capability's
name, description and sub-capabilities.

Precision safeguards, measured with app/evaluation:
  * a capability must clear an absolute similarity threshold, and
  * it must beat the closest *non-cyber* reference text (CCTV, IT hardware AMC, ERP, construction…)
    by a margin — vocabulary overlap alone is not enough;
  * catalog exclusions (security guards, CCTV, defect liability…) always veto a semantic match.

Runs locally (ONNX via fastembed; no tender text leaves the server). If the model cannot be loaded the
engine logs a warning and continues with lexicon matching only.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass

import numpy as np

from app.catalog import Catalog
from app.config import Settings

log = logging.getLogger(__name__)
_STOPWORDS = {"of", "for", "and", "the", "to", "in", "at", "on", "with", "a", "an", "by", "as", "per", "or"}
def _normalise(text: str) -> str:
    """Indian portals often publish titles in capitals; embedding models read sentence case better."""
    letters = [c for c in text if c.isalpha()]
    return text.lower() if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6 else text


def _segments(text: str, min_words: int) -> list[str]:
    """The whole text, plus each comma/semicolon segment long enough to be judged on its own."""
    parts = [p.strip() for p in re.split(r"[,;]| - ", text) if len(_CONTENT_WORD.findall(p)) >= min_words - 1]
    return [text] + (parts if len(parts) > 1 else [])


_CONTENT_WORD = re.compile(r"\b(?!(?:" + "|".join(_STOPWORDS) + r")\b)[A-Za-z][A-Za-z-]{1,}\b", re.IGNORECASE)


@dataclass
class SemanticHit:
    capability_id: str
    similarity: float
    anchor: str  # the catalog text it was closest to
    match_type: str  # SEMANTIC | ADJACENT
    margin: float  # how far above the closest non-cyber reference

    @property
    def confidence(self) -> int:
        # map similarity onto the same 0–100 scale as lexicon/LLM matches, capped below DIRECT
        return int(max(40, min(85, round(40 + (self.similarity - 0.70) * 300))))


class SemanticMatcher:
    def __init__(self, catalog: Catalog, settings: Settings, embed=None):
        self.catalog = catalog
        self.settings = settings
        self._embed_fn = embed  # injectable for tests: list[str] -> np.ndarray
        self._lock = threading.Lock()
        self._ready = False
        self._model = None
        self._cache: dict[str, np.ndarray] = {}  # titles recur on every crawl; embed each once

    def _vector(self, text: str) -> np.ndarray:
        v = self._cache.get(text)
        if v is None:
            v = self._embed([text])[0]
            if len(self._cache) >= 50_000:
                self._cache.clear()
            self._cache[text] = v
        return v

    # ------------------------------------------------------------------ embeddings
    def _embed(self, texts: list[str]) -> np.ndarray:
        if self._embed_fn is not None:
            vecs = np.asarray(self._embed_fn(texts), dtype=np.float32)
        else:
            vecs = np.asarray(list(self._model.embed(texts)), dtype=np.float32)  # type: ignore[union-attr]
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.where(norms == 0, 1, norms)

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            if self._embed_fn is None:
                from fastembed import TextEmbedding
                kwargs = {"cache_dir": self.settings.EMBEDDING_CACHE_DIR} if self.settings.EMBEDDING_CACHE_DIR else {}
                self._model = TextEmbedding(self.settings.EMBEDDING_MODEL, **kwargs)
            self._anchor_caps: list[str] = []
            self._anchor_texts: list[str] = []
            for cap in self.catalog.capabilities.values():
                for text in cap.anchor_texts():
                    self._anchor_caps.append(cap.id)
                    self._anchor_texts.append(text)
            self._anchors = self._embed(self._anchor_texts)
            self._negatives = self._embed(self.catalog.negative_anchors) if self.catalog.negative_anchors else None
            self._ready = True
            log.info("semantic matcher ready: %d capability anchors, %d negative anchors",
                     len(self._anchor_texts), len(self.catalog.negative_anchors))

    def vector(self, text: str) -> np.ndarray:
        """Normalised embedding of a text (cached); used for similar-tender search."""
        self._ensure_ready()
        return self._vector(_normalise((text or "")[:1000]))

    # ------------------------------------------------------------------ matching
    def match(self, text: str, top_k: int = 3, settings: Settings | None = None) -> list[SemanticHit]:
        """Thresholds come from `settings` when given (the caller's configuration), else from the
        settings the matcher was built with. The model and embedding cache are shared either way."""
        s = settings or self.settings
        text = (text or "").strip()
        # a handful of generic words ("Comprehensive Security Services") is too little to judge by meaning
        if len(_CONTENT_WORD.findall(text)) < s.SEMANTIC_MIN_WORDS:
            return []
        self._ensure_ready()
        # capability -> (similarity, margin over the same segment's nearest non-cyber reference, anchor, qualifies)
        best_by_cap: dict[str, tuple[float, float, str, bool]] = {}
        # Long titles list several topics ("TRAINING, AWARENESS, RESEARCH, CYBER DEFENCE…"); one embedding
        # of the whole string dilutes each. Score the full text and each substantial comma/semicolon
        # segment; each capability keeps its best-scoring segment.
        for piece in _segments(_normalise(text[:1000]), s.SEMANTIC_MIN_WORDS):
            v = self._vector(piece)
            sims = self._anchors @ v
            neg = float((self._negatives @ v).max()) if self._negatives is not None else 0.0
            for i, cap_id in enumerate(self._anchor_caps):
                sim = float(sims[i])
                margin = sim - neg
                ok = sim >= s.SEMANTIC_ADJACENT_THRESHOLD and margin >= s.SEMANTIC_NEGATIVE_MARGIN
                prev = best_by_cap.get(cap_id)
                # keep each capability's best *qualifying* segment; a noisy segment that fails the
                # non-cyber margin must never displace one that passes
                if prev is None or (ok, sim) > (prev[3], prev[0]):
                    best_by_cap[cap_id] = (sim, margin, self._anchor_texts[i], ok)
        hits = []
        if not best_by_cap:
            return hits
        qualifying = [(cap_id, v) for cap_id, v in best_by_cap.items() if v[3]]  # filter first, then rank
        for cap_id, (sim, margin, anchor, _) in sorted(qualifying, key=lambda kv: -kv[1][0])[:top_k]:
            kind = "SEMANTIC" if sim >= s.SEMANTIC_STRONG_THRESHOLD else "ADJACENT"
            hits.append(SemanticHit(cap_id, round(sim, 3), anchor, kind, round(margin, 3)))
        return hits


_matcher: SemanticMatcher | None = None
_failed = False


def get_semantic_matcher(catalog: Catalog, settings: Settings) -> SemanticMatcher | None:
    """Process-wide matcher; None when disabled or the model is unavailable (lexicon-only mode)."""
    global _matcher, _failed
    if not settings.SEMANTIC_MATCHING_ENABLED or _failed:
        return None
    if _matcher is None or _matcher.catalog is not catalog:
        candidate = SemanticMatcher(catalog, settings)
        try:
            candidate._ensure_ready()
        except Exception:  # model download/load failure must never stop the pipeline
            log.warning("semantic matching unavailable; continuing with lexicon matching only", exc_info=True)
            _failed = True
            return None
        _matcher = candidate
    return _matcher


def set_semantic_matcher(matcher: SemanticMatcher | None) -> None:
    """For tests and tools: install a specific matcher (or None to reset)."""
    global _matcher, _failed
    _matcher, _failed = matcher, False

