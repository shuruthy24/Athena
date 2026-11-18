"""Privacy: one PII policy for the app and ingest, the landline fix, and the PII evaluation (Data practices)."""
import json
import sys
from pathlib import Path

from athena.guard import inspect_text
from pipeline.clean import remove_pii

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))


def test_bracketed_sydney_landline_is_removed():
    assert "9123" not in inspect_text("my home phone is (02) 9123 4567")[0]
    assert "9123" not in inspect_text("call +61 2 9123 4567 after 6")[0]


def test_numbers_that_are_not_phones_survive():
    for text in ("Bus 891 stops at 11:45 pm", "About 400 m of path", "Call 000 if you are in danger"):
        assert inspect_text(text)[0] == text


def test_app_path_and_ingest_use_the_same_policy():
    text = "I live at 12 Barker Street, see https://photos.example.com/x or mail a@b.co, 0412 345 678"
    assert inspect_text(text)[0] == remove_pii(text)[0]
    red = inspect_text(text)[0]
    assert "Barker" not in red and "photos.example" not in red and "a@b.co" not in red and "0412" not in red


def test_introduced_names_and_student_ids_are_removed():
    assert "Priya Sharma" not in inspect_text("My name is Priya Sharma")[0]
    assert "Tom" not in inspect_text("My friend Tom got hassled")[0]
    assert "z5123456" not in inspect_text("My zID is z5123456")[0]


def test_names_without_an_introduction_are_a_documented_limit():
    # Regex finds names only after an introduction ("my name is", "my friend", "I'm"). A bare name passes, so the
    # report form asks people not to include names and labellers read every report (data/labelling/guide.md).
    assert "Priya Sharma" in inspect_text("Saw Priya Sharma near the station")[0]
    assert inspect_text("Two lights out on Anzac Parade")[0] == "Two lights out on Anzac Parade"


def test_pii_eval_runs_and_the_app_path_removes_at_least_four_in_five_spans(tmp_path, monkeypatch):
    import eval_pii
    monkeypatch.setattr(eval_pii, "OUT", tmp_path / "pii.json")
    assert eval_pii.main() == 0
    res = json.loads((tmp_path / "pii.json").read_text(encoding="utf-8"))["redactors"]
    assert res["none"]["recall"] == 0.0
    assert res["app_guard"]["recall"] >= 0.8
    assert res["app_guard"]["recall_by_kind"]["phone"]["removed"] == res["app_guard"]["recall_by_kind"]["phone"]["n"]
