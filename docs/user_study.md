# Task study: do people choose the lower-risk option when it costs them little?

The outcome metric in [metrics.md](metrics.md), measured with real people, not with Athena's own rule. Run by the
team; analysed by `python evals/eval_user_study.py` (results in `evals/results/user_study.json`). Nothing in this
study is generated: if it has not been run, the evaluation report says "not run yet".

## Design (fixed before the first session)

- **Participants:** 6 or more people who travel in the study area at night (classmates outside the team, friends,
  people on campus). No one from the team. Each gets a pseudonymous group id (`p1`, `p2`, ...), never a name.
- **Within-subject, two conditions, counterbalanced:** odd-numbered participants see the baseline first, even ones
  see Athena first.
  - *Baseline:* the same options as a plain list, fastest first, with times and modes only and no risk information.
    This is what a standard journey planner shows. Print it from `/api/journeys?...&sort=fastest`, or cover the risk
    labels on screen.
  - *Athena:* the planner at `/`, with the risk labels, "Why this rating" and "Explain this trade-off".
- **Tasks:** 3 trips the participant could plausibly take, at 10 pm or 1 am, from the routable pairs (`/api/health`
  lists them). Use the same 3 trips in both conditions, in the same order.
- **Recorded per task:** which option they chose (`fastest`, `lower_risk` or `other`), and how many extra minutes
  the lower-risk option costs (from the planner). After the Athena condition, ask once: "Did the app say any route
  is safe?" Record `understood_not_safe` as `yes` if they say no route was called safe.
- **Targets:** the lower-risk choice share when it costs 5 minutes or less is higher with Athena than with the
  baseline; misreading rate 0.

## Consent (read aloud, record `consent=yes` only after a clear yes)

> This is a 10-minute study for a UNSW course project about night-time journey planning. We'll show you two
> versions of a trip planner and ask which route you would pick. We record your choices and an optional comment,
> not your name or contact details. You can stop at any time, and we'll delete your answers if you ask before
> 27 November 2026. The data is used only for this course project and deleted after marking.

## Sheet

Copy `evals/user_study/sessions_template.csv` to `evals/user_study/sessions.csv`, with one row per task and
condition. Columns: `session_id, participant_group, consent, date, order (baseline-first | athena-first), condition
(baseline | athena), trip (e.g. unsw|central), band, lower_risk_extra_min, chosen, understood_not_safe, comment`.
Write comments in your own words with no names. Commit the sheet, then run the analysis.

## Limits

A handful of participants gives wide intervals, and a choice made on screen is not a choice made at 1 am. The
study shows whether the interface is understood and persuasive, not whether anyone is safer.
