# 06 · Sponsor technology explanation (paste into the HackOS form)

**Track:** Wildcard [Tinkerer]: we extended our existing Athena route map with a new agent layer.

**How Zetaris was integrated.**
Zetaris is Athena's data layer. Our route alternatives and street lighting data are published as JSON and
registered in Zetaris as Lightning REST tables, flattened into schemastore views (`athena.streets`,
`athena.route_steps`). We used the same pattern as the Setup Guide's PokéAPI recipe (`scripts/load_zetaris.py`).

On every route question, the agents query Zetaris over its REST API (`/sql-editor/sqls/run`, one statement per
call). They compute the comparison from the returned rows, and the app shows that SQL beside each claim. The
Report Verifier also checks every user report against Zetaris street data. If Zetaris is unreachable, Scout
re-plans onto the bundled sample data and the trace says so.

Note: on our local install, API tokens returned `400 Not Allowed`, so the client falls back to a login session,
as Zetaris's own helper scripts do. We reported this in Help Zetaris.

**Meterless Engine implemented: Scout Intent.**
We implemented Scout Intent in Python from the published Meterless specification (Apache-2.0). It runs before
every action:
- **Sense**: a weighted trigger scan, self-tested under 16 ms, plus semantic and contextual scoring with the
  canonical weights and confidence bands.
- **Interpret**: binds the trip, time and mode.
- **Guard**: injection patterns, role and surface policy, PII redaction, rate limit.
- **Route**: a capability graph that picks Zetaris or sample data, Nightshift or the offline stub.
- **Recommend**: a Nemotron profile per task.

It then signs an HMAC-SHA256 execution contract with a 5-minute TTL. Every agent verifies the contract before it
touches data or a model, and refuses tampered, expired or out-of-scope contracts.

Feature it powers: deciding whether a message is a route plan, a "why" question, a street report, an emergency
(shown immediately with 000, no model) or a manipulation attempt (blocked).

Test evidence:
- `tests/test_scout.py`: 20 tests covering the spec bands, routing, guard, contract signing, tampering, expiry,
  scope and re-planning.
- `evals/run_evals.py` against the Meterless eval thresholds. On our held-out set: intent top-1 0.962, tool
  precision 1.0, clarification rate 0.077, injection precision and recall 1.0. That injection set includes the
  10 Meterless v1 adversarial prompts.

**How the app connects to Meterless Nightshift.**
Nightshift is the app's only AI provider. The app calls the Nightshift Runtime's OpenAI-compatible API at
`http://127.0.0.1:8088/v1`, using the runtime token from the Console and the `meterless-app` and
`meterless-scope` headers. The NVIDIA key lives only in Nightshift.

Nightshift has no Linux x86 build, so it runs in a Windows 11 VM on our Ubuntu laptop (KVM via Docker), with a
port forward to the Runtime. `scripts/check_stack.py` runs the connection, auth, model-discovery and
text-request checks and records which model served the request.

**Where NVIDIA technology contributes.**
NVIDIA Nemotron models, reached through Nightshift, do three jobs:
1. The Route Analyst drafts the plain-English reasons.
2. The Evidence Critic semantically checks each claim against the facts.
3. The Report Moderator classifies street reports and flags prejudice.

Every step in the "How Athena decided" trace shows the model that actually served it (e.g.
`nvidia/nemotron-3.5-lightning-30b-a3b`), so judges can see where Nemotron does the work.

**What was unique about our use of the sponsor technologies.**
- Scout Intent's signed contracts make prompt injection unable to change route risk.
- Nemotron is used twice in a draft-and-critique loop. Deterministic rules and a second Nemotron call stop it
  from saying "safe" or citing numbers that aren't in the Zetaris data.
- Zetaris is where both the route evidence and the report verification come from, and the SQL is shown to the
  user.

**How Cursor was used.** *(Team: replace with an accurate description.)* For example: "We used Cursor's agent
to run and debug the code, the tests and the Zetaris load script, and to iterate on the interface. Much of the
agent code was generated with an AI coding assistant and reviewed and tested by us; generated code is permitted
by the rules." Only state what actually happened.

**Pre-existing components (declare in the form).**
Built for UNSW COMP3110 before 22 Oct 2026:
- the OpenTripPlanner `athena-sydney` street-tag mapper and its build pipeline (`tag_streets.py`, `plan_trips.py`)
- the MapLibre route map `web/index.html`
- the hand-made sample data `web/routes.json` and `web/streets.geojson`

Built during the build window:
- the agent layer (`athena/`)
- the Scout Intent implementation
- the Zetaris integration and data export
- the Nightshift integration
- the Ask Athena interface (`web/ask.html`)
- tests, evaluations and documentation
