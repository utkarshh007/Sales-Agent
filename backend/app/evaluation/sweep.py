"""Threshold sweep for semantic matching on the DEV split only (holdout stays untouched).

    python -m app.evaluation.sweep

Ranks settings by F0.5 (precision weighted above recall, per "precision > volume"), and only among
settings whose recall is at least 85%.
"""
from __future__ import annotations

import itertools
import sys

from app.config import get_settings
from app.evaluation.run import run


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    base = get_settings()
    rows = []
    for strong, adjacent, margin in itertools.product((0.78, 0.80, 0.82, 0.84), (0.74, 0.76, 0.78, 0.80), (0.02, 0.04, 0.06, 0.08)):
        if adjacent > strong:
            continue
        s = base.model_copy(update={"SEMANTIC_STRONG_THRESHOLD": strong, "SEMANTIC_ADJACENT_THRESHOLD": adjacent,
                                    "SEMANTIC_NEGATIVE_MARGIN": margin})
        r = run("dev", s)
        p, rc = r.precision, r.recall
        f05 = (1.25 * p * rc / (0.25 * p + rc)) if p + rc else 0.0
        rows.append((f05, p, rc, r.screen_recall, r.fp, r.fn, strong, adjacent, margin))
    rows.sort(key=lambda x: (x[2] >= 0.85, x[0]), reverse=True)
    print("F0.5   prec  recall screen  FP FN | strong adjacent margin")
    for f05, p, rc, sr, fp, fn, st, ad, mg in rows[:12]:
        print(f"{f05:.3f} {p:5.0%} {rc:6.0%} {sr:6.0%} {fp:3d} {fn:2d} | {st:.2f}   {ad:.2f}     {mg:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
