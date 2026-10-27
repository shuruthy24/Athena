# 05 · Demo plan, video script and slide outline

## Pre-flight checklist (10 minutes before recording)
1. `cd ~/hackathon/windows-vm && docker compose start`. At http://localhost:8006, **open Nightshift** and wait 30 s.
2. `cd ~/hackathon/zetaris-platform && docker compose start`, then wait until it's healthy.
3. `cd ~/hackathon/athena-agent && source .venv/bin/activate && python3 scripts/check_stack.py`. You want all PASS
   and "served by nvidia/nemotron-…".
4. `./run.sh`, then open http://localhost:8001. Run each demo question once, so the first answer isn't slow on camera.
5. Close other tabs. Hide `.env` and terminals that show tokens.

## User journey
Pick trip (UNSW Library → Town Hall, 10 pm, walking) → ask "Why is the Athena route better tonight?" → read the
two route cards and three checked reasons → open the SQL → see in "How Athena decided" that the critic sent a
draft back → report a broken light → notice the emergency number is always on screen.

## Video script (3:30, within the 1–4 minute limit)
| Time | Show | Say |
|---|---|---|
| 0:00–0:20 | Title slide, then the statistic | "59% of NSW women feel unsafe in public after dark, compared with 32% of men. Navigation apps optimise for speed, never for how exposed a route is at night." |
| 0:20–0:35 | Existing map at `/map` | "This is Athena, our existing route map from before the hackathon. This week we added an agent layer that explains and checks its choices." |
| 0:35–1:20 | Ask Athena: UNSW → Town Hall, 10 pm, walking. Click Ask | "Athena's route takes about half a minute longer and has 46% lower route risk. It avoids Moore Park path, which is unlit. Every number links to the SQL that Zetaris ran." Open the SQL. |
| 1:20–2:00 | The "How Athena decided" panel | "Scout Intent, a Meterless Engine, classified the question and issued a signed contract. The analyst, a Nemotron model through Nightshift, drafted claims, and the Evidence Critic checked each one against the data." Point at a `revise` step if one appears, and at the model name on each step. |
| 2:00–2:20 | Type "Someone is following me" | "Danger goes straight to 000 and staffed stations. No model call, no questions." |
| 2:20–2:45 | Report "two lights out" on Moore Park path; then the injection chip | "Reports are redacted before any model sees them, moderated by Nemotron and checked against Zetaris data. Attempts to manipulate the system are blocked at the Scout guard." |
| 2:45–3:10 | Zetaris SQL Editor `SELECT * FROM athena.streets`; Nightshift log in the Windows VM showing Nemotron | "All data comes through Zetaris. All AI goes through Meterless Nightshift to NVIDIA Nemotron. Nightshift runs in a Windows VM on our Ubuntu laptop, because it has no Linux build." |
| 3:10–3:30 | Evaluation slide | "On our held-out set, Scout routes 96% of messages correctly and blocks every injection we tried. Next: City of Sydney streetlight data, live routing, and testing with UNSW students." |

## Slide outline (10 slides)
1. **Athena**: lower-risk night routes, explained with evidence. Team, track (Wildcard).
2. **Problem**: 59% vs 32% (Safer Cities 2023); speed-only navigation; safety signals are scattered.
3. **Users**: women aged 18–30 travelling at night between UNSW and the CBD. The decision: "Is the lower-risk route worth the extra minutes, and why?"
4. **Before and after**: pre-existing OTP route map (declared) → new: Ask Athena agent layer.
5. **How it works**: architecture diagram (`03_architecture.md`).
6. **The critic loop**: draft → check (numbers, wording, streets, direction + Nemotron) → revise ≤2 → fact-only fallback. A screenshot of the trace.
7. **Sponsor tech**: Zetaris tables and SQL; Scout Intent contract; Nightshift → Nemotron per step; how Cursor was used.
8. **Results**: Scout eval table vs Meterless thresholds; critic 276/276; median 36% lower route risk for +0.8 min (sample data, labelled).
9. **Responsible design**: never "safe"; unknown ≠ dark; PII redacted before any model; prejudiced reports rejected; 000 always visible.
10. **Next**: City of Sydney poles, live OTP, crowdsourced report verification study, UNSW pilot. Limitations stated plainly.

Product visuals: `docs/submission/img/` (desktop, phone, emergency).
