"""Pulls current-session bills, votes, and the current MP roster from
Folketinget's open data API (oda.ft.dk) into a local SQLite database.
"""

from datetime import datetime

import requests

from database import get_connection, init_db

BASE = "https://oda.ft.dk/api/"
PAGE_SIZE = 100


def get_json(path, params=None):
    params = dict(params or {})
    params["$format"] = "json"
    response = requests.get(BASE + path, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def get_all_json(path, params=None):
    """Like get_json, but pages through $skip until every row is collected -
    the API caps a single response at 100 rows.
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


def fetch_current_mps(periode_id):
    """Returns {aktør_id: {"navn": None, "party": ...}}.

    Uses party-group (Folketingsgruppe) membership rather than committee
    membership or voting history: it's a structural signal scoped to the
    current session, and unlike committee membership it also covers
    ministers (who stay in their party group even though they don't sit
    on committees), and unlike voting history it covers MPs who simply
    haven't had a recorded vote yet.
    """
    groups = get_all_json(
        "Aktør", {"$filter": f"typeid eq 4 and periodeid eq {periode_id}"}
    )

    mps = {}
    for group in groups:
        relations = get_all_json(
            "AktørAktør",
            # slutdato eq null: only a person's currently-active membership
            # counts - an MP who has since left this group for another one
            # still has a (now-ended) row here that we must not pick up.
            {"$filter": f"tilaktørid eq {group['id']} and rolleid eq 15 and slutdato eq null"},
        )
        for relation in relations:
            mp_id = relation["fraaktørid"]
            if mp_id not in mps:
                mps[mp_id] = {"navn": None, "party": group["navn"]}
    return mps


def fill_in_mp_names(mps):
    for mp_id in mps:
        mps[mp_id]["navn"] = get_json(f"Aktør({mp_id})")["navn"]


STEMMETYPE = {1: "For", 2: "Imod", 3: "Fravær", 4: "Hverken for eller imod"}


def fetch_bills_with_votes(periode_id):
    """Returns a list of dicts, one per bill that was actually voted on in
    the current session, each with its committee (if any) and the
    individual MP votes from its final recorded vote.
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


def save_to_db(mps, bills):
    conn = get_connection()
    cursor = conn.cursor()

    for mp_id, mp in mps.items():
        cursor.execute(
            "INSERT INTO mp (id, navn, party) VALUES (?, ?, ?)",
            (mp_id, mp["navn"], mp["party"]),
        )

    saved_committees = set()
    for bill in bills:
        committee_id = None
        if bill["committee"]:
            committee_id = bill["committee"]["id"]
            if committee_id not in saved_committees:
                cursor.execute(
                    "INSERT INTO committee (id, navn) VALUES (?, ?)",
                    (committee_id, bill["committee"]["navn"]),
                )
                saved_committees.add(committee_id)

        cursor.execute(
            """INSERT INTO bill (id, titel, titelkort, nummer, committee_id, vedtaget, konklusion, dato)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bill["id"],
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
                continue  # not a current MP (e.g. someone who has since left)
            cursor.execute(
                "INSERT INTO vote (bill_id, mp_id, vote_type) VALUES (?, ?, ?)",
                (bill["id"], stemme["aktørid"], STEMMETYPE.get(stemme["typeid"], "Ukendt")),
            )

    conn.commit()
    conn.close()


def main():
    print("Setting up database...")
    init_db()

    print("Finding current parliamentary session...")
    periode_id = get_current_periode_id()
    print(f"  -> periode id {periode_id}")

    print("Fetching current MPs and their party...")
    mps = fetch_current_mps(periode_id)
    fill_in_mp_names(mps)
    print(f"  -> {len(mps)} current MPs")

    print("Fetching bills and votes for the current session (this takes a bit - one request per bill)...")
    bills = fetch_bills_with_votes(periode_id)
    print(f"  -> {len(bills)} bills with recorded votes")

    print("Saving to politik.db...")
    save_to_db(mps, bills)
    print("Done.")


if __name__ == "__main__":
    main()
