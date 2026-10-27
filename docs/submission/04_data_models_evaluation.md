# 04 · Data, models and evaluation

Status labels: **Completed** (exists and was checked), **Planned** (not in this submission), **Assumption** (needs validation).

## 1. Datasets
| Dataset | Provenance | Licence | Rows | Used for | Status |
|---|---|---|---|---|---|
| `data/streets.json` → `athena.streets` | Exported from the pre-existing `web/streets.geojson` (hand-made sample; street names from OpenStreetMap) | Code: ours. Street names: ODbL, © OpenStreetMap contributors | 12 named segments (one duplicate name removed) | Lighting tag (`yes`/`no`/unknown), rule-based risk per time band (18, 22, 01) | Completed |
| `data/route_steps.json` → `athena.route_steps` | Exported from the pre-existing `web/routes.json` (`"source": "sample"`, generated 18 Oct 2026, in OTP response shape) | ours | 186 rows = 2 trips × 3 bands × 3 modes × 2 variants | Fastest vs Athena route comparison | Completed |
| `data/places.json` | Same file | ours | 7 places (only UNSW Library and Town Hall have routes) | UI and parameter binding | Completed |
| `data/reports.json` | **Synthetic**, written for this submission | ours | 14 labelled reports | Moderation demo and evaluation | Completed |
| City of Sydney street-light poles | City of Sydney open data | Check the dataset licence before use | — | Replacing the lighting tag with measured pole density | Planned |
| TfNSW GTFS timetable | TfNSW Open Data Hub (token required) | Per dataset terms | — | Live OTP routing (used by the pre-existing pipeline) | Planned for this layer |
| BOCSAR crime counts | NSW BOCSAR | — | — | **Dropped**: suburb-level counts are too coarse and risk biasing routes against poorer or culturally diverse areas (ACC-3 crit) | Excluded |

### Data quality and validation (Completed)
- `scripts/export_data.py` checks that every street on a route exists in the streets table (0 unknown).
- Missing lighting stays `null`, never `"no"`. Tests cover this.
- The duplicated "UNSW campus" segment is de-duplicated (first kept).
- `scripts/load_zetaris.py` compares Zetaris row counts with the source files and writes `docs/evidence/zetaris_load.txt`.

### Limitations and bias
- **Not representative.** Two places, one corridor, hand-made values. Results describe the system, not Sydney.
- **The risk score is a rule we wrote** (lighting, quietness, path type, from the pre-existing pipeline). It isn't
  calibrated against incidents or perceptions.
- **Lighting tags come from OpenStreetMap.** Coverage is uneven, and under-mapped areas look "unknown". We
  surface that as low confidence rather than as risk.
- **The synthetic reports** were written by us with expected labels, so the moderation evaluation is a smoke
  test, not an accuracy estimate.

## 2. Models
| Role | Model | Where it runs | Why it's needed | Check |
|---|---|---|---|---|
| Route Analyst (draft claims) | NVIDIA Nemotron, fast lane (e.g. `nvidia/nemotron-3.5-lightning-30b-a3b`), via Nightshift | NVIDIA hosted endpoint, routed by Nightshift | Turns numbers into plain-English reasons a user can act on | Every claim passes the critic |
| Evidence Critic (model part) | Nemotron, critic lane via Nightshift | Same | Catches meaning the rules can't (exaggeration, unsupported inference) | Deterministic rules run in all cases |
| Report Moderator | Nemotron, fast lane | Same | Classifies free text and flags prejudice | Verifier cross-checks with Zetaris data |
| Scout Stage-2 semantic term | Local TF-IDF over registry examples | In process | Paraphrase tolerance for routing | Held-out routing evaluation |
| Offline stand-in | `sample-offline-stub` (deterministic) | In process | Lets judges run with no credentials | Labelled in every trace |

The model that actually served each call (the Runtime's `model` field) is recorded per step. **Assumption:** the
Nemotron route names available in the team's Nightshift catalog. `scripts/check_stack.py` lists them and warns
if a request was served by a non-Nemotron model.

## 3. Evaluation results (Completed: `python3 evals/run_evals.py`, full table in `docs/evidence/evaluation_report.md`)

### Scout Intent routing (against Meterless thresholds)
| Metric | v1 registry on held-out v1 | v2 registry on held-out v2 | Target / floor |
|---|---|---|---|
| intent_top1 | 0.821 (below floor) | **0.962** | 0.92 / 0.85 |
| intent_top3_recall | 0.964 | **0.962** (above floor, below target) | 0.98 / 0.94 |
| tool_precision | 0.750 | **1.000** | 0.88 / 0.80 |
| clarification_rate | 0.286 (above max) | **0.077** | ≤0.15 / ≤0.25 |
| injection precision / recall (18 prompts) | — | **1.000 / 1.000** | 0.95 / 0.90 |

Method: the v1 set (28 prompts) was written before the router was first run on it. Its failures (e.g. "HELP",
"catcalling") drove the v2 changes:
- stem-matching triggers
- light stemming in the semantic term
- no clarification for the GENERAL fallback
- a wider trigger vocabulary

A new v2 set (26 prompts) was written and checksummed **before** v2 was run on it (`evals/heldout_checksums.txt`).
The same people wrote the registry and both sets, which biases results upward. Treat them as smoke tests: the
Meterless authors say the same of their own 32-example corpus.

### Evidence Critic, deterministic layer
276 claims = the fact-only claims for all 18 cases, each tested clean and with 5 corruptions (wrong number,
"safe" wording, street on neither route, missing citations, reversed direction). Result: precision 1.000, recall
1.000.

The first run caught 0% of reversed-direction claims, and that rule was added afterwards. Because these claims
are generated by construction, this tests the rules. It doesn't measure how often real Nemotron drafts are wrong.
That rate shows up in the live trace as the share of `revise` verdicts.

### Report moderation
14 synthetic reports: 14/14 actions correct with the offline keyword stub. This shows the redaction → moderation
→ verification pipeline works. It is **not** a measure of Nemotron's moderation accuracy. Run `--live` to measure
that. The two "review" cases (a report that contradicts lighting data, an unknown street) are decided by the
verifier using Zetaris data.

### Route outcome versus baseline (sample data)
Baseline = the fastest route. Across 18 cases: median route-risk reduction 36.3% (range 20.2–46.2%); median extra
time 0.8 min (max 2.9). This is measured with Athena's own rule-based score on hand-made routes. It isn't
real-world evidence.

## 4. Evaluation plan beyond the hackathon (Planned)
- **Routing:** grow each intent family to at least 50 examples, as Meterless recommends. Split per class.
  Collect clarification and override rates from telemetry.
- **Explanations:** sample 50 live traces and have two people label each claim supported or unsupported. Measure
  the critic's precision and recall against them, and the agreement between the two labellers.
- **Moderation:** the synthetic fake-report attack (the ACC-3 crit item): planted false reports. Measure
  precision and recall of review/reject, as in the spam lab.
- **Risk model:** keep a held-out final test set and plot reliability curves. Ship ML only if it beats the
  lighting-only rule (ACC-3 crit).
- **User outcome:** post-trip 1–5 ratings for safety, time and cost. Compare choices with and without explanations.
