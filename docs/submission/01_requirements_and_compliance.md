# 01 · Requirements audit and compliance checklist

Source of truth: the HackOS Welcome Guide, Hackathon Overview, "How to APPROACH Solutioning", Participant Journey
Map, the Participant Setup Guide (21 Oct 2026) and the HackOS announcement of 27 Oct 2026. Nothing below is
inferred beyond those documents. Where they are silent, the item is listed under **Open questions**.

Status key:
- **Done ✔** = built and checked here.
- **Done ◐** = built and tested against mocks or in sample mode, but not yet run against the team's live services.
- **Team ☐** = needs the team (recording, uploading, running on the laptop).

## Event facts
| Item | Value (source) |
|---|---|
| Build window | 22 Oct 2026 00:00 UTC to 27 Oct 2026 23:45 UTC (Welcome Guide §1) |
| **Submission deadline** | **27 Oct 2026, 23:45 UTC = 28 Oct 10:45 AEDT. Hard deadline, on HackOS. Can be updated until then.** |
| Judging | Opens immediately; at least 3 judges; out of 100 points (Overview §13) |
| Track | One primary track. We chose **Wildcard [Tinkerer]**: an existing project extended with Zetaris and Meterless, scored on the new work |
| Team | 1–5 builders; composition locks when the first submission is created (Welcome Guide §7) |

## Mandatory technology
| Requirement (verbatim intent) | How Athena meets it | Evidence | Status |
|---|---|---|---|
| Zetaris as the data layer | Every route and street query goes to Zetaris over REST (`athena/data/zetaris.py`, `source.py`). The tables are registered by `scripts/load_zetaris.py` | Trace step `data` shows "Zetaris REST (auth=…)" plus the SQL in the UI; `docs/evidence/zetaris_load.txt` | Done ◐ (live run on laptop pending) |
| At least one Meterless Engine in a working feature, with a test | **Scout Intent** implemented to the Meterless spec: 5-stage pipeline, canonical bands and weights, guard stack, capability graph, model profiles, HMAC-signed contracts with a 5-min TTL, re-planning. Every executor verifies the contract before acting | `tests/test_scout.py` (20 tests), `evals/run_evals.py` against Meterless thresholds, `docs/evidence/evaluation_report.md` | Done ✔ |
| Meterless Nightshift as the primary AI provider | Every model call goes through `NightshiftLLM` (OpenAI-compatible Runtime, Bearer runtime token, `meterless-app`, `meterless-scope`). There is no other provider | `tests/test_api_and_live_path.py` against a mock Runtime; `scripts/check_stack.py` for the live run | Done ◐ |
| NVIDIA wherever it contributes (models via Nightshift) | Nemotron fast lane drafts claims and moderates reports; Nemotron critic lane checks claims. `FAST_MODEL` and `CRITIC_MODEL` are pinned to `nvidia/nemotron-3.5-bolt-e4b` in `.env.example` and `athena/config.py`, not `meterless/auto`. The trace records the model that served each call | Trace in UI and `runs/*.jsonl`; `docs/evidence/stack_checks.txt` (requested and served by `nvidia/nemotron-3.5-bolt-e4b`); `samples/outputs_live/` | Done ✔ |
| NVIDIA award: show which model handled the request | `model` field per step in the trace; `check_stack.py` flags a non-Nemotron model | UI panel "How Athena decided" | Done ◐ |
| Mention, copied prompt or unused dependency does not count | The engine sits on the request path of every feature and its tests pass | Tests | Done ✔ |
| Cursor | Optional. Stated in `06_sponsor_technology.md`: Cursor was the AI coding tool for the build; generated code reviewed and tested by us | `06_sponsor_technology.md` | Done ✔ |

## Submission components
| Component | Requirement | Artifact | Status |
|---|---|---|---|
| Demo video | 1–4 min: main workflow, how the agent solves the challenge, key features, Zetaris and Meterless integration | Script in `05_demo_and_presentation.md` | Team ☐ record and upload |
| Slide deck | Problem and users, solution, workflow or architecture, stack, Zetaris and Meterless use, what's unique, product visuals, future work | Outline in `05_demo_and_presentation.md` | Team ☐ (or ask Claude to build it) |
| GitHub repository | Public, all source, README with setup, usage and dependencies | This repo + `README.md` | Done ✔ content / Team ☐ push and make public |
| Sponsor technology explanation | Zetaris integration; engines, what each does, and the test; Nightshift connection; where NVIDIA contributes; what's unique; how Cursor was used | `06_sponsor_technology.md` (paste-ready) | Done ✔ |
| Pre-existing components | Declared in the submission form | README "Pre-existing components" and `06_sponsor_technology.md` | Done ✔ text / Team ☐ paste into form |
| Live demo / Drive links | "Where relevant" | — | Team ☐ optional |

## Expected for the Technical and Demo scores (Overview §12)
| Item | Artifact | Status |
|---|---|---|
| Runnable from a clean setup | `run.sh`, `requirements.txt`, README quick start | Done ✔ in sample mode (fresh venv) / ◐ live |
| Logs or traces, including the model per request | `trace` in every response; `runs/YYYYMMDD.jsonl` | Done ✔ |
| Sample inputs and outputs | `samples/inputs.json`, `samples/outputs_sample_mode/` | Done ✔ (`outputs_live/` Team ☐) |
| Sample mode with no Zetaris, Nightshift or credentials | `SAMPLE_MODE=true` | Done ✔ |
| No committed secrets; `.env.example` placeholders only | `.gitignore`, `.env.example`, secrets scan in `docs/evidence/` | Done ✔ |

## Agent-system expectations (Welcome Guide §3; APPROACH §9)
| Expectation | Where |
|---|---|
| Dynamic delegation | Scout routes to analyst, moderator, emergency or refusal by intent and risk |
| Critique or validation, iterative loop | Evidence Critic ↔ Route Analyst, up to 2 revisions, then a fact-only fallback |
| Branching, retries, escalation, recovery | Clarification, guard block, Zetaris failure → `scout.replan` onto sample data, model failure → fact-only explanation, report → human review |
| Shared context or state | Signed execution contract shared by all executors; report queue feeds route risk |
| Logs showing how the system reasoned | Trace and Scout telemetry events |

## Rubric alignment
| Criterion | Pts | What we show |
|---|---|---|
| Impact | 30 | A real, documented problem (Safer Cities 2023: 59% of NSW women feel unsafe in public after dark, compared with 32% of men). The output is a decision aid, not a claim of safety |
| Technical | 20 | Spec-conformant engine, a critic loop with bounded retries, recovery paths, 73 tests, held-out evaluation |
| Innovation | 15 | The model can't make claims the data doesn't support; unknown lighting is treated as uncertainty, not risk; user reports become fading, verified signals |
| Demo | 15 | One trip, one question, the visible revise-then-approve trace, the emergency path, the blocked injection |
| Product & UX | 10 | One screen, 000 always visible, plain language, confidence chip, empty, loading and error states |
| Sponsor Tech | 10 | Zetaris on every query, Scout on every request, Nightshift as the only provider, Nemotron named per step |

## Open questions and compliance risks
1. **Nightshift runs in a Windows VM on our Ubuntu laptop**, because Nightshift has no Linux x86 build (the DGX build is aarch64). It still runs on our machine. Ask the organisers to confirm in the Help desk and mention it in the submission.
2. **Zetaris API tokens are refused** ("Not Allowed") on our local install, so the app falls back to login, as Zetaris's own helper scripts do. Reported to Help Zetaris.
3. **Pre-existing work**: the route map, sample data and OTP pipeline were built before 22 Oct and must be declared. The Wildcard track scores only the new work.
4. **Cursor statement**: the announcement asks how Cursor was used. Describe what actually happened. Generated code is allowed (Welcome Guide §8), but the account must be honest.
5. **Data realism**: routes are a hand-made sample, and the reports are synthetic. Both are labelled everywhere they appear.
