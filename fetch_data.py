"""Pulls bills, votes, and the MP roster (with party) from Folketinget's open
data API (oda.ft.dk) into a local SQLite database - for the current session
plus a fixed backfill list of past sessions.
"""

from datetime import datetime

import requests

from database import get_connection, init_db

BASE = "https://oda.ft.dk/api/"
PAGE_SIZE = 100

# The previous valgperiode's samlinger, to fetch alongside whatever the
# current session turns out to be. Derived from a simple rule confirmed
# against oda.ft.dk's entire Periode history (1952-2026, zero exceptions):
# a samling titled "(2. samling)" or higher always marks a real election and
# the start of a brand-new valgperiode; a plain-titled year just continues
# whichever valgperiode is already running. The 2022-11-01 election started
# id 158 ("2022-23 (2. samling)"); that valgperiode ran through id 165
# ("2025-26 (1. samling)") until the 2026-03-24 election started a new one.
# Extend this list by hand if more history is wanted later.
HISTORICAL_SESSIONS = [165, 163, 160, 158]

STEMMETYPE = {1: "For", 2: "Imod", 3: "Fravær", 4: "Hverken for eller imod"}


def get_json(path, params=None):
    params = dict(params or {})
    params["$format"] = "json"
    response = requests.get(BASE + path, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def get_all_json(path, params=None):
    """Like get_json, but pages through $skip until every row is collected -
    the API caps a single response at 100 rows, including nested collections
    inside a $expand (see the CLAUDE.md gotcha note).
    """
    params = dict(params or {})
    params["$top"] = PAGE_SIZE
    rows = []
    skip = 0
    while True:
        params["$skip"] = skip
        page = get_json(path, params)["value"]
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        skip += PAGE_SIZE


def get_current_periode_id():
    periods = get_json(
        "Periode",
        {"$filter": "type eq 'samling'", "$orderby": "startdato desc", "$top": 5},
    )["value"]
    now = datetime.now()
    for periode in periods:
        start = datetime.fromisoformat(periode["startdato"])
        end = datetime.fromisoformat(periode["slutdato"]) if periode["slutdato"] else None
        if start <= now and (end is None or now <= end):
            return periode["id"]
    raise RuntimeError("Could not determine the current parliamentary session")


def periode_already_fetched(periode_id):
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM periode WHERE id = ?", (periode_id,)).fetchone()
    conn.close()
    return row is not None


def fetch_mps_for_periode(periode_id, reference_date):
    """Returns {aktør_id: {"navn": None, "party": ...}} - who was seated,
    and for which party, as of `reference_date` (the moment we care about
    for this particular samling: "now" if it's still open, otherwise its
    own end date).

    Uses party-group (Folketingsgruppe) membership rather than committee
    membership or voting history: it's a structural signal scoped to the
    session, and unlike committee membership it also covers ministers (who
    stay in their party group even though they don't sit on committees), and
    unlike voting history it covers MPs who simply haven't had a recorded
    vote yet.
    """
    groups = get_all_json(
        "Aktør", {"$filter": f"typeid eq 4 and periodeid eq {periode_id}"}
    )

    mps = {}
    for group in groups:
        relations = get_all_json(
            "AktørAktør",
            {"$filter": f"tilaktørid eq {group['id']} and rolleid eq 15"},
        )
        for relation in relations:
            start = datetime.fromisoformat(relation["startdato"])
            end = datetime.fromisoformat(relation["slutdato"]) if relation["slutdato"] else None
            if not (start <= reference_date and (end is None or reference_date <= end)):
                continue  # this particular membership wasn't active at reference_date
            mp_id = relation["fraaktørid"]
            if mp_id not in mps:
                mps[mp_id] = {"navn": None, "party": group["navn"]}
    return mps


def fill_in_mp_names(mps):
    for mp_id in mps:
        mps[mp_id]["navn"] = get_json(f"Aktør({mp_id})")["navn"]


def fetch_bills_for_periode(periode_id):
    """Returns a list of dicts, one per bill that was actually voted on in
    this session, each with its committee (if any) and the individual MP
    votes from its final recorded vote.
    """
    bill_ids = [
        row["id"]
        for row in get_all_json("Sag", {"$filter": f"periodeid eq {periode_id}"})
    ]

    committee_cache = {}

    def get_committee(aktør_id):
        if aktør_id not in committee_cache:
            data = get_json(f"Aktør({aktør_id})")
            committee_cache[aktør_id] = data if data["typeid"] == 3 else None
        return committee_cache[aktør_id]

    bills = []
    for bill_id in bill_ids:
        # NB: no nested Stemme here - a full-chamber vote can have up to 179
        # individual votes, well past the API's 100-row-per-collection cap,
        # and that cap applies inside $expand too with no way to page into
        # it. Stemme is fetched separately below, with real pagination.
        bill = get_json(f"Sag({bill_id})", {"$expand": "Sagstrin/Afstemning,SagAktør"})

        # the vote date lives on Sagstrin (the reading/step), not on Afstemning
        # itself, so carry the step's dato along with each vote
        afstemninger = [
            {**afstemning, "_dato": step["dato"]}
            for step in bill["Sagstrin"]
            for afstemning in step["Afstemning"]
        ]
        if not afstemninger:
            continue  # never voted on - out of scope for v0

        final_vote = max(afstemninger, key=lambda a: a["id"])
        stemmer = get_all_json("Stemme", {"$filter": f"afstemningid eq {final_vote['id']}"})

        committee = None
        for link in bill["SagAktør"]:
            if link["rolleid"] == 11:
                found = get_committee(link["aktørid"])
                if found:
                    committee = found
                    break

        bills.append(
            {
                "id": bill["id"],
                "periode_id": periode_id,
                "titel": bill["titel"],
                "titelkort": bill["titelkort"],
                "nummer": bill["nummer"],
                "vedtaget": final_vote["vedtaget"],
                "konklusion": final_vote["konklusion"],
                "dato": final_vote["_dato"],
                "committee": committee,
                "stemmer": stemmer,
            }
        )
    return bills


def save_periode_to_db(periode, mps, bills):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "INSERT OR REPLACE INTO periode (id, kode, titel, startdato, slutdato) VALUES (?, ?, ?, ?, ?)",
        (periode["id"], periode["kode"], periode["titel"], periode["startdato"], periode["slutdato"]),
    )

    # wipe this periode's existing rows first, so re-running one session is
    # always safe (idempotent) without touching any other stored session
    cursor.execute(
        "DELETE FROM vote WHERE bill_id IN (SELECT id FROM bill WHERE periode_id = ?)",
        (periode["id"],),
    )
    cursor.execute("DELETE FROM bill WHERE periode_id = ?", (periode["id"],))
    cursor.execute("DELETE FROM mp_period WHERE periode_id = ?", (periode["id"],))

    for mp_id, mp in mps.items():
        cursor.execute("INSERT OR IGNORE INTO mp (id, navn) VALUES (?, ?)", (mp_id, mp["navn"]))
        cursor.execute(
            "INSERT INTO mp_period (mp_id, periode_id, party) VALUES (?, ?, ?)",
            (mp_id, periode["id"], mp["party"]),
        )

    saved_committees = set()
    for bill in bills:
        committee_id = None
        if bill["committee"]:
            committee_id = bill["committee"]["id"]
            if committee_id not in saved_committees:
                cursor.execute(
                    "INSERT OR IGNORE INTO committee (id, navn) VALUES (?, ?)",
                    (committee_id, bill["committee"]["navn"]),
                )
                saved_committees.add(committee_id)

        cursor.execute(
            """INSERT INTO bill (id, periode_id, titel, titelkort, nummer, committee_id, vedtaget, konklusion, dato)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bill["id"],
                periode["id"],
                bill["titel"],
                bill["titelkort"],
                bill["nummer"],
                committee_id,
                bill["vedtaget"],
                bill["konklusion"],
                bill["dato"],
            ),
        )

        for stemme in bill["stemmer"]:
            if stemme["aktørid"] not in mps:
                continue  # not someone we're tracking for this session
            cursor.execute(
                "INSERT INTO vote (bill_id, mp_id, vote_type) VALUES (?, ?, ?)",
                (bill["id"], stemme["aktørid"], STEMMETYPE.get(stemme["typeid"], "Ukendt")),
            )

    conn.commit()
    conn.close()


def main():
    print("Setting up database...")
    init_db()

    current_periode_id = get_current_periode_id()
    sessions = [current_periode_id] + HISTORICAL_SESSIONS

    for periode_id in sessions:
        periode = get_json(f"Periode({periode_id})")
        end = datetime.fromisoformat(periode["slutdato"]) if periode["slutdato"] else None
        is_closed = end is not None and end < datetime.now()

        if is_closed and periode_already_fetched(periode_id):
            print(f"Skipping {periode['titel']} (id {periode_id}) - already fetched and closed.")
            continue

        print(f"Fetching {periode['titel']} (id {periode_id})...")
        reference_date = end if is_closed else datetime.now()

        mps = fetch_mps_for_periode(periode_id, reference_date)
        fill_in_mp_names(mps)
        print(f"  -> {len(mps)} MPs")

        bills = fetch_bills_for_periode(periode_id)
        print(f"  -> {len(bills)} bills with recorded votes")

        save_periode_to_db(periode, mps, bills)

    print("Done.")


if __name__ == "__main__":
    main()
