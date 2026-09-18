# Politik i Praksis

*Political transparency through actual voting records — not campaign promises.*

## Background

Political accountability is not transparent in Denmark. Candidates brand themselves
on political ambitions and dreams — not on political merits and track records. A
candidate's true intentions show through action, not fluffy goal statements.

If politicians' actions and votes on proposed laws were more transparent, citizens
could choose representatives that best align with their own views based on a
candidate's track record. After the election, citizens could keep tracking that
behavior, holding their representatives accountable to what they actually do —
not just what they said they'd do.

## Objective

Build a website that lets citizens review the voting patterns of Danish politicians
on the topics they care about — using Folketinget's own open data as the source of
truth, so the picture is factual rather than rhetorical.

## Outcome

Create political transparency that lets citizens:
- Select the candidate who best matches their own views, based on that candidate's
  actual actions (votes, proposed laws).
- Hold politicians accountable to their real political record, not just their talk.

## Data Source

[Folketinget's open data](https://www.ft.dk/da/dokumenter/aabne_data) is exposed
through an OData API at **oda.ft.dk** — no authentication required. It defaults to
XML but returns clean JSON if you add `?$format=json` to any request (confirmed
against the live API), which is what we'll use — no XML parsing needed. The
entities relevant to this project:

| Entity | What it is |
|---|---|
| `Aktør` | Participants: MPs, parties, and committees ("Udvalg") are all actors |
| `Sag` | A case/bill |
| `Sagstrin` | A procedural step of a case (a vote happens at a step) |
| `Afstemning` | A single vote event |
| `Stemme` | One MP's individual vote (for / against / abstain / absent) in an `Afstemning` |
| `SagAktør` | Links a `Sag` to actors — including which committee handled it |
| `Periode` | A parliamentary session/year ("samling") — we now store several |
| `Emneord` | Subject-term tags on cases — a secondary theming signal for later |

The key insight: **`SagAktør` already links each bill to the committee that handled
it** (e.g. Skatteudvalget ≈ tax policy, Miljø- og Fødevareudvalget ≈ environment).
That gives us theme categorization "for free" from Folketinget's own data structure,
with no text analysis needed to get started. Specifically, the committee-referral
relationship is the `SagAktør` row with `rolleid=11` ("Henvist til" = "referred to"),
pointing at an `Aktør` with `typeid=3` (Udvalg) — `SagAktør` also holds other
relationship types (proposers, ministers, spokespeople), so this role filter is
what makes the committee link specific.

Similarly, "who is a current MP" isn't a clean field on a Person `Aktør` — it's
derived from **`Folketingsgruppe` (party group) membership**: party groups are
`Aktør` rows with `typeid=4` scoped to the current session, and `AktørAktør` rows
with `rolleid=15` ("medlem") pointing at one of them give that group's current
members. This is more robust than looking at voting history (it includes MPs who
haven't voted yet) and more complete than committee membership (it includes
ministers, who keep their party-group seat even though they don't sit on
committees).

## Scope Decisions

- **Incumbents only.** Folketinget's data only contains voting records for sitting
  and former MPs. Brand-new candidates who've never held a seat have no track
  record to show — they're deliberately out of scope until a later version.
- **Themes via existing categories first.** We reuse the committee link the data
  already provides, rather than building our own topic-detection logic up front.
  Custom auto-tagging (e.g. for cross-cutting topics like "climate" that span
  multiple committees) is deferred to a later version.
- **Current valgperiode + previous valgperiode, to start.** Originally just the
  current session; expanded once v0 was validated to also cover the current and
  previous electoral terms (5 sessions total), so citizens can see an MP's record
  across more than just the last few months. Deeper history stays deferred.
- **Local only, to start.** No hosting or domain decisions yet — the focus is on
  getting the data pipeline and core pages working on one machine first.

## Versioned Roadmap

### v0 — Minimal local prototype
- A small Python script pulls current-session `Sag`, `Afstemning`, `Stemme`,
  `Aktør`, and `SagAktør` data from oda.ft.dk into a local SQLite database.
- A minimal Flask app with three pages:
  1. **MP list** — all current incumbents.
  2. **MP detail** — the bills they've voted on and how they voted.
  3. **Bill list** — each bill, the committee that handled it, and the vote result.
- No styling polish, no accounts/auth. Runs locally via `flask run`.
- **Since extended**: now pulls the current valgperiode plus the whole
  previous valgperiode (5 sessions), with a samling picker grouped by term
  on the MP list and bill list, and each MP's detail page showing every
  fetched session as its own section (party can change between them).

### v1 — Theming & browsing
- Use the committee link as a filterable "theme" across the site.
- Add search/filter by MP name and by theme.
- ~~Basic CSS so the site is presentable to show to other people.~~ Done: a
  proper front page plus a teal/slate CSS pass across all pages, deliberately
  neutral rather than partisan-coded given the subject matter.

### v2 — Accountability views
- Per-MP voting-profile summary: % of votes for/against, broken down by theme.
- Side-by-side comparison of two or more MPs on a given theme.
- A simple timeline of an MP's votes within the session.

### v3 — Smarter theming & more history
- Keyword/NLP-based auto-tagging layered on top of committee categories, to catch
  cross-cutting topics that don't map cleanly to one committee.
- Multi-session history (current + previous valgperiode) was pulled forward into
  v0 already. Remaining here: going back further than that, which still needs
  extending `HISTORICAL_SESSIONS` by hand — the valgperiode-boundary rule itself
  is confirmed correct all the way back to 1952, so this is a matter of deciding
  how far back is useful, not a data-modeling problem.

### v4 — Public & candidate-facing
- Deploy publicly (hosting + domain).
- Approximate non-incumbent candidates' likely positions via their party's average
  voting record on each theme.
- Possibly a "find your match" tool comparing a citizen's stated views to MPs'
  actual voting records.

## Tech Stack (v0)

Chosen for being approachable to a beginner and runnable with no extra infrastructure:

- **Python** + `requests` — call the oda.ft.dk API.
- **SQLite** — local data storage, no separate database server needed.
- **Flask** + **Jinja2 templates** — the web app itself.

## Explicitly Out of Scope (for now)

- User authentication/accounts
- Public hosting/deployment
- Historical data beyond the current + previous valgperiode
- NLP/auto-tagging of themes
- Candidate matching for non-incumbents

These are all deferred to later versions above, not abandoned — this list exists so
scope stays under control while v0 is being built.
