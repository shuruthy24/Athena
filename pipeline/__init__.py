"""Athena data pipeline: raw snapshots in data/raw/ -> derived tables in data/processed/.

Stages, each idempotent:  ingest (fetch raw, never edits) -> clean -> features -> trips -> export -> check.
Run everything with `make data`.
"""
