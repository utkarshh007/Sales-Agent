"""Phase 4 — semantic matching, competitor OEMs, and a matching-quality gate."""
from __future__ import annotations

import re

import numpy as np
import pytest

from app.catalog import get_catalog
from app.engine import REJECTED, evaluate
from app.llm.analyzer import RulesAnalyzer
from app.matching import build_matches
from app.semantic import SemanticMatcher, set_semantic_matcher
from tests.conftest import ctx

# A tiny, transparent "embedding": one dimension per concept keyword. Cosine similarity then means
# "shares concepts", which makes threshold behaviour testable without the real model.
CONCEPTS = ["privileged", "administrator", "session", "log", "correlation", "alert", "cctv", "camera",
            "guard", "laptop", "printer", "construction", "leak", "sensitive", "data", "training", "awareness"]


def fake_embed(texts):
    out = []
    for t in texts:
        low = t.lower()
        out.append([float(bool(re.search(rf"\b{c}", low))) for c in CONCEPTS] + [0.05])
    return np.array(out)


@pytest.fixture
def fake_matcher(settings):
    m = SemanticMatcher(get_catalog(), settings, embed=fake_embed)
    set_semantic_matcher(m)
    yield m
    set_semantic_matcher(None)


def tuned(settings, **kw):
    return settings.model_copy(update={"SEMANTIC_STRONG_THRESHOLD": 0.7, "SEMANTIC_ADJACENT_THRESHOLD": 0.5,
                                       "SEMANTIC_NEGATIVE_MARGIN": 0.05, **kw})


def test_semantic_match_finds_paraphrase_the_lexicon_misses(settings, fake_matcher):
    s = tuned(settings)
    title = "Tool for recording administrator session activity on privileged servers"
    assert get_catalog().lexicon_matches(title) == []
    hits = fake_matcher.match(title, settings=s)
    assert hits and hits[0].capability_id == "prd_pam" and hits[0].match_type == "SEMANTIC"


def test_negative_reference_margin_blocks_lookalikes(settings, fake_matcher):
    s = tuned(settings)
    # shares "camera" with nothing cyber but matches the CCTV reference text exactly -> no hit
    assert fake_matcher.match("Supply of CCTV camera systems for parking areas", settings=s) == []


def test_too_few_words_is_not_judged(settings, fake_matcher):
    assert fake_matcher.match("Privileged session logs", settings=tuned(settings)) == []


def test_long_titles_are_scored_by_segment(settings, fake_matcher):
    s = tuned(settings)
    title = ("EMPANELMENT OF VENDORS FOR SECURITY AWARENESS AND TRAINING OF STAFF, CITIZEN OUTREACH EVENTS, "
             "RESEARCH GRANTS, INNOVATION CHALLENGE")
    assert any(h.capability_id in ("ir_capacity_building", "df_training") for h in fake_matcher.match(title, settings=s))


def test_semantic_only_tender_is_surfaced_with_explanation(settings, fake_matcher):
    s = tuned(settings)
    d = evaluate(ctx("Tool for recording administrator session activity on privileged servers"),
                 get_catalog(), s, RulesAnalyzer(s, get_catalog()))
    assert d.status != REJECTED
    top = d.matches[0]
    assert top.capability_id == "prd_pam" and top.source == "EMBEDDING" and "similarity" in top.explanation


def test_exclusions_veto_semantic_matches(settings, fake_matcher):
    s = tuned(settings)
    d = evaluate(ctx("Hiring of security guards for privileged areas and administrator block"),
                 get_catalog(), s, RulesAnalyzer(s, get_catalog()))
    assert d.status == REJECTED and not d.prefilter.semantic_hits


def test_semantic_is_fallback_only_and_cannot_reclassify(settings, fake_matcher):
    """A red-team service must stay a service even if embeddings also resemble simulation tools."""
    s = tuned(settings)
    d = evaluate(ctx("Engagement of red team for annual adversary simulation", value=4_500_000),
                 get_catalog(), s, RulesAnalyzer(s, get_catalog()))
    assert d.opportunity_type == "SERVICE" and d.status == REJECTED and d.prefilter.semantic_hits == []


# ------------------------------------------------------------------ competitor OEMs
def test_competitor_oem_is_a_functional_requirement_and_a_risk(settings):
    set_semantic_matcher(None)
    s = settings.model_copy(update={"SEMANTIC_MATCHING_ENABLED": False})
    cat = get_catalog()
    d = evaluate(ctx("Supply of CyberArk or equivalent solution for 600 privileged users", value=30_000_000),
                 cat, s, RulesAnalyzer(s, cat))
    pam = next(m for m in d.matches if m.capability_id == "prd_pam")
    assert "CyberArk" in pam.explanation and "equivalent" in pam.explanation
    assert "NON_PORTFOLIO_OEM_SPECIFIED" in d.flags
    assert d.opportunity_type == "OEM" and d.status != REJECTED
    assert any("CyberArk" in r for r in d.analysis.risks)


def test_competitor_without_equivalent_clause_is_flagged_as_brand_risk(settings):
    s = settings.model_copy(update={"SEMANTIC_MATCHING_ENABLED": False})
    cat = get_catalog()
    hits = cat.competitor_mentions("Renewal of CrowdStrike Falcon subscription for 2000 endpoints")
    assert [(h.capability_id, h.oem, h.or_equivalent) for h in hits] == [("prd_xdr", "CrowdStrike", False)]
    m = build_matches(cat, [], [], None, [], hits)[0]
    assert "check whether the brand is mandatory" in m.explanation and m.confidence < 80


def test_match_sources_are_labelled_correctly_when_merged(settings, fake_matcher):
    cat = get_catalog()
    lex = cat.lexicon_matches("Procurement of privileged access management solution")
    sem = fake_matcher.match("Procurement of privileged access management solution for administrator sessions",
                             settings=tuned(settings))
    merged = build_matches(cat, lex, [], None, sem, [])
    pam = next(m for m in merged if m.capability_id == "prd_pam")
    assert pam.source == "LEXICON+EMBEDDING"


# ------------------------------------------------------------------ quality gate on the real model
@pytest.fixture(scope="module")
def real_model():
    from app.config import Settings
    from app.semantic import get_semantic_matcher

    set_semantic_matcher(None)
    s = Settings(_env_file=None, ANTHROPIC_API_KEY="", LLM_ENABLED=False)
    if get_semantic_matcher(get_catalog(), s) is None:
        pytest.skip("embedding model unavailable (offline); quality gate not run")
    return s


@pytest.mark.parametrize("split, min_precision, min_recall", [
    ("dev", 0.95, 0.95), ("holdout", 0.95, 0.90), ("holdout2", 0.95, 0.90),
])
def test_matching_quality_gate(real_model, split, min_precision, min_recall):
    """Fails if a catalog or engine change makes screening noticeably worse on labelled data."""
    from app.evaluation.run import run

    r = run(split, real_model)
    assert r.precision >= min_precision, r.summary() + "\n" + "\n".join(r.false_positives)
    assert r.recall >= min_recall, r.summary() + "\n" + "\n".join(r.false_negatives)
    assert r.capability_rate >= 0.9, r.summary()
