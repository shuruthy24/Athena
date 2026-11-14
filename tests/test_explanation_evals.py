"""Step 6: traces, assertions (one pass and one fail each), the strict judge and its audit, and the blind A/B.
Traces here come from the offline stand-in and fake judges: they test the workflow, not the models."""
import copy
import csv
import json

import pytest

import evals.assertions as A
import evals.judge as J
from athena.agents import Athena
from athena.config import get_settings
from athena.data.source import LocalSource
from athena.llm import LLMReply, SampleLLM
from evals import ab
from scripts import run_traces


@pytest.fixture(scope="module")
def traces(tmp_path_factory):
    import athena.analysis as analysis
    import athena.telemetry as telemetry
    out = tmp_path_factory.mktemp("traces")
    mp = pytest.MonkeyPatch()  # module scope: isolate the report queue and runs folder here too
    mp.setenv("SAMPLE_MODE", "true")
    mp.setattr(analysis, "REPORT_QUEUE", out.parent / "queue.jsonl")
    mp.setattr(telemetry, "RUNS", out.parent / "runs")
    recs = run_traces.run(Athena(get_settings(), SampleLLM(), LocalSource()), LocalSource(), out)
    yield out, recs
    mp.undo()


def test_run_traces_saves_everything_the_evals_need(traces):
    out, recs = traces
    assert len(recs) == 36 and len(list(out.glob("*.json"))) == 36
    assert all(r["critic_verdict"] for r in recs)  # every case produced an explanation
    r = recs[0]
    assert {"trace_id", "case", "facts", "explanation", "fact_only", "trace", "usage", "critic_verdict"} <= set(r)
    assert r["models"] == ["sample-offline-stub"]


def test_analysis_sheet_rows_keep_human_columns_empty(tmp_path):
    sheet = tmp_path / "analysis.csv"
    run_traces.add_to_analysis(["t1", "t2"], sheet)
    run_traces.add_to_analysis(["t2", "t3"], sheet)
    rows = list(csv.DictReader(sheet.open()))
    assert [r["trace_id"] for r in rows] == ["t1", "t2", "t3"]
    assert all(not r["vibe_verdict"] and not r["failure_category"] for r in rows)


# ---------------------------------------------------------------- assertions: one pass, one fail each
def good(traces):
    return copy.deepcopy(next(r for r in traces[1] if len(r["explanation"]["claims"]) >= 2
                              and (r["facts"].get("risk_reduction_pct") or 0) > 0))


def with_claim(rec, text, facts):
    rec["explanation"]["claims"] = [{"text": text, "facts": facts}]
    return rec


@pytest.mark.parametrize("assertion,breaker", [
    (A.claims_cite_known_facts, lambda r: with_claim(r, "It is lit.", [])),
    (A.numbers_match_facts, lambda r: with_claim(r, "It takes 999 min.", ["athena.duration_min"])),
    (A.no_banned_wording, lambda r: with_claim(r, "This route is safe.", ["extra_min"])),
    (A.streets_on_routes, lambda r: with_claim(r, "It avoids Chalmers Street.", ["avoided_streets"])),
    (A.direction_correct, lambda r: with_claim(r, "The fastest route has lower risk.", ["risk_reduction_pct"])),
    (A.claim_count, lambda r: with_claim(r, "x", ["extra_min"]) and r["explanation"].update(claims=[]) or r),
    (A.model_steps_observable, lambda r: [s.pop("tokens_in", None) for s in r["trace"] if s.get("model")] and r),
])
def test_each_assertion_passes_on_good_and_fails_on_broken(traces, assertion, breaker):
    rec = good(traces)
    assert assertion(rec).passed, assertion(rec).detail
    assert not assertion(breaker(copy.deepcopy(rec))).passed


def test_unknown_lighting_must_be_disclosed(traces):
    rec = good(traces)
    rec["facts"]["athena.unknown_share_pct"] = 40.0
    rec["explanation"]["claims"] = [{"text": "Athena's route takes 2 min longer.", "facts": ["extra_min"]}]
    assert not A.unknown_lighting_disclosed(rec).passed
    rec["explanation"]["claims"].append({"text": "Part of the route has unknown lighting.", "facts": ["athena.unknown_share_pct"]})
    assert A.unknown_lighting_disclosed(rec).passed


def test_run_all_counts(traces):
    res = A.run_all(traces[1])
    assert res["traces"] == 36 and set(res["assertions"]) == {a.__name__ for a in A.ASSERTIONS}


# ---------------------------------------------------------------- judge contract and audit
@pytest.mark.parametrize("raw", [
    '```json\n{"verdict": "PASS", "reason": "ok"}\n```', 'PASS', '{"verdict": "PASS"}',
    '{"verdict": "PASS", "reason": "ok", "score": 1}', '{"verdict": "pass", "reason": "ok"}',
    '{"verdict": "PASS", "reason": "  "}', '{"verdict": "PASS", "verdict": "FAIL", "reason": "x"}'])
def test_judge_rejects_anything_outside_the_contract(raw):
    with pytest.raises(J.JudgeOutputError):
        J.parse_binary(raw)


def test_judge_accepts_the_contract():
    assert J.parse_binary('{"verdict": "FAIL", "reason": "number differs"}')["verdict"] == "FAIL"
    assert J.parse_pairwise('{"verdict": "TIE", "reason": "same"}')["verdict"] == "TIE"


def test_judge_metrics_count_malformed_as_wrong():
    m = J.binary_metrics(["PASS", "PASS", "FAIL", "FAIL"], ["PASS", None, "FAIL", "PASS"])
    assert (m["tpr"], m["tnr"], m["malformed"], m["false_passes"], m["false_fails"]) == (0.5, 0.5, 1, 1, 0)


def test_judge_never_sees_the_label(traces):
    rec = traces[1][0]
    msg = json.loads(J.binary_user_message(rec["trace_id"], rec["explanation"], rec["facts"]))
    assert set(msg) == {"case_id", "explanation", "facts"}


class FakeJudge:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def chat(self, model, system, user, role=None, **kw):
        self.calls += 1
        return LLMReply(text=self.replies.pop(0), model=model, ms=1)


def test_audit_reads_labels_after_predictions_and_respects_budget(traces, tmp_path):
    out, recs = traces
    ids = sorted(r["trace_id"] for r in recs[:4])  # the audit judges held-out cases in id order
    (tmp_path / "heldout.json").write_text(json.dumps(dict(zip(ids, ["PASS", "PASS", "FAIL", "FAIL"]))))
    replies = ['{"verdict": "PASS", "reason": "a"}', "nonsense", '{"verdict": "FAIL", "reason": "c"}',
               '{"verdict": "PASS", "reason": "d"}']
    res = J.audit(FakeJudge(replies), "judge-m", out, labels_dir=tmp_path, budget=60)
    b = res["binary"]
    assert res["calls_used"] == 4 and (b["tpr"], b["tnr"], b["malformed"]) == (0.5, 0.5, 1)
    assert all("human" in o for o in b["outcomes"])
    with pytest.raises(RuntimeError):
        J.audit(FakeJudge(replies), "judge-m", out, labels_dir=tmp_path, budget=2)


# ---------------------------------------------------------------- blind A/B
@pytest.fixture()
def ab_dir(traces, tmp_path):
    src = tmp_path / "traces"
    src.mkdir()
    for i, r in enumerate(traces[1][:10]):
        r = copy.deepcopy(r)
        r["explanation"]["claims"] = [{"text": f"Model wording {i}.", "facts": ["extra_min"]}]
        (src / f"{r['trace_id']}.json").write_text(json.dumps(r))
    out = tmp_path / "ab"
    info = ab.prepare(src, n=4, out=out)
    assert info["presentations"] == 4
    return out


def fill(out, prefs):
    rows = list(csv.DictReader((out / "choices.csv").open()))
    with (out / "choices.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["presentation_id", "preference", "rationale"])
        w.writeheader()
        for r, p in zip(rows, prefs):
            w.writerow({"presentation_id": r["presentation_id"], "preference": p, "rationale": "cites the facts" if p else ""})


def test_review_sheet_is_blind(ab_dir):
    sheet = (ab_dir / "review.md").read_text()
    assert "llm" not in sheet.lower().replace("mapping", "") and "fact_only" not in sheet
    mapping = json.loads((ab_dir / "mapping.json").read_text())
    assert {m["A"] for m in mapping.values()} <= {"llm", "fact_only"} and len(mapping) == 4


def test_reveal_refuses_incomplete_choices_then_aggregates(ab_dir):
    fill(ab_dir, ["A", "", "B", "TIE"])
    with pytest.raises(ValueError):
        ab.reveal(ab_dir, require_commit=False)
    fill(ab_dir, ["A", "A", "B", "TIE"])
    res = ab.reveal(ab_dir, require_commit=False)
    h = res["human"]
    assert h["llm"] + h["fact_only"] + h["tie"] == 4 and h["tie"] == 1


def test_reveal_detects_a_changed_mapping(ab_dir):
    fill(ab_dir, ["A", "A", "B", "TIE"])
    m = json.loads((ab_dir / "mapping.json").read_text())
    first = sorted(m)[0]
    m[first]["A"], m[first]["B"] = m[first]["B"], m[first]["A"]
    (ab_dir / "mapping.json").write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
    with pytest.raises(ValueError):
        ab.reveal(ab_dir, require_commit=False)


def test_reveal_with_pairwise_judge(ab_dir):
    fill(ab_dir, ["A", "A", "A", "A"])
    judge = FakeJudge(['{"verdict": "A", "reason": "x"}'] * 3 + ["oops"])
    res = ab.reveal(ab_dir, judge_llm=judge, judge_model="j", require_commit=False)
    rows = res["judge"]["rows"]
    assert res["calls_used"] == 4 and rows[-1]["verdict"] is None and rows[-1]["error"]
    assert res["judge"]["agrees_with_humans"] == 3


def test_reveal_requires_committed_choices(ab_dir):
    fill(ab_dir, ["A", "A", "B", "TIE"])
    with pytest.raises(PermissionError):
        ab.reveal(ab_dir)
