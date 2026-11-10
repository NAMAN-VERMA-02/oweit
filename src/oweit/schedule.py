"""One gentle reminder a day, placed in a free slot, skipped when the user is busy.

Day plan (made on the first tick of a day, remade if that day's calendar changes):
  packed day -> non-urgent items move to the next free day; only promises due today get a
                short nudge, at the first free slot
  normal day -> one digest of everything due for a reminder, at the first free slot
At the slot, tick() checks the live signals: in a meeting -> wait for the next tick;
Do Not Disturb or a full-screen app -> skip today (non-urgent items move to the next free day)
and keep only the due-today nudge for when the user is free again.
"""

import hashlib
import urllib.request
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import icalendar
import recurring_ical_events

REPEAT_DAYS = 7  # overdue or undated promises come back once a week until done or snoozed
LOOKAHEAD_DAYS = 14

Events = list[tuple[datetime, datetime]]


@dataclass
class Settings:
    day_start: time = time(9)
    day_end: time = time(22)
    digest_after: time = time(10)
    packed_ratio: float = 0.7
    min_slot: timedelta = timedelta(minutes=20)

    @classmethod
    def from_config(cls, cfg: dict) -> "Settings":
        t = time.fromisoformat
        return cls(
            t(cfg["day_start"]),
            t(cfg["day_end"]),
            t(cfg["digest_after"]),
            float(cfg["packed_ratio"]),
            timedelta(minutes=int(cfg["min_slot_minutes"])),
        )


# --- calendar -------------------------------------------------------------


def load_calendar(src: str, cache: Path | None = None) -> icalendar.Calendar | None:
    """Read an .ics path or URL (e.g. Google Calendar's secret iCal address).

    If the URL can't be fetched, the last good copy in `cache` is used.
    """
    if not src:
        return None
    try:
        if src.startswith(("http://", "https://")):
            with urllib.request.urlopen(src, timeout=20) as r:
                data = r.read()
        else:
            data = Path(src).read_bytes()
        if cache:
            cache.write_bytes(data)
    except OSError:
        if not (cache and cache.exists()):
            raise
        data = cache.read_bytes()
    return icalendar.Calendar.from_ical(data)


def _naive_local(dt: datetime) -> datetime:
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


def events_on(cal: icalendar.Calendar | None, day: date) -> Events:
    """Timed events overlapping `day`, clipped to it. All-day and 'free' events don't count."""
    if cal is None:
        return []
    lo = datetime.combine(day, time.min)
    hi = lo + timedelta(days=1)
    out = []
    for ev in recurring_ical_events.of(cal).between(lo, hi):
        start, end = ev.start, ev.end
        if not isinstance(start, datetime) or str(ev.get("TRANSP", "")).upper() == "TRANSPARENT":
            continue
        start, end = _naive_local(start), _naive_local(end)
        if end > lo and start < hi:
            out.append((max(start, lo), min(end, hi)))
    return _merge(out)


def _merge(events: Events) -> Events:
    merged: Events = []
    for s, e in sorted(events):
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def busy_ratio(events: Events, day: date, s: Settings) -> float:
    ws, we = datetime.combine(day, s.day_start), datetime.combine(day, s.day_end)
    booked = sum(((min(e, we) - max(b, ws)) for b, e in events if e > ws and b < we), timedelta())
    return booked / (we - ws)


def is_packed(events: Events, day: date, s: Settings) -> bool:
    return busy_ratio(events, day, s) >= s.packed_ratio


def free_slot(events: Events, day: date, s: Settings, now: datetime) -> datetime:
    """Start of the first gap of at least min_slot after max(now, digest_after).

    With no such gap, the end of the last event (the user is free then, if late).
    """
    t = max(now, datetime.combine(day, s.digest_after))
    for b, e in events:
        if e <= t:
            continue
        if b - t >= s.min_slot:
            return t
        t = max(t, e)
    return t


def next_free_day(cal, day: date, s: Settings) -> date:
    for i in range(1, LOOKAHEAD_DAYS + 1):
        d = day + timedelta(days=i)
        if not is_packed(events_on(cal, d), d, s):
            return d
    return day + timedelta(days=1)


# --- promises in play -----------------------------------------------------


def pool(conn, day: date) -> list:
    return conn.execute(
        "SELECT * FROM promises WHERE status='open' AND remind_on<=? ORDER BY due IS NULL, due",
        (day.isoformat(),),
    ).fetchall()


def next_remind(due: str | None, day: date) -> date:
    if due and day < date.fromisoformat(due):
        return date.fromisoformat(due)  # one more on the day itself
    return day + timedelta(days=REPEAT_DAYS)


def _defer(conn, rows, to: date, day: date, reason: str, now: datetime) -> None:
    for r in rows:
        due = date.fromisoformat(r["due"]) if r["due"] else None
        # never move a reminder past the deadline: on its due day it becomes the urgent nudge
        target = due if due and day < due < to else to
        conn.execute("UPDATE promises SET remind_on=? WHERE id=?", (target.isoformat(), r["id"]))
        conn.execute(
            "INSERT INTO log VALUES (?,?,?,?,?)",
            (now.isoformat(), day.isoformat(), r["id"], "deferred", reason),
        )


def _plan(conn, cal, events: Events, day: date, s: Settings, now: datetime) -> str:
    if not is_packed(events, day, s):
        return "digest"
    urgent_day = day.isoformat()
    later = [r for r in pool(conn, day) if r["due"] != urgent_day]
    if later:
        _defer(conn, later, next_free_day(cal, day, s), day, "calendar", now)
    return "nudge"


# --- the tick -------------------------------------------------------------

Notifier = Callable[[date, list, str], None]


def tick(
    conn, cal, s: Settings, now: datetime, probe: Callable[[], str | None], notify: Notifier
) -> str:
    """Run once (Task Scheduler calls this every 30 min). Returns what happened."""
    day = now.date()
    if not (s.day_start <= now.time() < s.day_end):
        return "quiet hours"
    row = conn.execute("SELECT * FROM days WHERE day=?", (day.isoformat(),)).fetchone()
    if row and row["sent_at"]:
        return "already reminded today"

    events = events_on(cal, day)
    cal_hash = hashlib.sha1(repr(events).encode()).hexdigest()[:12]
    if row is None or row["cal_hash"] != cal_hash:
        mode = _plan(conn, cal, events, day, s, now)
        if row is not None and row["mode"] == "nudge":
            mode = "nudge"  # today was already skipped; a calendar change doesn't un-skip it
        slot = free_slot(events, day, s, now)
        if row is None:
            conn.execute(
                "INSERT INTO days(day, cal_hash, mode, slot) VALUES (?,?,?,?)",
                (day.isoformat(), cal_hash, mode, slot.isoformat()),
            )
        else:  # the day's schedule changed before we reminded: re-plan around it
            conn.execute(
                "UPDATE days SET cal_hash=?, mode=?, slot=?, reschedules=reschedules+1 WHERE day=?",
                (cal_hash, mode, slot.isoformat(), day.isoformat()),
            )
        row = conn.execute("SELECT * FROM days WHERE day=?", (day.isoformat(),)).fetchone()

    slot = datetime.fromisoformat(row["slot"])
    if now < slot:
        return f"waiting for {slot:%H:%M}"
    if any(b <= now < e for b, e in events):
        return "in a meeting, retry next tick"

    items = pool(conn, day)
    urgent = [r for r in items if r["due"] == day.isoformat()]
    why = probe()
    if why:
        if row["mode"] == "digest":  # busy at the slot: skip today, keep only the due-today nudge
            later = [r for r in items if r["due"] != day.isoformat()]
            if later:
                _defer(conn, later, next_free_day(cal, day, s), day, why, now)
            conn.execute("UPDATE days SET mode='nudge' WHERE day=?", (day.isoformat(),))
        return f"busy ({why}), retry next tick"

    send = urgent if row["mode"] == "nudge" else items
    if not send:
        return "nothing to remind"
    notify(day, send, row["mode"])
    for r in send:
        conn.execute(
            "UPDATE promises SET remind_on=? WHERE id=?",
            (next_remind(r["due"], day).isoformat(), r["id"]),
        )
        conn.execute(
            "INSERT INTO log VALUES (?,?,?,?,?)",
            (now.isoformat(), day.isoformat(), r["id"], "reminded", row["mode"]),
        )
    conn.execute("UPDATE days SET sent_at=? WHERE day=?", (now.isoformat(), day.isoformat()))
    return f"sent {row['mode']} with {len(send)} promise(s)"


# --- numbers --------------------------------------------------------------


def stats(conn) -> dict:
    per = Counter(r[0] for r in conn.execute("SELECT promise_id FROM log WHERE kind='reminded'"))
    deferred = conn.execute(
        "SELECT reason, COUNT(*) FROM log WHERE kind='deferred' GROUP BY reason"
    ).fetchall()
    scans = conn.execute("SELECT SUM(messages), SUM(mine), SUM(llm_calls) FROM scans").fetchone()
    days = conn.execute("SELECT COUNT(*), COUNT(sent_at), SUM(reschedules) FROM days").fetchone()
    promises = conn.execute("SELECT COUNT(*), SUM(status='done') FROM promises").fetchone()
    return {
        "promises": promises[0],
        "done": promises[1] or 0,
        "reminders": sum(per.values()),
        "reminders_per_promise_mean": round(sum(per.values()) / len(per), 2) if per else 0,
        "reminders_per_promise_max": max(per.values(), default=0),
        "deferrals": {r[0]: r[1] for r in deferred},
        "promises_moved": conn.execute(
            "SELECT COUNT(DISTINCT promise_id) FROM log WHERE kind='deferred'"
        ).fetchone()[0],
        "days_planned": days[0],
        "days_notified": days[1],
        "reschedules": days[2] or 0,
        "messages_scanned": scans[0] or 0,
        "llm_calls": scans[2] or 0,
    }
