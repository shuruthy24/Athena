# Contributions

How the work on Athena was divided. The commit counts and areas come from `git log`. The sections marked
**[team to fill in]** must be written by the team, because they cover work git can't show.

## Who did what

| Member | Git identity | Commits | Main areas (from the files they changed) |
| --- | --- | --- | --- |
| Kaira Dumasia | `kaira`, `ka17a` [team to confirm both are Kaira] | 20 + 9 | first prototype (app, web pages, docs); typed `/api/explain` and `/api/report` endpoints; first live evaluation runs and error analysis; real-data pipeline (OpenStreetMap matching, lamp-based lighting, trip scoring by time, DVC) |
| Shuruthy Dhushiyandan | `Shuruthy Dhushiyandan`, `shuruthy24` | 9 + 30 | evaluation and data practices (reproducibility, provenance, frozen sets); live stack check and injection evaluation; judge audit and blind A/B; report labelling, group split and classifier comparison; final evaluations, cleanup and documentation |
| Andrii Rohovyi | — | 0 | [team to fill in: Andrii's work outside git, e.g. labelling, collecting reports, slides, testing] |

## Project stages and owners

| Stage | What was built | Owner |
| --- | --- | --- |
| 1 | Prototype | Kaira (app), Shuruthy (README) |
| 2 | Rework: model gateway, data pipeline, evaluation scaffolding | [team to fill in] |
| 3 | Typed endpoints and first live evidence | Kaira |
| 4 | Real data | Kaira |
| 5 | Evaluation and data practices | Shuruthy |
| 6 | Verification, cleanup and final results | Shuruthy |

## Work outside git [team to fill in]

For each member, list what git doesn't show, for example:

- writing and collecting street reports, and labelling them (every report was labelled by two people)
- labelling explanation traces PASS/FAIL and making the blind A/B choices
- design decisions (risk weights, unknown lighting scored halfway, labels relative to the fastest route)
- demo slides, the Demo Day talk and the demo video

## How we worked [team to fill in]

One or two sentences on how tasks were split and reviewed (for example, who owned which stage, and how
disagreements on labels were settled).
