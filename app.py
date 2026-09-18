from flask import Flask, render_template, abort

from database import get_connection

app = Flask(__name__)


@app.route("/")
def mp_list():
    conn = get_connection()
    mps = conn.execute("SELECT id, navn, party FROM mp ORDER BY navn").fetchall()
    conn.close()
    return render_template("mp_list.html", mps=mps)


@app.route("/mp/<int:mp_id>")
def mp_detail(mp_id):
    conn = get_connection()
    mp = conn.execute("SELECT id, navn, party FROM mp WHERE id = ?", (mp_id,)).fetchone()
    if mp is None:
        abort(404)
    votes = conn.execute(
        """SELECT bill.titelkort, bill.nummer, vote.vote_type
           FROM vote JOIN bill ON vote.bill_id = bill.id
           WHERE vote.mp_id = ?
           ORDER BY bill.titelkort""",
        (mp_id,),
    ).fetchall()
    conn.close()
    return render_template("mp_detail.html", mp=mp, votes=votes)


@app.route("/bills")
def bill_list():
    conn = get_connection()
    bills = conn.execute(
        """SELECT bill.id, bill.titelkort, bill.nummer, bill.vedtaget, committee.navn AS committee_navn
           FROM bill LEFT JOIN committee ON bill.committee_id = committee.id
           ORDER BY bill.titelkort"""
    ).fetchall()
    conn.close()
    return render_template("bill_list.html", bills=bills)


if __name__ == "__main__":
    app.run(debug=True)
