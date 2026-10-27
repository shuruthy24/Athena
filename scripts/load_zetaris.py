"""Register Athena's tables in Zetaris and verify them. Run on the machine where Zetaris runs.

Zetaris cannot read files on your laptop, but it can read a URL (the Setup Guide's PokeAPI recipe).
So each data/*.json file is registered as a Lightning REST table and flattened into a schemastore view.

  python3 scripts/load_zetaris.py --base-url https://raw.githubusercontent.com/<you>/athena-agent/main/data
  python3 scripts/load_zetaris.py --base-url http://<docker-gateway>:8000      # local fallback, see README
  python3 scripts/load_zetaris.py --inline                                     # fallback: inline VALUES views
  python3 scripts/load_zetaris.py --check                                      # only verify row counts

Writes docs/evidence/zetaris_load.txt with what ran and the row counts Zetaris returned.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from athena.config import get_settings  # noqa: E402
from athena.data.zetaris import ZetarisClient, ZetarisError  # noqa: E402

TABLES = {
    "streets": ["id", "name", "highway", "lit", "tunnel", "risk_18", "risk_22", "risk_01",
                "venues_18", "venues_22", "venues_01", "lights_per_100m"],
    "route_steps": ["trip", "origin", "destination", "band", "mode", "variant", "duration_min", "walk_m",
                    "transfers", "leg", "leg_mode", "line", "step", "street", "distance_m"],
    "places": ["id", "name", "area", "lat", "lon"],
    "reports": ["id", "street", "band", "text", "expected_action", "expected_reason", "synthetic"],
}
DB = "ATHENA_REST"


def rest_statements(base_url: str, schema: str) -> list[str]:
    out = [f'CREATE LIGHTNING DATABASE {DB} DESCRIBE BY "Athena route and street data"',
           f"CREATE SCHEMASTORE CONTAINER {schema}"]
    for t, cols in TABLES.items():
        out.append(f'CREATE LIGHTNING REST TABLE {t}_raw FROM {DB} REQUEST(\n'
                   f'    endpoint "{base_url.rstrip("/")}/{t}.json",\n'
                   f'    method "get", response_type "json", http_encoding "URLENCODED"\n) HEADER (\n) BODY ()')
        sel = ",\n    ".join(f"r.{c} AS {c}" for c in cols)
        out.append(f"CREATE SCHEMASTORE VIEW {t} WITH CONTAINER {schema} AS\nSELECT\n    {sel}\n"
                   f"FROM {DB.lower()}.{t}_raw LATERAL VIEW explode(rows) AS r")
    return out


def sql_literal(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def inline_statements(schema: str) -> list[str]:
    """Fallback: views over inline VALUES (Spark SQL syntax). Unverified on Zetaris Lightning SQL."""
    out = [f"CREATE SCHEMASTORE CONTAINER {schema}"]
    for t, cols in TABLES.items():
        rows = json.loads((ROOT / "data" / f"{t}.json").read_text())["rows"]
        vals = ",\n  ".join("(" + ", ".join(sql_literal(r.get(c)) for c in cols) + ")" for r in rows)
        out.append(f"CREATE SCHEMASTORE VIEW {t} WITH CONTAINER {schema} AS\nSELECT * FROM VALUES\n  {vals}\nAS t({', '.join(cols)})")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--base-url", help="URL folder that serves data/*.json and that the Zetaris server can reach")
    g.add_argument("--inline", action="store_true")
    g.add_argument("--check", action="store_true")
    ap.add_argument("--print-only", action="store_true", help="print the SQL instead of running it")
    args = ap.parse_args()
    s = get_settings()
    stmts = [] if args.check else (rest_statements(args.base_url, s.zetaris_schema) if args.base_url
                                   else inline_statements(s.zetaris_schema))
    if args.print_only:
        print(";\n\n".join(stmts) + ";")
        return 0
    client = ZetarisClient(s.zetaris_rest_url, s.zetaris_base_url, s.zetaris_org_id, s.zetaris_api_key,
                           s.zetaris_username, s.zetaris_password, timeout_s=120)
    log = [f"# Zetaris load {datetime.now(timezone.utc).isoformat()}", f"mode: {'check' if args.check else 'rest' if args.base_url else 'inline'}"]
    for sql in stmts:
        first = sql.splitlines()[0][:90]
        try:
            client.run_sql(sql)
            log.append(f"OK    {first}")
        except ZetarisError as exc:
            msg = str(exc)
            status = "EXISTS" if "exist" in msg.lower() else "FAIL"
            log.append(f"{status:5} {first}  [{msg[:140]}]")
        print(log[-1])
    ok = True
    for t in TABLES:
        expected = len(json.loads((ROOT / "data" / f"{t}.json").read_text())["rows"])
        try:
            got = client.run_sql(f"SELECT COUNT(*) AS n FROM {s.zetaris_schema}.{t}")
            n = int(float(list(got[0].values())[0])) if got else 0
        except ZetarisError as exc:
            n, ok = None, False
            log.append(f"COUNT {t}: error {str(exc)[:120]}")
            print(log[-1])
            continue
        ok &= n == expected
        log.append(f"COUNT {s.zetaris_schema}.{t}: {n} (expected {expected}) {'PASS' if n == expected else 'FAIL'}")
        print(log[-1])
    log.append(f"auth method: {client.auth_method}")
    out = ROOT / "docs/evidence/zetaris_load.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(log) + "\n")
    print(f"\n{'ALL TABLES PASS' if ok else 'SOME TABLES FAILED'}; log written to {out.relative_to(ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
