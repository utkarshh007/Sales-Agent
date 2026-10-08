from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_bootstrap.db")
os.environ["ANTHROPIC_API_KEY"] = ""  # tests never call the real API

from app.analysis import Analysis, Component, Requirement, TenderContext  # noqa: E402
from app.catalog import get_catalog  # noqa: E402
from app.config import Settings  # noqa: E402

NOW = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, ANTHROPIC_API_KEY="", LLM_ENABLED=False, SECRET_KEY="x" * 40)


@pytest.fixture
def catalog():
    return get_catalog()


def ctx(title: str, text: str = "", org: str = "Ministry of Electronics and IT", days_to_close: int = 30,
        value: int | None = None) -> TenderContext:
    return TenderContext(
        title=title, organization=org, closing_at=NOW + timedelta(days=days_to_close), published_at=NOW,
        portal_value_inr=value, document_text=text, llm_context=text, has_documents=bool(text),
    )


class StubAnalyzer:
    """Stands in for the LLM: returns a preset Analysis and records whether it was called."""

    def __init__(self, analysis: Analysis):
        self.analysis = analysis
        self.calls = 0

    def analyze(self, ctx, hints):
        self.calls += 1
        return self.analysis


def llm_analysis(requirements: list[Requirement], components: list[Component], total: int | None = None,
                 eligibility: str = "FEASIBLE") -> Analysis:
    return Analysis(mode="LLM", model_version="stub", requirements=requirements, components=components,
                    total_value_inr=total, eligibility_assessment=eligibility, strategic_relevance="HIGH")
