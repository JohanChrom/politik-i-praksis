import math
import re

from flask import Flask, render_template, abort, request

from database import get_connection

app = Flask(__name__)

@app.template_filter("dk_number")
def dk_number(n):
    """Danish thousands separator: 532637 -> 532.637"""
    return f"{n:,}".replace(",", ".")


SPLIT_RE =re.compile(r"\((\d+)\. samling\)")
VALGPERIODE_LABELS = ["Nuværende valgperiode", "Forrige valgperiode"]
VOTE_TYPES = ["For", "Imod", "Fravær", "Hverken for eller imod"]
PAGE_SIZE = 50


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


def get_selected_periode(conn, allow_alle=False):
    """The chosen samling's id, or "alle" (only when allow_alle) for every samling."""
    if allow_alle and request.args.get("periode") == "alle":
        return "alle"
    default_row = conn.execute("SELECT id FROM periode ORDER BY startdato DESC LIMIT 1").fetchone()
    default_id = default_row["id"] if default_row else None
    return request.args.get("periode", type=int) or default_id


def filter_clause(committee_id, tema_id):
    """Extra ' AND ...' SQL (plus its params) for the Udvalg and Tema filters,
    shared by /bills and /mp/<id>. Expects the query to be on `bill`."""
    where, params = "", []
    if committee_id:
        where += " AND bill.committee_id = ?"
        params.append(committee_id)
    if tema_id:
        where += """ AND EXISTS (SELECT 1 FROM bill_emneord
                                 JOIN emneord_tema ON emneord_tema.emneord_id = bill_emneord.emneord_id
                                 WHERE bill_emneord.bill_id = bill.id AND emneord_tema.tema_id = ?)"""
        params.append(tema_id)
    return where, params


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


def attach_extras_per_periode(conn, bills):
    """attach_bill_extras for a list that can span several samlinger (the
    party breakdown is per samling, so build it once per samling present)."""
    breakdowns = {}
    result = []
    for bill in bills:
        pid = bill["periode_id"]
        if pid not in breakdowns:
            breakdowns[pid] = get_party_vote_breakdowns(conn, pid)
        result.append(attach_bill_extras(bill, breakdowns[pid]))
    return result


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
    selected_committee = request.args.get("udvalg", type=int)

    committees = conn.execute(
        """SELECT DISTINCT committee.id, committee.navn
           FROM mp_committee JOIN committee ON committee.id = mp_committee.committee_id
           WHERE mp_committee.periode_id = ?
           ORDER BY committee.navn""",
        (selected_periode,),
    ).fetchall()

    where = "mp_period.periode_id = ?"
    params = [selected_periode]
    if selected_committee:
        where += """ AND EXISTS (
            SELECT 1 FROM mp_committee
            WHERE mp_id = mp.id AND periode_id = mp_period.periode_id AND committee_id = ?
        )"""
        params.append(selected_committee)

    mps = conn.execute(
        f"""SELECT mp.id, mp.navn, mp_period.party,
                   (SELECT GROUP_CONCAT(committee.navn, '|')
                    FROM mp_committee
                    JOIN committee ON committee.id = mp_committee.committee_id
                    WHERE mp_committee.mp_id = mp.id
                      AND mp_committee.periode_id = mp_period.periode_id) AS committees
           FROM mp_period JOIN mp ON mp_period.mp_id = mp.id
           WHERE {where}
           ORDER BY mp.navn""",
        params,
    ).fetchall()
    conn.close()

    # filtered in Python rather than SQL LIKE, so æ/ø/å case-fold correctly
    q = request.args.get("q", "").strip()
    if q:
        mps = [mp for mp in mps if q.lower() in mp["navn"].lower()]

    return render_template(
        "mp_list.html", mps=mps, periode_groups=periode_groups, selected_periode=selected_periode, q=q,
        committees=committees, selected_committee=selected_committee,
    )


@app.route("/mp/<int:mp_id>")
def mp_detail(mp_id):
    # only one sortable column on this page, so just a plain asc/desc toggle
    # (checked against a fixed literal before use in SQL - never the raw
    # request value - so this stays safe from SQL injection)
    direction = "asc" if request.args.get("dir") == "asc" else "desc"
    order_by = f"bill.dato {'ASC' if direction == 'asc' else 'DESC'}"
    view = "forslag" if request.args.get("view") == "forslag" else "stemmer"
    selected_periode = request.args.get("periode", type=int)  # None = alle samlinger
    selected_committee = request.args.get("udvalg", type=int)
    selected_tema = request.args.get("tema", type=int)

    conn = get_connection()
    mp = conn.execute("SELECT id, navn FROM mp WHERE id = ?", (mp_id,)).fetchone()
    if mp is None:
        abort(404)

    all_periods = conn.execute(
        """SELECT periode.id, periode.titel, mp_period.party,
                  (SELECT GROUP_CONCAT(committee.navn, ', ')
                   FROM mp_committee
                   JOIN committee ON committee.id = mp_committee.committee_id
                   WHERE mp_committee.mp_id = mp_period.mp_id
                     AND mp_committee.periode_id = periode.id) AS committees
           FROM mp_period JOIN periode ON mp_period.periode_id = periode.id
           WHERE mp_period.mp_id = ?
           ORDER BY periode.startdato DESC""",
        (mp_id,),
    ).fetchall()
    periods_by_id = {p["id"]: p for p in all_periods}

    # Samling dropdown: only the samlinger this MP actually sat in
    mp_period_ids = {p["id"] for p in all_periods}
    periode_groups = [
        (label, [p for p in group if p["id"] in mp_period_ids])
        for label, group in get_periode_groups(conn)
    ]
    periode_groups = [(label, group) for label, group in periode_groups if group]

    # Udvalg / Tema options: only ones that occur on bills in the current view
    if view == "forslag":
        view_bill_ids = "SELECT bill_id FROM bill_sponsor WHERE sponsor_id = ?"
    else:
        view_bill_ids = "SELECT bill_id FROM vote WHERE mp_id = ?"
    option_where = f"bill.id IN ({view_bill_ids})"
    option_params = [mp_id]
    if selected_periode:
        option_where += " AND bill.periode_id = ?"
        option_params.append(selected_periode)
    committees = conn.execute(
        f"""SELECT DISTINCT committee.id, committee.navn
            FROM bill JOIN committee ON bill.committee_id = committee.id
            WHERE {option_where} ORDER BY committee.navn""",
        option_params,
    ).fetchall()
    tema_options = conn.execute(
        f"""SELECT DISTINCT tema.id, tema.navn
            FROM bill JOIN bill_emneord ON bill_emneord.bill_id = bill.id
                      JOIN emneord_tema ON emneord_tema.emneord_id = bill_emneord.emneord_id
                      JOIN tema ON tema.id = emneord_tema.tema_id
            WHERE {option_where} ORDER BY tema.navn""",
        option_params,
    ).fetchall()

    filter_where, filter_params = filter_clause(selected_committee, selected_tema)
    periode_where, periode_params = (" AND bill.periode_id = ?", [selected_periode]) if selected_periode else ("", [])

    # one query over all the MP's samlinger, newest samling first, so the page
    # can be cut with LIMIT/OFFSET; the rows are grouped into per-samling
    # sections afterwards
    if view == "forslag":
        from_sql = """FROM bill
                      JOIN bill_sponsor ON bill_sponsor.bill_id = bill.id AND bill_sponsor.sponsor_id = ?
                      JOIN periode ON bill.periode_id = periode.id
                      LEFT JOIN committee ON bill.committee_id = committee.id"""
        from_params = [mp_id]
        where_sql = "1 = 1"
        extra_select = """, (SELECT GROUP_CONCAT(sponsor.navn, ', ')
                            FROM bill_sponsor
                            JOIN sponsor ON sponsor.id = bill_sponsor.sponsor_id
                            WHERE bill_sponsor.bill_id = bill.id
                              AND sponsor.id != ?) AS co_sponsor_navne"""
        select_params = [mp_id]
    else:
        from_sql = """FROM vote
                      JOIN bill ON vote.bill_id = bill.id
                      JOIN periode ON bill.periode_id = periode.id
                      LEFT JOIN committee ON bill.committee_id = committee.id"""
        from_params = []
        where_sql = "vote.mp_id = ?"
        extra_select = """, vote.vote_type,
                          (SELECT GROUP_CONCAT(sponsor.navn, ', ')
                           FROM bill_sponsor
                           JOIN sponsor ON sponsor.id = bill_sponsor.sponsor_id
                           WHERE bill_sponsor.bill_id = bill.id) AS sponsor_navne"""
        select_params = []
    where_sql += periode_where + filter_where
    where_params = ([mp_id] if view == "stemmer" else []) + periode_params + filter_params

    total = conn.execute(
        f"SELECT COUNT(*) {from_sql} WHERE {where_sql}", (*from_params, *where_params)
    ).fetchone()[0]
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(1, request.args.get("side", 1, type=int)), pages)

    rows = conn.execute(
        f"""SELECT bill.id, bill.periode_id, periode.titel AS periode_titel,
                   bill.titelkort, bill.nummer, bill.dato, bill.vedtaget,
                   bill.resume, bill.lovnummer, bill.lovnummerdato, bill.retsinformationsurl,
                   committee.navn AS committee_navn,
                   (SELECT GROUP_CONCAT(emneord.tekst, ', ')
                    FROM bill_emneord
                    JOIN emneord ON emneord.id = bill_emneord.emneord_id
                    WHERE bill_emneord.bill_id = bill.id) AS emneord_tekster
                   {extra_select}
            {from_sql}
            WHERE {where_sql}
            ORDER BY periode.startdato DESC, {order_by}, bill.id
            LIMIT ? OFFSET ?""",
        (*select_params, *from_params, *where_params, PAGE_SIZE, (page - 1) * PAGE_SIZE),
    ).fetchall()
    bills = attach_extras_per_periode(conn, rows)

    sections = []
    for bill in bills:
        if not sections or sections[-1]["periode"]["id"] != bill["periode_id"]:
            info = periods_by_id.get(bill["periode_id"])
            sections.append({
                "periode": {
                    "id": bill["periode_id"],
                    "titel": bill["periode_titel"],
                    "party": info["party"] if info else "-",
                    "committees": info["committees"] if info else None,
                },
                "bills": [],
            })
        sections[-1]["bills"].append(bill)

    conn.close()
    return render_template(
        "mp_detail.html", mp=mp, sections=sections, dir=direction, view=view,
        has_periods=bool(all_periods), total=total, page=page, pages=pages,
        periode_groups=periode_groups, selected_periode=selected_periode,
        committees=committees, selected_committee=selected_committee,
        tema_options=tema_options, selected_tema=selected_tema,
    )


@app.route("/bills")
def bill_list():
    conn = get_connection()
    periode_groups = get_periode_groups(conn)
    selected_periode = get_selected_periode(conn, allow_alle=True)
    show_alle = selected_periode == "alle"
    # alphabetical order means little across ten years, so "alle" defaults to newest first
    order_by, sort, direction = date_order_by("bill.dato DESC" if show_alle else "bill.titelkort")
    selected_committee = request.args.get("udvalg", type=int)
    selected_tema = request.args.get("tema", type=int)

    periode_where, periode_params = ("1 = 1", []) if show_alle else ("bill.periode_id = ?", [selected_periode])

    committees = conn.execute(
        f"""SELECT DISTINCT committee.id, committee.navn
            FROM bill JOIN committee ON bill.committee_id = committee.id
            WHERE {periode_where}
            ORDER BY committee.navn""",
        periode_params,
    ).fetchall()

    tema_options = conn.execute(
        f"""SELECT DISTINCT tema.id, tema.navn
            FROM bill JOIN bill_emneord ON bill_emneord.bill_id = bill.id
                      JOIN emneord_tema ON emneord_tema.emneord_id = bill_emneord.emneord_id
                      JOIN tema ON tema.id = emneord_tema.tema_id
            WHERE {periode_where}
            ORDER BY tema.navn""",
        periode_params,
    ).fetchall()

    filter_where, filter_params = filter_clause(selected_committee, selected_tema)
    where = periode_where + filter_where
    params = periode_params + filter_params

    total = conn.execute(f"SELECT COUNT(*) FROM bill WHERE {where}", params).fetchone()[0]
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(1, request.args.get("side", 1, type=int)), pages)

    bills = conn.execute(
        f"""SELECT bill.id, bill.periode_id, periode.titel AS periode_titel,
                   bill.titelkort, bill.nummer, bill.vedtaget, bill.dato,
                   bill.resume, bill.lovnummer, bill.lovnummerdato, bill.retsinformationsurl,
                   committee.navn AS committee_navn,
                   (SELECT GROUP_CONCAT(sponsor.navn, ', ')
                    FROM bill_sponsor
                    JOIN sponsor ON sponsor.id = bill_sponsor.sponsor_id
                    WHERE bill_sponsor.bill_id = bill.id) AS sponsor_navne,
                   (SELECT GROUP_CONCAT(emneord.tekst, ', ')
                    FROM bill_emneord
                    JOIN emneord ON emneord.id = bill_emneord.emneord_id
                    WHERE bill_emneord.bill_id = bill.id) AS emneord_tekster
           FROM bill
           JOIN periode ON bill.periode_id = periode.id
           LEFT JOIN committee ON bill.committee_id = committee.id
           WHERE {where}
           ORDER BY {order_by}, bill.id
           LIMIT ? OFFSET ?""",
        (*params, PAGE_SIZE, (page - 1) * PAGE_SIZE),
    ).fetchall()

    bills = attach_extras_per_periode(conn, bills)
    conn.close()

    return render_template(
        "bill_list.html",
        bills=bills,
        total=total,
        page=page,
        pages=pages,
        show_alle=show_alle,
        sort=sort,
        dir=direction,
        periode_groups=periode_groups,
        selected_periode=selected_periode,
        committees=committees,
        selected_committee=selected_committee,
        tema_options=tema_options,
        selected_tema=selected_tema,
    )


if __name__ == "__main__":
    app.run(debug=True)
