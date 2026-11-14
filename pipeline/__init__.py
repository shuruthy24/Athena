"""Athena data pipeline: raw snapshots in data/raw/ -> derived tables in data/processed/ (AGENTS.md Step 2).

Stages, each idempotent:  ingest (fetch raw, never edits) -> clean -> features -> trips -> export -> check.
Run everything with `make data`.
"""
