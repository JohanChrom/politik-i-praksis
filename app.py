from flask import Flask, render_template, abort, request

from database import get_connection

app = Flask(__name__)


def date_order_by(default_order_by):
    """Shared sort-by-date logic for the two date columns. Only 'dato' is
    sortable for now, so `sort`/`dir` are checked against a fixed set of
    literal values before being used in the SQL - never the raw request
    value itself - so this stays safe from SQL injection.
    """
    sort = request.args.get("sort")
    direction = "desc" if request.args.get("dir") == "desc" else "asc"
    if sort == "dato":
        return f"bill.dato {'DESC' if direction == 'desc' else 'ASC'}", sort, direction
    return default_order_by, sort, direction


@app.route("/")
def mp_list():
    conn = get_connection()
    mps = conn.execute("SELECT id, navn, party FROM mp ORDER BY navn").fetchall()
    conn.close()
    return render_template("mp_list.html", mps=mps)


@app.route("/mp/<int:mp_id>")
def mp_detail(mp_id):
    order_by, sort, direction = date_order_by("bill.titelkort")
    conn = get_connection()
    mp = conn.execute("SELECT id, navn, party FROM mp WHERE id = ?", (mp_id,)).fetchone()
    if mp is None:
        abort(404)
    votes = conn.execute(
        f"""SELECT bill.titelkort, bill.nummer, bill.dato, vote.vote_type
           FROM vote JOIN bill ON vote.bill_id = bill.id
           WHERE vote.mp_id = ?
           ORDER BY {order_by}""",
        (mp_id,),
    ).fetchall()
    conn.close()
    return render_template("mp_detail.html", mp=mp, votes=votes, sort=sort, dir=direction)


@app.route("/bills")
def bill_list():
    order_by, sort, direction = date_order_by("bill.titelkort")
    conn = get_connection()
    bills = conn.execute(
        f"""SELECT bill.id, bill.titelkort, bill.nummer, bill.vedtaget, bill.dato, committee.navn AS committee_navn
           FROM bill LEFT JOIN committee ON bill.committee_id = committee.id
           ORDER BY {order_by}"""
    ).fetchall()
    conn.close()
    return render_template("bill_list.html", bills=bills, sort=sort, dir=direction)


if __name__ == "__main__":
    app.run(debug=True)
