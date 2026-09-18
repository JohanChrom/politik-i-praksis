# Working notes for Claude

**Politik i Praksis** — a website letting Danish citizens see how sitting MPs
actually vote, by theme, using Folketinget's open data. Full background, objective
and versioned roadmap live in `README.md` — read that for the "why". This file is
the standing context to keep in mind every session.

## Current status

v0+ built and working locally: `fetch_data.py` pulls **5 sessions** into
`politik.db` - the current samling plus the whole previous valgperiode
(`HISTORICAL_SESSIONS` in fetch_data.py; extend that list by hand to go
further back). `app.py` serves `/` (front page with live stats),
`/medlemmer` (MP list), `/mp/<id>` (MP detail, all fetched sessions shown as
separate sections since party can change between them, date-sortable), and
`/bills` (bill list, date-sortable) - `/medlemmer` and `/bills` have a
samling picker grouped by valgperiode. Basic CSS is in place
(`static/style.css`, teal/slate palette, deliberately neutral - no
red/blue - given the political subject matter). Run with:
```
source .venv/bin/activate
python3 fetch_data.py   # populates politik.db - closed sessions are skipped
                         # once already fetched, only the open one re-runs
flask run                # auto-detects app.py, no FLASK_APP needed
```
Next up: rest of v1 (theming/filtering by committee, MP name search).

**Keep this line updated** whenever a version milestone from the README roadmap is
completed, so the next session knows where things actually stand without having to
re-derive it from the code.

## Standing constraints — don't silently expand past these

- **Incumbents only.** Only sitting/former MPs have voting records in the data;
  don't add candidate-matching logic (that's v4).
- **Theme = Folketinget's existing committee data, not NLP.** Use the `SagAktør`
  committee link for theming. Keyword/NLP auto-tagging is v3, not now.
- **Current valgperiode + previous valgperiode only** (5 sessions, explicit
  list in `fetch_data.py`). Deeper history / auto-detecting valgperiode
  boundaries indefinitely is still v3 territory - the current rule (see the
  data-source cheat-sheet below) is proven correct back to 1952, but going
  further back is still a deliberate by-hand extension, not automatic.
- **Local only.** No hosting/deployment work until v4.

If a task seems to require going past one of these, flag it and ask rather than
just doing it — these boundaries were deliberately chosen to keep v0 buildable.

## Data source cheat-sheet (oda.ft.dk — OData, no auth, add `?$format=json` for JSON)

- `Aktør` — MPs, parties, and committees ("Udvalg") are all actors
- `Sag` — a case/bill
- `Sagstrin` — a procedural step of a case (a vote happens at a step)
- `Afstemning` — a single vote event
- `Stemme` — one MP's individual vote (for/against/abstain/absent) in an `Afstemning`
- `SagAktør` — links a `Sag` to actors, including the handling committee (our theme proxy)
- `Periode` — a parliamentary session/year, used to scope to "current session"
- `Emneord` — subject-term tags on cases (secondary theming signal, later)

**Gotcha: the API caps every collection at 100 rows, including nested
collections inside `$expand`, with no way to `$skip` into a nested one.** A
full-chamber vote has up to 179 `Stemme` rows, so `Sag(id)?$expand=Sagstrin/
Afstemning/Stemme` silently drops everyone past the first 100 — this caused a
real bug (found by checking one bill against ft.dk's own site: an MP's vote
was simply missing) that turned out to affect ~40% of all vote rows across the
dataset. Fix: fetch any collection that can plausibly exceed 100 rows (`Stemme`
by `afstemningid`, so far) as its own separate call through `get_all_json`
(already used for the 553-bill `Sag` list), never trust it from inside a
nested expand. Also: a person's `AktørAktör` "medlem" relation to a party
group is only valid for the reference date it actually covers
(`startdato <= reference_date <= slutdato`) - for the still-open current
samling that's "now", for a closed one it's that samling's own `slutdato`.
An earlier version hardcoded "now" via `slutdato eq null`, which broke as
soon as historical sessions were added (see `fetch_mps_for_periode` in
fetch_data.py for the generalized version).

**Valgperiode grouping rule (confirmed against the entire 1952-2026 Periode
history, zero exceptions):** a samling titled "(2. samling)" or higher always
marks a real election and the start of a brand-new valgperiode; a plain year
or "(1. samling)" just continues whichever valgperiode is already running.
Implemented in `app.py`'s `group_by_valgperiode`.

## Working conventions

- The user is a **novice coder** — explain things in plain terms, avoid jargon
  without a quick explanation, and prefer small, understandable steps over clever
  or dense code.
- Keep it simple: no abstractions, config layers, or generalization the user hasn't
  asked for. Three similar lines beats a premature helper function.
- Stack for now: **Python**, `requests`, **SQLite**, **Flask** + **Jinja2**.
