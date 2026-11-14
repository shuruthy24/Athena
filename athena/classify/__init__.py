"""Street-report classifiers behind one interface (AGENTS.md Step 5, section 6).

Candidates, from simplest to most expensive (week 1: rule before ML; week 3: compare a classification
prompt with a conventional classifier):
  majority        always the most common train labels (the floor every candidate must beat)
  keyword         hand-written rules, no training
  tfidf-logreg    TF-IDF + logistic regression (scikit-learn), trained on the train split
  tfidf-svm       TF-IDF + LinearSVC (won Tech 5)
  prompt-zero     moderator.v2 prompt, no examples
  prompt-5shot    moderator.v2 prompt with 5+ examples from the train split only

Every classifier returns {"action": approve|review|reject, "category": ..., "reason": ...}. Failures raise
ClassifierError; the app then forces human review. REPORT_CLASSIFIER picks the one the app uses; the default
"moderator-v1" keeps the hackathon moderator until the team chooses from evals/results/classifier_dev.json.
"""
from __future__ import annotations

from typing import Protocol

ACTIONS = ("approve", "review", "reject")
CATEGORIES = ("lighting", "isolation", "harassment", "infrastructure", "positive", "other")
CHOICES = ("moderator-v1", "majority", "keyword", "tfidf-logreg", "tfidf-svm", "prompt-zero", "prompt-5shot")


class ClassifierError(RuntimeError):
    pass


class ReportClassifier(Protocol):
    name: str

    def predict(self, text: str, street: str | None) -> dict:  # {"action", "category", "reason"}
        ...


def build_classifier(name: str, llm=None, model: str | None = None, train_rows: list[dict] | None = None):
    """The app's classifier. Trained ones load their saved model; prompted ones need the gateway client."""
    from .baseline import KeywordClassifier, MajorityClassifier
    if name == "keyword":
        return KeywordClassifier()
    if name == "majority":
        return MajorityClassifier().fit(train_rows or [])
    if name in ("tfidf-logreg", "tfidf-svm"):
        from .tfidf import MODEL_PATH, TfidfClassifier
        return TfidfClassifier.load(MODEL_PATH, expect=name)
    if name in ("prompt-zero", "prompt-5shot"):
        from .prompted import PromptClassifier
        return PromptClassifier(llm, model, shots=0 if name == "prompt-zero" else 5, train_rows=train_rows or [])
    raise ValueError(f"REPORT_CLASSIFIER must be one of {', '.join(CHOICES)}")
