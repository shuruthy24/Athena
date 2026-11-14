# Athena for COMP3110. See AGENTS.md section 9.
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PORT ?= 8001

.PHONY: help install data ingest dvc-track labels train test eval eval-live check checksums secrets serve hooks

help:
	@echo "make install | ingest | data | dvc-track | labels | train | test | eval | eval-live | check | serve | hooks"

install:
	$(PYTHON) -m pip install -r requirements.txt

# raw -> processed, then pipeline.check (AGENTS.md Step 2). Fetch raw first with `make ingest`.
data:
	$(PYTHON) -m pipeline.run
	$(PYTHON) -m pipeline.check

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

# Offline evals: no model calls. Writes evals/results/offline.json; never touches the hackathon evidence.
eval:
	SAMPLE_MODE=true $(PYTHON) evals/run_evals.py

# Budgeted live evals through the COMP3110 gateway (needs COMP3110_MODEL_BASE_URL and COMP3110_API_KEY).
eval-live:
	$(PYTHON) evals/run_evals.py --live
	@for s in evals/compare_models.py evals/eval_classifier.py evals/judge.py; do \
		if [ -f $$s ]; then echo "run $$s explicitly; each has its own call budget (AGENTS.md section 7)"; fi; done

# Frozen eval sets must not change: held-out Scout prompts now, the report test split once it exists.
checksums:
	cd evals/scout && sha256sum -c --quiet ../heldout_checksums.txt
	@if [ -f evals/checksums.txt ]; then sha256sum -c --quiet evals/checksums.txt; fi
	@echo "checksums: ok"

secrets:
	$(PYTHON) scripts/scan_secrets.py

check: test eval checksums secrets
	$(PYTHON) -m pipeline.check
	@echo "make check: all green"

serve:
	$(PYTHON) -m uvicorn athena.server:app --host 127.0.0.1 --port $(PORT)

# Plain git hook, no extra package needed. pre-commit users can use .pre-commit-config.yaml instead.
hooks:
	printf '#!/bin/sh\nexec %s scripts/scan_secrets.py --staged\n' "$(PYTHON)" > .git/hooks/pre-commit
	chmod +x .git/hooks/pre-commit
	@echo "installed .git/hooks/pre-commit (secrets scan on staged files)"
