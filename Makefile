# Athena. Every target has a plain-Python equivalent (README, "Commands").
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PORT ?= 8001

.PHONY: help install install-lock data data-verify fetch-raw ingest dvc-track labels train test eval eval-real eval-live check checksums secrets serve verify reproduce report docker load-test readme hooks

help:
	@echo "make install | serve | verify | check | eval | reproduce | data | data-verify | fetch-raw | eval-real | labels | train | eval-live | docker | load-test"
	@echo "Without make: python scripts/check.py, python scripts/eval_all.py, python scripts/verify_demo.py (see README)"

install:
	$(PYTHON) -m pip install -r requirements.txt

# The exact versions behind the committed results (Python 3.12 or 3.13).
install-lock:
	$(PYTHON) -m pip install -r requirements-lock.txt

# raw -> processed, then pipeline.check. Fetch raw first with `make ingest`.
data:
	$(PYTHON) -m pipeline.run
	$(PYTHON) -m pipeline.check --write-quality

# Prove data/processed rebuilds identically from the raw snapshots named in its manifest (content hashes).
data-verify:
	$(PYTHON) -m pipeline.verify

# Download the raw snapshots listed in data/raw_manifest.json and check their sha256.
fetch-raw:
	$(PYTHON) -m pipeline.fetch_raw

# Writes a new dated raw snapshot (never overwrites). Needs network access to overpass-api.de.
ingest:
	$(PYTHON) -m pipeline.ingest osm
	@$(PYTHON) -m pipeline.ingest gtfs

# Version raw snapshots with DVC (needs `pip install dvc` and a remote; see data_sources.md).
dvc-track:
	dvc add data/raw
	@echo "now: git add data/raw.dvc data/.gitignore && git commit, then dvc push"

# After both labellers fill data/labelling/reports_to_label.csv: kappa, disagreements, then the group split.
labels:
	$(PYTHON) -m pipeline.agreement
	$(PYTHON) -m pipeline.split

# Train the TF-IDF report classifier on the train split (needed when REPORT_CLASSIFIER=tfidf-*).
train:
	$(PYTHON) -m athena.classify.tfidf --kind $(or $(KIND),logreg)

test:
	SAMPLE_MODE=true $(PYTHON) -m pytest -q

# Offline evals: no model calls. Pinned to the hand-made sample so they stay a fixed regression gate; rewrites
# evals/results/{offline,pii,route_baselines,injection_e2e,data_quality}.json, the README table and docs/evaluation.md.
eval:
	$(PYTHON) scripts/eval_all.py

# The same evaluations on the real data (data/processed). LABEL names the data version in data_coverage.json.
eval-real:
	$(PYTHON) -m pipeline.check --write-quality
	$(PYTHON) evals/eval_data_coverage.py --label $(or $(LABEL),final)
	$(PYTHON) evals/eval_route_baselines.py
	$(PYTHON) scripts/verify_demo.py
	$(PYTHON) scripts/results_table.py --write
	$(PYTHON) scripts/build_eval_report.py --write

# Regenerate every offline result into a temp folder and compare with the committed files (changes nothing).
reproduce:
	$(PYTHON) scripts/reproduce.py

# Every demonstrated feature end to end, in-process (BASE_URL=... to check a running or deployed server).
verify:
	$(PYTHON) scripts/verify_demo.py $(if $(BASE_URL),--base-url $(BASE_URL),)

report:
	$(PYTHON) scripts/results_table.py --write
	$(PYTHON) scripts/build_eval_report.py --write

# Budgeted live evals through the model gateway (needs COMP3110_MODEL_BASE_URL and COMP3110_API_KEY).
eval-live:
	$(PYTHON) evals/run_evals.py --live
	@for s in evals/compare_models.py evals/eval_classifier.py evals/judge.py; do \
		if [ -f $$s ]; then echo "run $$s explicitly; each has its own call budget"; fi; done

# Frozen held-out sets must not change (evals/checksums.txt: guard probes, PII cases, report test split).
checksums:
	$(PYTHON) -m pipeline.lineage

secrets:
	$(PYTHON) scripts/scan_secrets.py

# Tests, reproduce (compare, never rewrite), frozen sets, secrets, data checks, docs in sync, demo features.
check:
	$(PYTHON) scripts/check.py

# Regenerate the README results table from evals/results/.
readme:
	$(PYTHON) scripts/results_table.py --write

serve:
	$(PYTHON) -m uvicorn athena.server:app --host 127.0.0.1 --port $(PORT)

# Container (see the Dockerfile header) and load test. BASE_URL is the deployed URL for the evidence in ops.json.
docker:
	docker build -t athena .

load-test:
	$(PYTHON) scripts/load_test.py --base-url $(or $(BASE_URL),http://127.0.0.1:$(PORT))

# Plain git hook, no extra package needed. pre-commit users can use .pre-commit-config.yaml instead.
hooks:
	printf '#!/bin/sh\nexec %s scripts/scan_secrets.py --staged\n' "$(PYTHON)" > .git/hooks/pre-commit
	chmod +x .git/hooks/pre-commit
	@echo "installed .git/hooks/pre-commit (secrets scan on staged files)"
