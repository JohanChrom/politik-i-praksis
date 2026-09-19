import re

from flask import Flask, render_template, abort, request

from database import get_connection

app = Flask(__name__)

SPLIT_RE = re.compile(r"\((\d+)\. samling\)")
VALGPERIODE_LABELS = ["Nuværende valgperiode", "Forrige valgperiode"]
VOTE_TYPES = ["For", "Imod", "Fravær", "Hverken for eller imod"]


def group_by_valgperiode(periods_oldest_first):
    """A samling titled '(2. samling)' or higher always marks a real
    election and the start of a new valgperiode (confirmed against the
    entire 1952-2026 history) - a plain-titled year or '(1. samling)' just
    continues whichever valgperiode is already running.
    """
    groups = [[]]
    for periode in periods_oldest_first:
        match = SPLIT_RE.search(periode["titel"])
        if match and int(match.group(1)) >= 2:
            groups.append([])
        groups[-1].append(periode)
    return [group for group in groups if group]  # drop a dangling empty leading group


def get_periode_groups(conn):
    """Returns [(label, [periode rows newest-first]), ...], newest group first."""
    periods = conn.execute("SELECT * FROM periode ORDER BY startdato ASC").fetchall()
    groups = list(reversed(group_by_valgperiode(periods)))
    result = []
    for i, group in enumerate(groups):
        label = VALGPERIODE_LABELS[i] if i < len(VALGPERIODE_LABELS) else f"Valgperiode {len(groups) - i}"
        result.append((label, list(reversed(group))))
    return result


def get_selected_periode(conn):
    default_row = conn.execute("SELECT id FROM periode ORDER BY startdato DESC LIMIT 1").fetchone()
    default_id = default_row["id"] if default_row else None
    return request.args.get("periode", type=int) or default_id


def date_order_by(default_order_by):
    """Shared sort-by-date logic for the bill list's date column. Only
    'dato' is sortable for now, so `sort`/`dir` are checked against a fixed
    set of literal values before being used in the SQL - never the raw
    request value itself - so this stays safe from SQL injection.
    """
    sort = request.args.get("sort")
    direction = "desc" if request.args.get("dir") == "desc" else "asc"
    if sort == "dato":
        return f"bill.dato {'DESC' if direction == 'desc' else 'ASC'}", sort, direction
    return default_order_by, sort, direction


DK_MONTHS = ["januar", "februar", "marts", "april", "maj", "juni", "juli",
             "august", "september", "oktober", "november", "december"]


def format_dk_date(iso_date):
    year, month, day = iso_date[:10].split("-")
    return f"{int(day)}. {DK_MONTHS[int(month) - 1]} {year}"


def law_text_url(bill):
    """Where to read the actual law text, if this bill became one - the
    retsinformation.dk URL scheme (verified against a real law: HTTP 200 for
    a real lovnummer/year, HTTP 404 for a made-up one), built from lovnummer
    + lovnummerdato's year. Folketinget's own retsinformationsurl field is
    used first if it's ever populated. Bills that didn't pass have no
    lovnummer, so they correctly get no link."""
    if bill["retsinformationsurl"]:
        return bill["retsinformationsurl"]
    if bill["lovnummer"] and bill["lovnummerdato"]:
        return f"https://www.retsinformation.dk/eli/lta/{bill['lovnummerdato'][:4]}/{bill['lovnummer']}"
    return None


def get_party_vote_breakdowns(conn, periode_id):
    """Returns a party_votes_for(bill_id) function giving the per-party
    for/imod/fravær/hverken vote counts for that bill, scoped to this one
    periode - party composition (who's in a multi-member party) differs
    across samlinger, so this has to be computed per periode rather than
    once globally."""
    parties = conn.execute(
        """SELECT party FROM mp_period WHERE periode_id = ?
           GROUP BY party HAVING COUNT(*) > 1 ORDER BY COUNT(*) DESC""",
        (periode_id,),
    ).fetchall()
    party_names = [p["party"] for p in parties]

    vote_rows = conn.execute(
        f"""SELECT bill.id AS bill_id, mp_period.party, vote.vote_type, COUNT(*) AS n
            FROM vote
            JOIN bill ON vote.bill_id = bill.id
            JOIN mp_period ON vote.mp_id = mp_period.mp_id AND mp_period.periode_id = bill.periode_id
            WHERE bill.periode_id = ? AND mp_period.party IN ({",".join("?" * len(party_names))})
            GROUP BY bill.id, mp_period.party, vote.vote_type""",
        (periode_id, *party_names),
    ).fetchall() if party_names else []

    votes_by_bill = {}
    for row in vote_rows:
        votes_by_bill.setdefault(row["bill_id"], {}).setdefault(row["party"], {})[row["vote_type"]] = row["n"]

    def party_votes_for(bill_id):
        bill_votes = votes_by_bill.get(bill_id, {})
        return [
            {"party": party, **{vt: bill_votes.get(party, {}).get(vt, 0) for vt in VOTE_TYPES}}
            for party in party_names
        ]

    return party_votes_for


def attach_bill_extras(bill, party_votes_for):
    """Adds the law-text link and per-party breakdown (suppressed if every
    count would be zero) to a bill row, for the expandable details on
    /bills and /mp/<id>."""
    pv_list = party_votes_for(bill["id"])
    has_votes = any(pv[vt] for pv in pv_list for vt in VOTE_TYPES)
    return dict(bill, law_url=law_text_url(bill), party_votes=pv_list if has_votes else None)


@app.route("/")
def index():
    conn = get_connection()
    stats = {
        "bill_count": conn.execute("SELECT COUNT(*) FROM bill").fetchone()[0],
        "vote_count": conn.execute("SELECT COUNT(*) FROM vote").fetchone()[0],
        "periode_count": conn.execute("SELECT COUNT(*) FROM periode").fetchone()[0],
    }
    earliest = conn.execute("SELECT MIN(startdato) FROM periode").fetchone()[0]
    stats["data_start"] = format_dk_date(earliest) if earliest else None
    conn.close()
    return render_template("index.html", stats=stats)


@app.route("/medlemmer")
def mp_list():
    conn = get_connection()
    periode_groups = get_periode_groups(conn)
    selected_periode = get_selected_periode(conn)

    mps = conn.execute(
        """SELECT mp.id, mp.navn, mp_period.party
           FROM mp_period JOIN mp ON mp_period.mp_id = mp.id
           WHERE mp_period.periode_id = ?
           ORDER BY mp.navn""",
        (selected_periode,),
    ).fetchall()
    conn.close()
    return render_template(
        "mp_list.html", mps=mps, periode_groups=periode_groups, selected_periode=selected_periode
    )


@app.route("/mp/<int:mp_id>")
def mp_detail(mp_id):
    # only one sortable column on this page, so just a plain asc/desc toggle
    # (checked against a fixed literal before use in SQL - never the raw
    # request value - so this stays safe from SQL injection)
    direction = "asc" if request.args.get("dir") == "asc" else "desc"
    order_by = f"bill.dato {'ASC' if direction == 'asc' else 'DESC'}"

    conn = get_connection()
    mp = conn.execute("SELECT id, navn FROM mp WHERE id = ?", (mp_id,)).fetchone()
    if mp is None:
        abort(404)

    periods = conn.execute(
        """SELECT periode.id, periode.titel, mp_period.party
           FROM mp_period JOIN periode ON mp_period.periode_id = periode.id
           WHERE mp_period.mp_id = ?
           ORDER BY periode.startdato DESC""",
        (mp_id,),
    ).fetchall()

    sections = []
    for periode in periods:
        votes = conn.execute(
            f"""SELECT bill.id, bill.titelkort, bill.nummer, bill.dato, bill.vedtaget,
                       bill.resume, bill.lovnummer, bill.lovnummerdato, bill.retsinformationsurl,
                       committee.navn AS committee_navn,
                       (SELECT GROUP_CONCAT(sponsor.navn, ', ')
                        FROM bill_sponsor
                        JOIN sponsor ON sponsor.id = bill_sponsor.sponsor_id
                        WHERE bill_sponsor.bill_id = bill.id) AS sponsor_navne,
                       vote.vote_type
               FROM vote JOIN bill ON vote.bill_id = bill.id
               LEFT JOIN committee ON bill.committee_id = committee.id
               WHERE vote.mp_id = ? AND bill.periode_id = ?
               ORDER BY {order_by}""",
            (mp_id, periode["id"]),
        ).fetchall()

        party_votes_for = get_party_vote_breakdowns(conn, periode["id"])
        votes = [attach_bill_extras(v, party_votes_for) for v in votes]
        sections.append({"periode": periode, "votes": votes})

    conn.close()
    return render_template("mp_detail.html", mp=mp, sections=sections, dir=direction)


@app.route("/bills")
def bill_list():
    order_by, sort, direction = date_order_by("bill.titelkort")
    conn = get_connection()
    periode_groups = get_periode_groups(conn)
    selected_periode = get_selected_periode(conn)

    bills = conn.execute(
        f"""SELECT bill.id, bill.titelkort, bill.nummer, bill.vedtaget, bill.dato,
                   bill.resume, bill.lovnummer, bill.lovnummerdato, bill.retsinformationsurl,
                   committee.navn AS committee_navn,
                   (SELECT GROUP_CONCAT(sponsor.navn, ', ')
                    FROM bill_sponsor
                    JOIN sponsor ON sponsor.id = bill_sponsor.sponsor_id
                    WHERE bill_sponsor.bill_id = bill.id) AS sponsor_navne
           FROM bill LEFT JOIN committee ON bill.committee_id = committee.id
           WHERE bill.periode_id = ?
           ORDER BY {order_by}""",
        (selected_periode,),
    ).fetchall()

    party_votes_for = get_party_vote_breakdowns(conn, selected_periode)
    bills = [attach_bill_extras(b, party_votes_for) for b in bills]
    conn.close()

    return render_template(
        "bill_list.html",
        bills=bills,
        sort=sort,
        dir=direction,
        periode_groups=periode_groups,
        selected_periode=selected_periode,
    )


if __name__ == "__main__":
    app.run(debug=True)
