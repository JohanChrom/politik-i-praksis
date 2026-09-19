# Working notes for Claude

**Politik i Praksis** — a website letting Danish citizens see how sitting MPs
actually vote, by theme, using Folketinget's open data. Full background, objective
and versioned roadmap live in `README.md` — read that for the "why". This file is
the standing context to keep in mind every session.

## Current status

**v1 and v2 both complete.** `fetch_data.py` pulls **15 sessions** into
`politik.db` - the current samling plus four full past valgperioder, back
to the 2015-06-18 election (`HISTORICAL_SESSIONS` in fetch_data.py; extend
that list by hand to go further back). `app.py` serves `/` (front page with
live stats and a disclaimer of how far back the data goes), `/medlemmer`
(MP list, with a name-search box and a committee/"Udvalg" filter, each
member showing their party and which committee(s) they sit on), `/mp/<id>`
(MP detail, all fetched sessions shown as separate sections since party and
committee seats can change between them, each section listing that
periode's committee membership(s) too, same columns/expand treatment as
`/bills` plus the MP's own vote), and `/bills` (bill list, date-sortable,
with committee and Emneord/topic-tag filters, each row expandable to show
the bill's resume, its Emneord tags, a per-party for/imod/fravær/hverken
vote breakdown, and a link to the enacted law text on retsinformation.dk
when one exists). `/medlemmer` and `/bills` both have a samling picker
grouped by valgperiode. CSS is in place (`static/style.css`, teal/slate
palette, deliberately neutral - no red/blue - given the political subject
matter), with a light/dark theme toggle.

**Theming (Emneord tags)** and **committee membership** were both added as
part of v2, in the same `politik.db` rebuild (2026-09-19 session):
- Bills are tagged with Folketinget's own `Emneord` (subject-term) keywords
  - fetched via the `EmneordSag` junction (`emneord`/`bill_emneord`
  tables), filtered to drop `typeid == 4` entries (confirmed by sampling
  real bills that those are law-section citations like "Retspleje 24", not
  topics). No NLP/keyword dictionary work was needed - Folketinget's own
  tagging turned out to be clean enough on its own. Verified after the
  rebuild: 99.9% of all 3,784 bills have at least one tag, across all 15
  periods.
- MPs' committee memberships are fetched the same `AktørAktör` `rolleid=15`
  ("medlem") way party-group membership already was, just pointed at
  `Aktør typeid=3` (committees) instead of `typeid=4` (party groups) -
  stored in a new `mp_committee` table. This is exactly the data that was
  missing when `/medlemmer`'s committee filter was first deferred (see the
  old note below) - now it's not deferred anymore.

Both features needed a **full `politik.db` rebuild** to backfill historical
sessions, since `fetch_data.py` only populates a periode's data when it
actually (re-)fetches that periode, and closed sessions already stored get
skipped on normal runs - so adding a new field/table to the fetch code
only takes effect for sessions fetched *after* the code change unless you
force a full rebuild (delete `politik.db`, re-run `fetch_data.py`). Keep
this in mind for any future addition to what gets fetched.

Run with:
```
source .venv/bin/activate
python3 fetch_data.py   # populates politik.db - closed sessions are skipped
                         # once already fetched, only the open one re-runs
flask run                # auto-detects app.py, no FLASK_APP needed
```

Next up: v3 (public & candidate-facing: side-by-side MP comparison, deploy,
approximate non-incumbent candidates, possibly a "find your match" tool).
Note the roadmap changed since v1 was scoped: the original v2 (per-MP %
for/against summary, MP comparison, a vote timeline) was revised - the user
wasn't sold on the % summary or timeline, so those were dropped entirely,
and the side-by-side MP comparison idea was kept but pushed to v3 instead.
See `README.md`'s Versioned Roadmap for the current version list.

**Keep this line updated** whenever a version milestone from the README roadmap is
completed, so the next session knows where things actually stand without having to
re-derive it from the code.

## Standing constraints — don't silently expand past these

- **Incumbents only.** Only sitting/former MPs have voting records in the data;
  don't add candidate-matching logic (that's v3).
- **Theme = Folketinget's existing data, not NLP.** Committees (`SagAktør`)
  and, as of v2, `Emneord` subject-term tags (`EmneordSag`) are both fair
  game - they're official, pre-curated data. A hand-written keyword
  dictionary or statistical/NLP topic modeling is still out of scope unless
  Emneord coverage proves insufficient.
- **Back to 2015 (four valgperioder) only** - explicit list in
  `fetch_data.py`'s `HISTORICAL_SESSIONS`. Auto-detecting valgperiode
  boundaries indefinitely further back is deliberately not automatic - the
  current rule (see the data-source cheat-sheet below) is proven correct all
  the way back to 1952, but going further is a by-hand extension, a decision
  about how far back is useful, not a data-modeling problem.
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
- `Emneord` — subject-term tags on cases, linked via the `EmneordSag`
  junction (`id`, `emneordid`, `sagid`); fetched and stored, excluding
  `typeid == 4` entries (law-section citations, not topics)
- Committee membership isn't a separate entity - it's the same `AktørAktör`
  "medlem" relation (`rolleid=15`) used for party-group membership, just
  pointed at `Aktør typeid=3` (committees) instead of `typeid=4` (party
  groups); stored in `mp_committee`

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
exclude on this end" (`datetime.min` as the sentinel). `EmneordSag` is safe to
trust from inside a nested `$expand` unlike `Stemme` - a bill has at most a
handful of subject-term tags, nowhere near the 100-row cap - but the nested
rows only carry `emneordid`, not the term text itself, so each id still needs
a separate `Emneord(id)` lookup (cached per fetch run) to resolve it, same as
committees.

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
