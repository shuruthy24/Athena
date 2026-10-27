# 01 · Requirements audit and compliance checklist

Source of truth: the HackOS Welcome Guide, Hackathon Overview, "How to APPROACH Solutioning", Participant Journey
Map, the Participant Setup Guide (21 Oct 2026) and the HackOS announcement of 27 Oct 2026. Nothing below is
inferred beyond those documents. Where they are silent, the item is listed under **Open questions**.

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
| Zetaris as the data layer | Every route and street query goes to Zetaris over REST (`athena/data/zetaris.py`, `source.py`). Six Lightning REST tables behind schemastore views, registered by `scripts/load_zetaris.py` | `docs/evidence/zetaris_load.txt` (streets 12, route_steps 186, places 7, reports 14, route_legs 108, leg_streets 204, all matched); trace step `data` shows "Zetaris REST (auth=login)" plus the SQL in the UI | Done ✔ |
| At least one Meterless Engine in a working feature, with a test | **Scout Intent** implemented to the Meterless spec: 5-stage pipeline, canonical bands and weights, guard stack, capability graph, model profiles, HMAC-SHA256 contracts with a 5-min TTL, re-planning. Every executor verifies the contract before acting | `tests/test_scout.py` (20 tests), `evals/run_evals.py` against Meterless thresholds, `docs/evidence/evaluation_report.md` | Done ✔ |
| Meterless Nightshift as the primary AI provider | Every model call goes through `NightshiftLLM` (OpenAI-compatible Runtime, Bearer runtime token, `meterless-app`, `meterless-scope`). There is no other provider | `docs/evidence/stack_checks.txt`: connection, auth and model discovery (108 models) all PASS; `tests/test_api_and_live_path.py` against a mock Runtime | Done ✔ |
| NVIDIA wherever it contributes (models via Nightshift) | Nemotron drafts claims, critiques them and moderates reports. We pinned `nvidia/nemotron-3.5-bolt-e4b` for the fast and critic lanes after measuring four routes against the real analyst prompt (see `07`) | `docs/evidence/stack_checks.txt` records the served model; `samples/outputs_live/` names it on every analyst and critic step | Done ✔ |
| NVIDIA award: show which model handled the request | `model` field per step in the trace, surfaced in the "How Athena decided" panel; `check_stack.py` flags a non-Nemotron model | `samples/outputs_live/*.json`, UI panel | Done ✔ |
| Mention, copied prompt or unused dependency does not count | The engine sits on the request path of every feature, its tests pass, and the live runs show it acting | Tests, live samples | Done ✔ |

## Submission components
| Component | Requirement | Artifact | Status |
|---|---|---|---|
| Demo video | 1–4 min: main workflow, how the agent solves the challenge, key features, Zetaris and Meterless integration | — | Done ✔ |
| Slide deck | Problem and users, solution, workflow or architecture, stack, Zetaris and Meterless use, what's unique, product visuals, future work | Built; covers all eight required elements | Done ✔ |
| GitHub repository | Public, all source, README with setup, usage and dependencies | This repo + `README.md` | Done ✔ |
| Sponsor technology explanation | Zetaris integration; engines, what each does, and the test; Nightshift connection; where NVIDIA contributes; what's unique; how Cursor was used | `06_sponsor_technology.md` | Done ✔ |
| Pre-existing components | Declared in the submission form | README "Pre-existing components" and `06_sponsor_technology.md` | Done ✔ |

## Expected for the Technical and Demo scores (Overview §12)
| Item | Artifact | Status |
|---|---|---|
| Runnable from a clean setup | `run.sh`, `requirements.txt`, README quick start | Done ✔ both sample and live |
| Logs or traces, including the model per request | `trace` in every response, with the serving model named per step | Done ✔ |
| Sample inputs and outputs | `samples/inputs.json`, `samples/outputs_sample_mode/`, `samples/outputs_live/` | Done ✔ |
| Sample mode with no Zetaris, Nightshift or credentials | `SAMPLE_MODE=true`, offline stand-in labelled in every trace | Done ✔ |
| No committed secrets; `.env.example` placeholders only | `.gitignore`, `.env.example`, `docs/evidence/secrets_scan.txt` | Done ✔ |

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
| Impact | 30 | A real, documented problem: 92% of NSW women say a sense of safety affects the transport routes they choose, and 59% feel unsafe in public after dark against 31% of men (NSW Safer Cities Survey 2022–23). The output is a decision aid, not a claim of safety |
| Technical | 20 | Spec-conformant engine, a critic loop with bounded retries, recovery paths, 73 tests, held-out evaluation, live stack verified |
| Innovation | 15 | The model can't make claims the data doesn't support; unknown lighting is treated as uncertainty, not risk; user reports become fading, verified signals; two of four intent paths reach no model at all |
| Demo | 15 | A planned trip across three modes, the checked explanation, the visible trace with the model per step, the emergency path, the blocked injection |
| Product & UX | 10 | Planner, street map and Ask Athena on one navigation bar; 000 always visible; plain language; confidence chip; empty, loading and error states |
| Sponsor Tech | 10 | Zetaris on every query, Scout on every request, Nightshift as the only provider, a pinned Nemotron route named per step |

## Open questions and compliance risks
1. **Nightshift runs in a Windows VM on our Ubuntu laptop**, because Nightshift has no Linux x86 build (the DGX build is aarch64). It still runs on our machine. Ask the organisers to confirm in the Help desk and mention it in the submission.
2. **Zetaris API tokens are refused** ("Not Allowed") on our local install, so the app falls back to login, as Zetaris's own helper scripts do. Reported to Help Zetaris.
3. **Pre-existing work**: the route map, sample data and OTP pipeline were built before 22 Oct and must be declared. The Wildcard track scores only the new work.
4. **Cursor statement**: the announcement asks how Cursor was used. Describe what actually happened. Generated code is allowed (Welcome Guide §8), but the account must be honest.
5. **Data realism**: routes are a hand-made sample, and the reports are synthetic. Both are labelled everywhere they appear.
6. **Moderation accuracy fell with a real model**: 14/14 with the offline stub, 12/14 with Nemotron. Both misses sent a redacted report to human review rather than approving it, so the failures fall on the safe side. We report the lower number.
