"""Metric helpers shared by the eval scripts (standard library only, no scikit-learn needed at runtime)."""
from __future__ import annotations

import statistics

ADMIT = {"approve", "redact"}   # the report is allowed to change route risk
HOLD = {"review", "reject"}     # it is not (a wrongly admitted report is the severe error)


def rate(num: int, den: int) -> float | None:
    return None if den == 0 else round(num / den, 4)


def wilson(num: int, den: int, z: float = 1.96) -> list[float] | None:
    """95% Wilson score interval for a proportion. Our eval sets are small (tens of cases), so every rate in the
    evaluation report carries this interval: 16/16 is [0.81, 1.0], not "100%"."""
    if den == 0:
        return None
    p = num / den
    centre = (p + z * z / (2 * den)) / (1 + z * z / den)
    half = z * ((p * (1 - p) / den + z * z / (4 * den * den)) ** 0.5) / (1 + z * z / den)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def binary_scores(truth: list[bool], pred: list[bool]) -> dict:
    """Precision, recall and false-positive rate (positive class = True) with Wilson intervals and counts."""
    tp = sum(t and p for t, p in zip(truth, pred))
    fp = sum((not t) and p for t, p in zip(truth, pred))
    fn = sum(t and not p for t, p in zip(truth, pred))
    tn = sum((not t) and not p for t, p in zip(truth, pred))
    return {"n": len(truth), "positives": tp + fn, "negatives": tn + fp, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": rate(tp, tp + fp), "precision_ci95": wilson(tp, tp + fp),
            "recall": rate(tp, tp + fn), "recall_ci95": wilson(tp, tp + fn),
            "false_positive_rate": rate(fp, fp + tn), "false_positive_rate_ci95": wilson(fp, fp + tn)}


def tpr_tnr(truth: list[bool], pred: list[bool | None]) -> dict:
    """Positive class = True. A None prediction (malformed output) stays in the denominator and counts as
    wrong."""
    tp = sum(1 for t, p in zip(truth, pred) if t and p is True)
    fn = sum(1 for t, p in zip(truth, pred) if t and p is not True)
    tn = sum(1 for t, p in zip(truth, pred) if not t and p is False)
    fp = sum(1 for t, p in zip(truth, pred) if not t and p is not False)
    return {"tpr": rate(tp, tp + fn), "tnr": rate(tn, tn + fp), "tp": tp, "fn": fn, "tn": tn, "fp": fp,
            "malformed": sum(p is None for p in pred)}


def macro_f1(truth: list[str], pred: list[str | None], labels: list[str]) -> float | None:
    """Mean F1 over the labels that appear in truth or predictions; None predictions are wrong."""
    present = [lab for lab in labels if lab in truth or lab in pred]
    if not present:
        return None
    scores = []
    for lab in present:
        tp = sum(1 for t, p in zip(truth, pred) if t == lab and p == lab)
        fp = sum(1 for t, p in zip(truth, pred) if t != lab and p == lab)
        fn = sum(1 for t, p in zip(truth, pred) if t == lab and p != lab)
        scores.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return round(sum(scores) / len(scores), 4)


def latency_summary(ms: list[float]) -> dict:
    if not ms:
        return {"n": 0, "mean_ms": None, "p50_ms": None, "p95_ms": None}
    s = sorted(ms)
    return {"n": len(s), "mean_ms": round(statistics.mean(s)), "p50_ms": round(statistics.median(s)),
            "p95_ms": round(s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))])}


class CallBudget:
    """Hard cap on model calls for one script run. Raises before the call that would exceed it."""

    def __init__(self, llm, limit: int):
        self.llm, self.limit, self.used = llm, limit, 0

    def __getattr__(self, name):  # provider, models(), ... pass through to the wrapped client
        return getattr(self.llm, name)

    def chat(self, *args, **kwargs):
        if self.used >= self.limit:
            raise BudgetExceeded(f"call budget of {self.limit} reached")
        self.used += 1
        return self.llm.chat(*args, **kwargs)


class BudgetExceeded(Exception):
    """Deliberately not a RuntimeError, so no classifier or client error handler can swallow it: a spent
    budget always stops the run instead of turning into 'malformed' predictions."""
