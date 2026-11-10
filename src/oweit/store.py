import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS promises(
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE,
  source TEXT, msg_ts TEXT, to_whom TEXT, what TEXT,
  due TEXT,                 -- ISO date or NULL
  quote TEXT,
  status TEXT NOT NULL DEFAULT 'open',   -- open | done
  remind_on TEXT NOT NULL,  -- next day it may appear in a reminder (snooze/defer move this)
  created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen(key TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS days(
  day TEXT PRIMARY KEY, cal_hash TEXT, mode TEXT, slot TEXT, sent_at TEXT,
  reschedules INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS log(ts TEXT, day TEXT, promise_id INTEGER, kind TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS scans(
  ts TEXT, source TEXT, messages INTEGER, mine INTEGER, candidates INTEGER,
  llm_calls INTEGER, promises INTEGER
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def first_remind_day(due: date | None, today: date) -> date:
    # a day's notice before the deadline; undated promises show up in tomorrow's digest
    return max(today, due - timedelta(days=1)) if due else today + timedelta(days=1)


def add_promise(conn, key: str, source: str, msg_ts: datetime, p, today: date) -> bool:
    cur = conn.execute(
        "INSERT OR IGNORE INTO promises(key, source, msg_ts, to_whom, what, due, quote, remind_on,"
        " created) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            key,
            source,
            msg_ts.isoformat(),
            p.to,
            p.what,
            p.due.isoformat() if p.due else None,
            p.quote,
            first_remind_day(p.due, today).isoformat(),
            today.isoformat(),
        ),
    )
    return cur.rowcount == 1


def is_seen(conn, key: str) -> bool:
    return conn.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone() is not None


def mark_seen(conn, keys: list[str]) -> None:
    conn.executemany("INSERT OR IGNORE INTO seen VALUES (?)", [(k,) for k in keys])


def promises(conn, include_done: bool = False) -> list[sqlite3.Row]:
    where = "" if include_done else "WHERE status='open'"
    return conn.execute(f"SELECT * FROM promises {where} ORDER BY due IS NULL, due, id").fetchall()


def set_done(conn, pid: int) -> bool:
    return conn.execute("UPDATE promises SET status='done' WHERE id=?", (pid,)).rowcount == 1


def snooze(conn, pid: int, until: date) -> bool:
    cur = conn.execute("UPDATE promises SET remind_on=? WHERE id=?", (until.isoformat(), pid))
    return cur.rowcount == 1


def log_scan(
    conn,
    source: str,
    messages: int,
    mine: int,
    candidates: int,
    calls: int,
    found: int,
    now: datetime,
) -> None:
    conn.execute(
        "INSERT INTO scans VALUES (?,?,?,?,?,?,?)",
        (now.isoformat(timespec="seconds"), source, messages, mine, candidates, calls, found),
    )
