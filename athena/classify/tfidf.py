"""TF-IDF report classifiers (week 3: compare a classification prompt with a conventional classifier).

Two scikit-learn pipelines, one for the action and one for the category, each over word 1-2 grams and
character 3-5 grams (the feature set that won Tech 5). Logistic regression or LinearSVC, class weights
balanced, seed 3110. Trained on data/reports/train.jsonl plus train_synthetic.jsonl, never dev or test.

  python -m athena.classify.tfidf --kind logreg    # writes models/report_classifier.joblib
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "models" / "report_classifier.joblib"
SEED = 3110


def _pipeline(kind: str):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import FeatureUnion, Pipeline
    from sklearn.svm import LinearSVC
    features = FeatureUnion([
        ("words", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, lowercase=True)),
        ("chars", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=1)),
    ])
    clf = (LogisticRegression(class_weight="balanced", max_iter=2000, random_state=SEED) if kind == "logreg"
           else LinearSVC(class_weight="balanced", random_state=SEED))
    return Pipeline([("features", features), ("clf", clf)])


class _Constant:
    """Used when the train split has a single label for a target, where a classifier cannot be fitted."""

    def __init__(self, label: str):
        self.label = label

    def predict(self, texts):
        return [self.label for _ in texts]


class TfidfClassifier:
    def __init__(self, kind: str = "logreg"):
        if kind not in ("logreg", "svm"):
            raise ValueError("kind must be logreg or svm")
        self.kind = kind
        self.name = f"tfidf-{kind}"
        self.models: dict = {}

    def fit(self, rows: list[dict]) -> "TfidfClassifier":
        if not rows:
            raise ValueError("no training rows")
        texts = [r["text"] for r in rows]
        for target in ("action", "category"):
            labels = [r[target] for r in rows]
            if len(set(labels)) < 2:
                self.models[target] = _Constant(labels[0])
            else:
                self.models[target] = _pipeline(self.kind).fit(texts, labels)
        return self

    def predict(self, text: str, street: str | None = None) -> dict:
        if not self.models:
            raise RuntimeError("classifier is not trained")
        action = str(self.models["action"].predict([text])[0])
        category = str(self.models["category"].predict([text])[0])
        return {"action": action, "category": category, "reason": f"{self.name} prediction"}

    def save(self, path: Path = MODEL_PATH) -> Path:
        import joblib
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"kind": self.kind, "models": self.models}, path)
        return path

    @classmethod
    def load(cls, path: Path = MODEL_PATH, expect: str | None = None) -> "TfidfClassifier":
        import joblib
        if not path.exists():
            raise FileNotFoundError(f"{path} is missing: run `make train` (python -m athena.classify.tfidf)")
        blob = joblib.load(path)
        obj = cls(blob["kind"])
        if expect and obj.name != expect:
            raise ValueError(f"saved model is {obj.name}, but REPORT_CLASSIFIER is {expect}")
        obj.models = blob["models"]
        return obj


def train_rows(reports_dir: Path = ROOT / "data" / "reports") -> list[dict]:
    rows = []
    for name in ("train.jsonl", "train_synthetic.jsonl"):
        p = reports_dir / name
        if p.exists():
            rows += [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["logreg", "svm"], default="logreg")
    args = ap.parse_args(argv)
    rows = train_rows()
    path = TfidfClassifier(args.kind).fit(rows).save()
    print(f"trained tfidf-{args.kind} on {len(rows)} train rows -> {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
