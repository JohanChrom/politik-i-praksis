# Working notes for Claude

**Politik i Praksis** — a website letting Danish citizens see how sitting MPs
actually vote, by theme, using Folketinget's open data. Full background, objective
and versioned roadmap live in `README.md` — read that for the "why". This file is
the standing context to keep in mind every session.

## Current status

Not started yet — about to build v0 (data fetch script + minimal Flask app).

**Keep this line updated** whenever a version milestone from the README roadmap is
completed, so the next session knows where things actually stand without having to
re-derive it from the code.

## Standing constraints — don't silently expand past these

- **Incumbents only.** Only sitting/former MPs have voting records in the data;
  don't add candidate-matching logic (that's v4).
- **Theme = Folketinget's existing committee data, not NLP.** Use the `SagAktør`
  committee link for theming. Keyword/NLP auto-tagging is v3, not now.
- **Current parliamentary session only.** Don't pull historical sessions until v3.
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

## Working conventions

- The user is a **novice coder** — explain things in plain terms, avoid jargon
  without a quick explanation, and prefer small, understandable steps over clever
  or dense code.
- Keep it simple: no abstractions, config layers, or generalization the user hasn't
  asked for. Three similar lines beats a premature helper function.
- Stack for now: **Python**, `requests`, **SQLite**, **Flask** + **Jinja2**.
