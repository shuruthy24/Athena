# 02 · Project proposal: Athena

## In one sentence
We are extending Athena so it helps **women travelling at night in Sydney** choose between the fastest route and a
lower-risk one. Athena answers their questions in plain language, grounds every claim in **street lighting and
route data served through Zetaris**, and has an evidence-checking agent verify each claim before it's shown.

## 1. Problem
Navigation apps optimise for time or price, never for how exposed a route feels at night. In NSW, **92% of women
say a sense of safety affects the transport routes they choose**; 59% feel unsafe in public after dark, against
31% of men, and 42% feel unsafe most or all of the time (NSW Safer Cities Survey, December 2022 – February 2023,
reported in the NSW Gender Equality Budget Statement 2023–24). Useful signals exist — street lighting, activity,
user reports — but they are scattered and absent from routing tools. When a tool does offer a "safer" route, it
rarely explains why, and it can overclaim.

## 2. Target users
Young women aged 18–30 travelling at night between UNSW Kensington and the Sydney CBD. The secondary group is
anyone who feels vulnerable travelling alone after dark (gender-diverse people, people with disability,
international students). The decision supported is: *"Is the lower-risk route worth the extra minutes tonight,
and why?"*

## 3. Solution
Three surfaces on one navigation bar:
- **Plan a trip.** Walking, cycling and public transport ranked together for any departure time — best balance,
  lowest risk, fastest, fewest changes, least walking. Each option opens into why it got its rating, every leg
  and street with its lighting and score, the Zetaris SQL behind the numbers, and nearby police stations,
  hospitals and train stations on the map.
- **Ask Athena.** For the selected trip, time and mode, Athena shows the fastest route against the Athena route
  (time, route risk, lit share, unknown-lighting share, worst stretch). It explains the trade-off in 2–4 claims,
  each citing the data behind it, with the SQL one click away.
- **Street map.** Every street Athena scores, coloured by its risk indicators for a chosen time, with counts of
  higher-risk and unlit streets.

Across all three: **help now** — a danger message goes straight to 000 and staffed stations, with no model call
and no clarifying question; **report a street** — one-tap reports are redacted, moderated and verified against
street data, and approved reports add a fading penalty; and **show its reasoning** — every answer carries a trace
of which agent ran, which Nemotron model served it, how long it took, what the critic rejected, and the signed
Scout contract that authorised it.

## 4. Innovation
1. **The model can't make claims the data doesn't support.** The Evidence Critic combines deterministic checks
   with a Nemotron critic:
   - every number must match a cited fact
   - no "safe" wording
   - no streets that aren't on either route
   - no reversed direction

   Failing claims go back to the analyst, up to two times. After that, Athena shows a fact-only explanation.
   Most safety-routing apps present a score; Athena presents checked reasons.
2. **Uncertainty is shown as uncertainty.** Missing lighting data is reported as "unknown" and lowers confidence.
   It's never silently treated as dark (a decision from our ACC-3 crit), which avoids penalising areas only
   because they're under-mapped.
3. **Intent and risk decided before any action.** Scout Intent turns each message into a signed, expiring,
   capability-scoped contract. Executors refuse anything outside it, so a prompt-injected report can't change
   risk scores. Two of our four conversational paths reach no model at all: danger is answered immediately, and
   manipulation is blocked at the guard.
4. **Reports become evidence, not opinions.** Redaction, then moderation, then verification against Zetaris
   data, then a time-decaying penalty. Contradictory or prejudiced reports go to review or are rejected.

## 5. Technical approach
Python 3.12 and FastAPI. Zetaris, the data layer, runs in Docker. Meterless Nightshift serves NVIDIA Nemotron
over an OpenAI-compatible Runtime. Scout Intent is implemented to the Meterless spec. The pre-existing
OpenTripPlanner pipeline produced the route alternatives, and the journey planner ranks and explains them.
The interface is plain HTML with MapLibre vendored for offline use. Details: `03_architecture.md`.

## 6. Data strategy
| Data | Source | Use | Status |
|---|---|---|---|
| Street segments, lighting tag, rule-based risk per time band | Pre-existing Athena pipeline on OpenStreetMap (ODbL) | `athena.streets` in Zetaris | Hand-made sample of 12 named segments |
| Route alternatives (fastest vs Athena) for UNSW ↔ Town Hall, 3 time bands, 3 modes | Pre-existing OTP pipeline (sample) | `athena.route_steps` in Zetaris | 36 route variants, 186 rows |
| Journey legs and their streets | Derived from the same pipeline | `athena.route_legs` (108 rows), `athena.leg_streets` (204 rows) | Powers the planner's per-leg detail |
| Places | Curated | `athena.places` | 7 rows |
| User reports | Synthetic, written for evaluation | `athena.reports` | 14 labelled reports |
| Help locations (police, hospitals, train stations) | OpenStreetMap (ODbL) | Map overlay | Fetched on the laptop; OSM records no staffing hours |
| City of Sydney street-light poles, TfNSW GTFS | Public open data | Planned to replace the sample | Not used in this submission |

Details and limitations: `04_data_models_evaluation.md`.

## 7. AI and ML strategy (and what we chose not to use)
- **Needed.** A language model to turn numbers into plain-English reasons, and to moderate free-text reports.
  Nemotron via Nightshift does both. A second Nemotron call critiques the drafts.
- **Not needed, so not used.** No model training at runtime and no embedding service. The Scout semantic term
  uses local TF-IDF, which runs in well under a millisecond. Risk scoring stays rule-based, because our crit
  advised shipping the lighting rule unless an ML model beats it on held-out data. That comparison needs real
  data we don't have yet.
- **Chosen deliberately.** We measured four Nemotron routes against our real analyst prompt before picking one.
  `nemotron-3.5-lightning-30b-a3b` failed with a provider error every time; `nemotron-3-ultra-550b-a55b` worked
  but took 37.8 s and emitted reasoning prose before any JSON; `meterless/auto` routed to ultra;
  `nemotron-3.5-bolt-e4b` returned clean JSON in 7.4 s. We pinned bolt-e4b for both lanes, which also makes the
  trace evidence unambiguous. See `07_status_and_limitations.md`.

## 8. Impact (what we can and cannot claim)
On the sample data, across all 18 trip/time/mode cases, the Athena route has a **median 36.3% lower route risk**
(range 20.2–46.2%) for a **median of 0.8 extra minutes** (max 2.9). This shows what the system does, measured
with its own rule-based score. It is **not** evidence of real-world safety. Real impact would be measured by
post-trip ratings and by comparing route choices with and without explanations (`04`, section 4).

## 9. Feasibility
Everything in this submission runs today, both in sample mode with no credentials and live on the team's stack:
Zetaris in Docker, Nightshift in a Windows VM, and NVIDIA Nemotron behind it. 73 tests pass, the live stack
checks are 10/10, and ten end-to-end live runs are committed with their traces. The remaining risk is
operational rather than design: Nightshift needs about 4 GB of RAM and must be reopened after each VM start.

## 10. Evaluation
- **Scout routing:** held-out top-1 0.962, injection precision and recall 1.0, tool precision 1.0, clarification
  rate 0.077. Top-3 recall 0.962, above the 0.94 floor but under the 0.98 target — the one threshold we missed.
- **Critic rules:** precision and recall 1.0 on 276 generated claims, across six corruption types.
- **Moderation:** 14/14 with the offline stub, 12/14 (0.857) with Nemotron. Both misses sent a redacted report to
  human review rather than approving it, so the failures fall on the safe side. We report the lower number.
- **Route outcome:** see section 8.

Methods, caveats and the v1 → v2 history: `04_data_models_evaluation.md` and `docs/evidence/evaluation_report.md`.

## Critical assessment of our own idea
- **Strength:** a clear user, a clear decision, and a design where the sponsor technology does essential work.
- **Weakness:** the data is a two-place sample, so the demo can't show city-wide coverage. We say so on screen.
- **Weakness:** the risk score is a rule we wrote. The explanation layer is only as good as that score. We
  mitigate this by showing unknowns and never saying "safe".
- **Weakness:** both held-out sets were written by the same people who wrote the router, which is a known source
  of optimism. They are smoke tests, not benchmarks.
- **Changed from the original proposal** (following the ACC-3 crit): Uber/cost and crime statistics were
  dropped, because suburb-level crime data is coarse and biased. Lighting is the primary factor.
