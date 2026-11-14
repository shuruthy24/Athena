"""Report labelling workflow (AGENTS.md Step 3). All rows here are generated test data, not labels."""
import json
import random

import pytest

from athena.llm import LLMReply
from athena.prompts import load, parse
from pipeline.agreement import analyse, clean_reports, cohen_kappa
from pipeline.split import leaks, split, write_split
from pipeline.synth import generate, strict_variants


def test_cohen_kappa_matches_hand_calculation():
    # observed 0.75; chance = (2*1 + 2*3) / 16 = 0.5 -> kappa = (0.75 - 0.5) / 0.5 = 0.5
    assert cohen_kappa(list("yynn"), list("ynnn")) == pytest.approx(0.5)
    assert cohen_kappa(list("abc"), list("abc")) == pytest.approx(1.0)
    assert cohen_kappa(list("aaa"), list("aaa")) is None  # undefined: one label used throughout
    assert cohen_kappa([], []) is None


def _sheet_row(i, a, b, final=("", "")):
    return {"id": f"r-{i}", "street": "Anzac Parade", "band": "22", "text": f"report {i}", "source": "seed",
            "author_group": "team-a", "created_at": "2026-11-15",
            "label_a_action": a[0], "label_a_category": a[1], "label_b_action": b[0], "label_b_category": b[1],
            "final_action": final[0], "final_category": final[1]}


def test_unsettled_disagreements_are_left_out_never_guessed():
    rows = [_sheet_row(1, ("approve", "lighting"), ("approve", "lighting")),
            _sheet_row(2, ("approve", "lighting"), ("review", "other")),
            _sheet_row(3, ("approve", "lighting"), ("review", "other"), final=("review", "other")),
            _sheet_row(4, ("", ""), ("approve", "lighting"))]           # single-labelled: not counted
    summary, disagreements, labelled = analyse(rows)
    assert summary["double_labelled"] == 3 and summary["disagreements"] == 2 and summary["unsettled"] == 1
    assert [r["id"] for r in labelled] == ["r-1", "r-3"]
    assert next(r for r in labelled if r["id"] == "r-3")["action"] == "review"


def test_cleaning_removes_pii_and_rejects_invalid_rows():
    rows = [{**_sheet_row(1, ("approve", "lighting"), ("approve", "lighting")),
             "text": "Lights out, call me on 0412 345 678", "action": "approve", "category": "lighting"},
            {**_sheet_row(2, ("approve", "lighting"), ("approve", "lighting")),
             "band": "23", "text": "dark stairs", "action": "approve", "category": "lighting"}]
    out, stats = clean_reports(rows)
    assert [r["id"] for r in out] == ["r-1"] and "0412" not in out[0]["text"]
    assert stats["pii_redacted_rows"] == 1 and stats["invalid"] == ["r-2"]


def _generated_reports(n=200, seed=1):
    rng = random.Random(seed)
    streets = [f"Street {i}" for i in range(40)]
    rows = []
    for i in range(n):
        rows.append({"id": f"r-{i:04d}", "street": rng.choice(streets), "author_group": f"w-{rng.randrange(25)}",
                     "source": rng.choice(["collected", "seed"]), "text": f"t{i}", "action": "approve",
                     "category": "lighting", "band": "22", "created_at": "2026-11-15"})
    rows += [{**rows[0], "id": f"s-{k}", "source": "synthetic", "street": "Street 0"} for k in range(30)]
    return rows


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_split_never_puts_a_street_or_writer_in_two_splits(seed):
    parts = split(_generated_reports(seed=seed))
    assert leaks(parts) == []
    assert all(r["source"] != "synthetic" for name in ("dev", "test") for r in parts[name])
    assert sum(len(v) for v in parts.values()) == 230


def test_leak_check_catches_a_shared_street():
    parts = {"train": [{"street": "A St", "author_group": "w1"}], "test": [{"street": "A St", "author_group": "w2"}]}
    assert leaks(parts) == ["street 'a st' in train and test"]


def test_split_prefers_collected_reports_for_test():
    rows = [{"id": f"c{i}", "street": "Collected St", "author_group": "outside", "source": "collected"}
            for i in range(10)]
    rows += [{"id": f"t{i}", "street": f"Team St {i}", "author_group": f"team-{i}", "source": "seed"}
             for i in range(40)]
    parts = split(rows)
    assert {r["source"] for r in parts["test"]} == {"collected"}


def test_test_split_is_frozen_by_checksum(tmp_path):
    parts = split(_generated_reports())
    sums = tmp_path / "checksums.txt"
    path = write_split(parts, out_dir=tmp_path, checksums=sums)
    assert sums.read_text().split()[1] == str(path)
    with pytest.raises(FileExistsError):
        write_split(parts, out_dir=tmp_path, checksums=sums)
    write_split(parts, out_dir=tmp_path, checksums=sums, refreeze=True)


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, model, system, user, role=None, **kw):
        self.calls.append(json.loads(user))
        return LLMReply(text=self.replies.pop(0), model=model, ms=1)


def test_synthetic_variants_are_tagged_scrubbed_and_filtered():
    seeds = [{"id": "r-1", "street": "Anzac Parade", "band": "22", "text": "Two lights out by the stairs",
              "category": "lighting", "action": "approve", "source": "seed", "author_group": "team-a",
              "created_at": "2026-11-15"},
             {"id": "r-2", "street": "Oxford Street", "band": "01", "text": "Footpath closed",
              "category": "infrastructure", "action": "approve", "source": "seed", "author_group": "team-b",
              "created_at": "2026-11-15"}]
    llm = FakeLLM([json.dumps({"variants": ["Stair lights broken, ring 0412 345 678", "It is totally safe here"]}),
                   "not json"])
    rows, stats = generate(seeds, llm, "m", n=2)
    assert [r["id"] for r in rows] == ["s-r-1-1"] and rows[0]["source"] == "synthetic"
    assert "0412" not in rows[0]["text"] and rows[0]["author_group"] == "team-a" and rows[0]["action"] == "approve"
    assert stats["calls_used"] == 2 and stats["dropped"] == 1 and stats["malformed"] == 1
    assert llm.calls[0] == {"text": "Two lights out by the stairs", "n": 2}  # report text only as a data field


def test_strict_variant_parser():
    assert strict_variants('{"variants": ["a", " ", "b", "c"]}', 2) == ["a", "b"]
    with pytest.raises(ValueError):
        strict_variants('{"variants": ["a"], "extra": 1}', 2)


def test_prompt_files_load_with_front_matter(tmp_path):
    p = load("synth")
    assert p.version == 1 and "variants" in p.body and len(p.sha) == 12
    (tmp_path / "x.v1.md").write_text("---\nrole: y\nversion: 1\n---\nbody\n")
    with pytest.raises(ValueError):
        load("x", folder=tmp_path)
    assert parse("---\nrole: a\n---\nhi")[0] == {"role": "a"}
