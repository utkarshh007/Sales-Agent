"""Matching-quality evaluation (Phase 4).

    python -m app.evaluation.run                 # dev split, rules-only engine
    python -m app.evaluation.run --split holdout
    python -m app.evaluation.run --errors        # list every false positive / false negative

Measures, on labelled cases plus ~2,700 real non-cyber titles from the live portals:
  * title-screen recall  — share of relevant tenders whose title alone earns a detail/document fetch
  * surfaced precision / recall — relevant = shown to the team (accepted or sent to review)
  * capability hit rate — of surfaced relevant tenders, how many map to an expected capability

Runs the real engine on title-level evidence (the hardest case: no detail page, no documents). With an
ANTHROPIC_API_KEY set and --llm, the same cases go through the LLM analyser (costs API credits).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import yaml

from app.analysis import TenderContext
from app.catalog import get_catalog
from app.config import Settings, get_settings
from app.db import utcnow
from app.engine import REJECTED, evaluate
from app.llm.analyzer import RulesAnalyzer, build_analyzer
from app.rules.prefilter import title_is_candidate

HERE = Path(__file__).parent


@dataclass
class Case:
    text: str
    label: str
    expect: list[str]
    split: str
    source: str


@dataclass
class Report:
    split: str
    n_relevant: int = 0
    n_irrelevant: int = 0
    screened_in: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    capability_hits: int = 0
    false_positives: list[str] = field(default_factory=list)
    false_negatives: list[str] = field(default_factory=list)
    wrong_capability: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        return self.tp / self.n_relevant if self.n_relevant else 1.0

    @property
    def screen_recall(self) -> float:
        return self.screened_in / self.n_relevant if self.n_relevant else 1.0

    @property
    def capability_rate(self) -> float:
        return self.capability_hits / self.tp if self.tp else 1.0

    def summary(self) -> str:
        return (f"[{self.split}] relevant={self.n_relevant} irrelevant={self.n_irrelevant} | "
                f"title-screen recall {self.screen_recall:.0%} | surfaced precision {self.precision:.0%} "
                f"recall {self.recall:.0%} (TP {self.tp}, FP {self.fp}, FN {self.fn}) | "
                f"capability hit {self.capability_rate:.0%}")


def load_cases(split: str = "dev") -> list[Case]:
    data = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))["cases"]
    cases = [Case(c["text"], c["label"], list(c.get("expect", [])), c["split"], c["source"]) for c in data]
    for title in json.loads((HERE / "live_negative_titles.json").read_text(encoding="utf-8")):
        # deterministic split so the same title is always in the same half
        half = "dev" if int(hashlib.sha1(title.encode()).hexdigest(), 16) % 2 == 0 else "holdout"
        cases.append(Case(title, "IRRELEVANT", [], half, "live"))
    return [c for c in cases if split == "all" or c.split == split]


def run(split: str = "dev", settings: Settings | None = None, analyzer=None) -> Report:
    settings = settings or get_settings()
    catalog = get_catalog()
    analyzer = analyzer or RulesAnalyzer(settings, catalog)
    report = Report(split)
    closing = utcnow() + timedelta(days=21)
    for case in load_cases(split):
        if case.label == "EITHER":
            continue
        ctx = TenderContext(title=case.text, closing_at=closing, organization=None)
        decision = evaluate(ctx, catalog, settings, analyzer)
        surfaced = decision.status != REJECTED
        if case.label == "RELEVANT":
            report.n_relevant += 1
            report.screened_in += title_is_candidate(case.text, closing, catalog)
            if surfaced:
                report.tp += 1
                caps = {m.capability_id for m in decision.matches}
                if caps & set(case.expect):
                    report.capability_hits += 1
                else:
                    report.wrong_capability.append(f"{case.text[:90]}  -> got {sorted(caps)[:4]}, expected {case.expect}")
            else:
                report.fn += 1
                report.false_negatives.append(f"{case.text[:110]}  ({decision.primary_reason[:80]})")
        else:
            report.n_irrelevant += 1
            if surfaced:
                report.fp += 1
                top = decision.matches[0] if decision.matches else None
                report.false_positives.append(
                    f"{case.text[:110]}  -> {top.capability_id if top else '?'} {top.match_type if top else ''}")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.evaluation.run")
    ap.add_argument("--split", choices=["dev", "holdout", "holdout2", "all"], default="dev")
    ap.add_argument("--errors", action="store_true")
    ap.add_argument("--llm", action="store_true", help="use the configured LLM analyser (costs API credits)")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    settings = get_settings()
    analyzer = build_analyzer(settings, get_catalog()) if args.llm else None
    r = run(args.split, settings, analyzer)
    print(r.summary())
    if args.errors:
        for title, rows in (("False negatives", r.false_negatives), ("False positives", r.false_positives),
                            ("Surfaced with an unexpected capability", r.wrong_capability)):
            print(f"\n{title} ({len(rows)}):")
            for row in rows:
                print("  -", row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
