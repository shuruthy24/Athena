"""The labelling helper: the held-out split is fixed before labelling, labels land where the judge reads them,
and each report labeller sees only their own columns."""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))

import label  # noqa: E402

RUN = ROOT / "evals" / "traces" / "2026-11-14-live-scx-gemma-4-31B-it"


def _answers(seq):
    it = iter(seq)
    return lambda prompt="": next(it)


def test_assignment_is_seeded_disjoint_and_written_before_labels(tmp_path):
    ids = [f"t{i:02d}" for i in range(36)]
    a, b = label.assign(ids, 20), label.assign(list(reversed(ids)), 20)
    assert a["heldout"] == b["heldout"] and len(a["heldout"]) == 18
    assert not set(a["heldout"]) & set(a["dev"]) and set(a["heldout"]) | set(a["dev"]) == set(ids)


def test_trace_labels_resume_and_use_the_judge_file_shape(tmp_path):
    shown = []
    counts = label.label_traces(RUN, ask=_answers(["p", "x", "f", "q"]), out=shown.append, labels=tmp_path)
    assert (tmp_path / "assignment.json").exists()
    assert sum(counts.values()) == 2
    dev = json.loads((tmp_path / "dev.json").read_text(encoding="utf-8"))
    assert set(dev.values()) <= {"PASS", "FAIL"}
    assert "problems" not in shown[0] and "verdict" not in shown[0]        # no critic output on screen
    label.label_traces(RUN, ask=_answers(["q"]), out=shown.append, labels=tmp_path)
    assert json.loads((tmp_path / "dev.json").read_text(encoding="utf-8")) == dev            # resumes, keeps earlier labels


def test_report_labeller_sees_only_their_own_columns(tmp_path):
    sheet = tmp_path / "sheet.csv"
    shutil.copy(ROOT / "data" / "labelling" / "reports_to_label.csv", sheet)
    label.add_reports("team-1", "seed", ask=_answers(["Two lights out near the bus stop", "Anzac Parade", "22", ""]),
                      out=lambda *_: None, path=sheet)
    fields, rows = label.read_sheet(sheet)
    rows[0]["label_a_action"], rows[0]["label_a_category"] = "review", "infrastructure"
    label.write_sheet(fields, rows, sheet)
    shown = []
    assert label.label_reports("b", ask=_answers(["a", "1"]), out=shown.append, path=sheet) == 1
    _, rows = label.read_sheet(sheet)
    assert rows[0]["label_b_action"] == "approve" and rows[0]["label_b_category"] == "lighting"
    assert "infrastructure" not in shown[0]                                  # labeller a's answer never shown
    assert rows[0]["source"] == "seed" and rows[0]["author_group"] == "team-1"


def test_user_study_analysis_drops_rows_without_consent_and_compares_conditions():
    import eval_user_study as us
    base = {"participant_group": "p1", "consent": "yes", "trip": "unsw|central", "band": "01"}
    rows = [{**base, "condition": "baseline", "chosen": "fastest", "lower_risk_extra_min": "3", "understood_not_safe": ""},
            {**base, "condition": "athena", "chosen": "lower_risk", "lower_risk_extra_min": "3", "understood_not_safe": "yes"},
            {**base, "participant_group": "p2", "consent": "no", "condition": "athena", "chosen": "lower_risk",
             "lower_risk_extra_min": "9", "understood_not_safe": "no"}]
    res = us.analyse(rows)
    assert res["participants"] == 1 and res["tasks"] == 2
    assert res["conditions"]["baseline"]["lower_risk_choice_share"] == 0.0
    assert res["conditions"]["athena"]["lower_risk_choice_share"] == 1.0
    assert res["conditions"]["athena"]["misreading_rate"] == 0.0
