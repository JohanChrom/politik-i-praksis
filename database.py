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
        DROP TABLE IF EXISTS vote;
        DROP TABLE IF EXISTS bill;
        DROP TABLE IF EXISTS committee;
        DROP TABLE IF EXISTS mp;

        CREATE TABLE mp (
            id INTEGER PRIMARY KEY,
            navn TEXT NOT NULL,
            party TEXT
        );

        CREATE TABLE committee (
            id INTEGER PRIMARY KEY,
            navn TEXT NOT NULL
        );

        CREATE TABLE bill (
            id INTEGER PRIMARY KEY,
            titel TEXT NOT NULL,
            titelkort TEXT,
            nummer TEXT,
            committee_id INTEGER REFERENCES committee(id),
            vedtaget INTEGER,
            konklusion TEXT
        );

        CREATE TABLE vote (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id INTEGER NOT NULL REFERENCES bill(id),
            mp_id INTEGER NOT NULL REFERENCES mp(id),
            vote_type TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()
