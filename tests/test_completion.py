"""Completion: the submitted code can be checked and verified by someone who only has the zip and the README."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import scan_secrets  # noqa: E402
import verify_demo  # noqa: E402


def test_secrets_scan_walks_the_folder_when_it_is_not_a_git_checkout(tmp_path):
    for rel in ("athena/app.py", ".env.example", ".env", ".venv/lib/x.py", "data/raw/osm.json", "runs/t.jsonl",
                "data/processed/streets.parquet", "web/__pycache__/x.pyc"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    files = scan_secrets.walked_files(tmp_path)
    assert {"athena/app.py", ".env.example", "data/processed/streets.parquet"} <= set(files)
    assert not {".env", ".venv/lib/x.py", "data/raw/osm.json", "runs/t.jsonl", "web/__pycache__/x.pyc"} & set(files)


def test_secrets_scan_still_flags_a_key_in_a_walked_file():
    assert scan_secrets.scan_text('API_KEY = "sk-' + "a" * 30 + '"')
    assert not scan_secrets.scan_text("COMP3110_API_KEY=YOUR_VIRTUAL_KEY")


def test_verify_demo_passes_every_feature_offline(tmp_path):
    out = tmp_path / "verify.json"
    assert verify_demo.main(["--out", str(out)]) == 0
    res = json.loads(out.read_text(encoding="utf-8"))
    assert res["summary"]["FAIL"] == 0 and res["summary"]["PASS"] >= 15
    features = {r["feature"].split()[0] for r in res["checks"]}
    assert {"F0", "F1", "F2", "F3", "F4", "F5", "F6", "F7"} <= features
    assert res["config"]["data_provenance"]["mode"] in ("sample", "processed")


def test_verify_demo_flags_banned_wording_but_not_its_negation():
    assert verify_demo.banned_words(["This route is safe."]) == ["This route is safe."]
    assert verify_demo.banned_words(["Athena never calls a route safe."]) == []


def test_every_text_file_read_and_write_names_utf8():
    """Windows Python 3.12 defaults to cp1252: a bare read_text()/write_text()/open() garbles or crashes on the
    non-ASCII data (help-location names, arrows in labels, © in attributions). Every text I/O call must say UTF-8."""
    import ast
    bad = []
    for folder in ("athena", "pipeline", "evals", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
                if name not in ("read_text", "write_text", "open"):
                    continue
                if any(k.arg == "encoding" for k in node.keywords):
                    continue
                modes = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                modes += [k.value.value for k in node.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)]
                if any("b" in m for m in modes if len(m) <= 3):
                    continue
                if name == "open" and isinstance(node.func, ast.Attribute) and node.func.attr == "open" \
                        and isinstance(node.func.value, ast.Name) and node.func.value.id in ("tarfile", "zipfile"):
                    continue
                bad.append(f"{path.relative_to(ROOT)}:{node.lineno} {name}()")
    assert not bad, bad
