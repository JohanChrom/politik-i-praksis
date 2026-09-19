# Working notes for Claude

**Politik i Praksis** — a website letting Danish citizens see how sitting MPs
actually vote, by theme, using Folketinget's open data. Full background, objective
and versioned roadmap live in `README.md` — read that for the "why". This file is
the standing context to keep in mind every session.

## Current status

**v1 complete.** `fetch_data.py` pulls **5 sessions** into `politik.db` -
the current samling plus the whole previous valgperiode
(`HISTORICAL_SESSIONS` in fetch_data.py; extend that list by hand to go
further back). `app.py` serves `/` (front page with live stats and a
disclaimer of how far back the data goes), `/medlemmer` (MP list, with a
name-search box), `/mp/<id>` (MP detail, all fetched sessions shown as
separate sections since party can change between them, same columns/expand
treatment as `/bills` plus the MP's own vote), and `/bills` (bill list,
date-sortable, with a committee/"Udvalg" filter, each row expandable to
show the bill's resume, a per-party for/imod/fravær/hverken vote
breakdown, and a link to the enacted law text on retsinformation.dk when
one exists). `/medlemmer` and `/bills` both have a samling picker grouped
by valgperiode. CSS is in place (`static/style.css`, teal/slate palette,
deliberately neutral - no red/blue - given the political subject matter),
with a light/dark theme toggle. Run with:
```
source .venv/bin/activate
python3 fetch_data.py   # populates politik.db - closed sessions are skipped
                         # once already fetched, only the open one re-runs
flask run                # auto-detects app.py, no FLASK_APP needed
```
**Deliberately not done:** filtering `/medlemmer` by committee - the
database only links a committee to the bills it handled, not to the MPs who
sit on it (that's separate data `fetch_data.py` doesn't pull), so this
would need a new fetch + schema addition, not just a UI filter. Flagged to
the user, who chose to skip it for now.

Next up: v2, smarter theming & more history (NLP/keyword auto-tagging on top
of the committee categories; deeper history beyond the current + previous
valgperiode). Note the roadmap changed since v1 was scoped: the original v2
(per-MP % for/against summary, MP comparison, a vote timeline) was revised -
the user wasn't sold on the % summary or timeline, so those were dropped
entirely, and the side-by-side MP comparison idea was kept but pushed to v3
instead. See `README.md`'s Versioned Roadmap for the current version list.

**Keep this line updated** whenever a version milestone from the README roadmap is
completed, so the next session knows where things actually stand without having to
re-derive it from the code.

## Standing constraints — don't silently expand past these

- **Incumbents only.** Only sitting/former MPs have voting records in the data;
  don't add candidate-matching logic (that's v3).
- **Theme = Folketinget's existing committee data, not NLP.** Use the `SagAktør`
  committee link for theming. Keyword/NLP auto-tagging is v2, not now.
- **Current valgperiode + previous valgperiode only** (5 sessions, explicit
  list in `fetch_data.py`). Deeper history / auto-detecting valgperiode
  boundaries indefinitely is still v2 territory - the current rule (see the
  data-source cheat-sheet below) is proven correct back to 1952, but going
  further back is still a deliberate by-hand extension, not automatic.
- **Local only.** No hosting/deployment work until v3.

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
fetch_data.py for the generalized version). `startdato` can be null too, not
just `slutdato` - found a real row while backfilling to 2015 (a Greenlandic
MP's membership in a small, newly-formed party grouping, `AktørAktör` id
37896341, has both dates null - a genuine gap in Folketinget's own data, not
a fetch bug). Treated the same way as a null `slutdato`: "unknown, so don't
exclude on this end" (`datetime.min` as the sentinel).

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
