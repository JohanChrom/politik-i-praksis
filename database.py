import sqlite3

DB_PATH = "politik.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS periode (
            id INTEGER PRIMARY KEY,
            kode TEXT,
            titel TEXT NOT NULL,
            startdato TEXT,
            slutdato TEXT
        );

        CREATE TABLE IF NOT EXISTS mp (
            id INTEGER PRIMARY KEY,
            navn TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS mp_period (
            mp_id INTEGER NOT NULL REFERENCES mp(id),
            periode_id INTEGER NOT NULL REFERENCES periode(id),
            party TEXT,
            PRIMARY KEY (mp_id, periode_id)
        );

        CREATE TABLE IF NOT EXISTS committee (
            id INTEGER PRIMARY KEY,
            navn TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bill (
            id INTEGER PRIMARY KEY,
            periode_id INTEGER NOT NULL REFERENCES periode(id),
            titel TEXT NOT NULL,
            titelkort TEXT,
            nummer TEXT,
            committee_id INTEGER REFERENCES committee(id),
            vedtaget INTEGER,
            konklusion TEXT,
            dato TEXT
        );

        CREATE TABLE IF NOT EXISTS vote (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id INTEGER NOT NULL REFERENCES bill(id),
            mp_id INTEGER NOT NULL REFERENCES mp(id),
            vote_type TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()
