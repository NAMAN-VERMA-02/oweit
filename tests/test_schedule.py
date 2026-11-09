import random
from collections import Counter
from datetime import date, datetime, time, timedelta

import icalendar
import pytest

from oweit import store
from oweit.extract import Promise
from oweit.schedule import (
    Settings,
    busy_ratio,
    events_on,
    free_slot,
    next_free_day,
    next_remind,
    stats,
    tick,
)

MON = date(2026, 10, 12)  # a Monday
S = Settings()  # 09:00-22:00 window, digest from 10:00, packed at 70%, 20 min slots


def at(d: date, hhmm: str) -> datetime:
    return datetime.combine(d, time.fromisoformat(hhmm))


def cal(*spans, rrule=None, allday=None):
    c = icalendar.Calendar()
    c.add("prodid", "-//oweit-test//")
    c.add("version", "2.0")
    for i, (s, e) in enumerate(spans):
        ev = icalendar.Event()
        ev.add("uid", f"e{i}@test")
        ev.add("dtstart", s)
        ev.add("dtend", e)
        ev.add("summary", "class")
        if rrule:
            ev.add("rrule", rrule)
        c.add_component(ev)
    if allday:
        ev = icalendar.Event()
        ev.add("uid", "allday@test")
        ev.add("dtstart", allday)
        ev.add("dtend", allday + timedelta(days=1))
        c.add_component(ev)
    return c


def packed(d: date):
    """09:00-21:30 booked except 13:00-13:30: 92% of the window."""
    return [(at(d, "09:00"), at(d, "13:00")), (at(d, "13:30"), at(d, "21:30"))]


class Inbox:
    def __init__(self):
        self.sent = []

    def __call__(self, day, rows, mode):
        self.sent.append((day, mode, sorted(r["what"] for r in rows)))


def free():
    return None


@pytest.fixture
def db():
    conn = store.connect(":memory:")
    yield conn
    conn.close()


def owe(conn, what, due, today=MON):
    p = Promise("Riya", what, due, None, what)
    store.add_promise(conn, what, "whatsapp", at(today, "08:00"), p, today)


def remind_on(conn, what) -> date:
    row = conn.execute("SELECT remind_on FROM promises WHERE what=?", (what,)).fetchone()
    return date.fromisoformat(row[0])


def deferrals(conn):
    return conn.execute("SELECT reason, COUNT(*) FROM log WHERE kind='deferred' GROUP BY reason")


# --- free day ---------------------------------------------------------------


def test_free_day_sends_one_digest_at_first_free_slot(db):
    owe(db, "send notes", MON)  # due today
    owe(db, "return book", MON + timedelta(days=1))  # due tomorrow -> heads-up today
    c, inbox = cal((at(MON, "10:00"), at(MON, "11:00"))), Inbox()

    assert tick(db, c, S, at(MON, "09:30"), free, inbox) == "waiting for 11:00"
    assert tick(db, c, S, at(MON, "11:00"), free, inbox).startswith("sent digest")
    assert tick(db, c, S, at(MON, "11:30"), free, inbox) == "already reminded today"
    assert inbox.sent == [(MON, "digest", ["return book", "send notes"])]
    # heads-up given; it comes back on its due day, not every day
    assert remind_on(db, "return book") == MON + timedelta(days=1)
    assert remind_on(db, "send notes") == MON + timedelta(days=7)


# --- busy: packed calendar ----------------------------------------------------


def test_packed_day_moves_non_urgent_and_nudges_due_today_at_free_slot(db):
    owe(db, "send notes", MON)
    owe(db, "return book", MON + timedelta(days=1))
    c, inbox = cal(*packed(MON)), Inbox()  # Tuesday is free

    assert tick(db, c, S, at(MON, "10:00"), free, inbox) == "waiting for 13:00"
    assert remind_on(db, "return book") == MON + timedelta(days=1)
    assert dict(deferrals(db)) == {"calendar": 1}
    assert tick(db, c, S, at(MON, "13:00"), free, inbox).startswith("sent nudge")
    assert inbox.sent == [(MON, "nudge", ["send notes"])]


def test_next_free_day_skips_packed_days():
    c = cal(*packed(MON + timedelta(days=1)), *packed(MON + timedelta(days=2)))
    assert next_free_day(c, MON, S) == MON + timedelta(days=3)


def test_packed_ratio():
    assert busy_ratio([], MON, S) == 0
    assert busy_ratio(packed(MON), MON, S) == pytest.approx(12 / 13)


# --- busy: Do Not Disturb / full screen ---------------------------------------


@pytest.mark.parametrize("why", ["dnd", "fullscreen"])
def test_busy_at_slot_skips_today_but_still_nudges_due_today(db, why):
    owe(db, "send notes", MON)
    owe(db, "return book", MON + timedelta(days=1))
    inbox = Inbox()

    assert (
        tick(db, None, S, at(MON, "10:00"), lambda: why, inbox) == f"busy ({why}), retry next tick"
    )
    assert inbox.sent == []
    assert remind_on(db, "return book") == MON + timedelta(days=1)  # moved to the next free day
    assert dict(deferrals(db)) == {why: 1}
    # free again later: one short nudge, only for what's due today
    assert tick(db, None, S, at(MON, "10:30"), free, inbox).startswith("sent nudge")
    assert inbox.sent == [(MON, "nudge", ["send notes"])]


def test_busy_with_nothing_urgent_stays_quiet_all_day(db):
    owe(db, "return book", MON + timedelta(days=1))
    inbox = Inbox()
    tick(db, None, S, at(MON, "10:00"), lambda: "dnd", inbox)
    for hhmm in ["10:30", "14:00", "21:30"]:
        assert tick(db, None, S, at(MON, hhmm), free, inbox) == "nothing to remind"
    assert inbox.sent == []
    tue = MON + timedelta(days=1)
    assert tick(db, None, S, at(tue, "10:00"), free, inbox).startswith("sent digest")
    assert inbox.sent == [(tue, "digest", ["return book"])]


def test_in_a_meeting_waits_without_skipping_the_day(db):
    owe(db, "return book", MON + timedelta(days=1))
    c, inbox = cal((at(MON, "10:20"), at(MON, "11:00"))), Inbox()
    assert tick(db, c, S, at(MON, "09:55"), free, inbox) == "waiting for 10:00"
    # the 30-minute tick lands inside the next class
    assert tick(db, c, S, at(MON, "10:25"), free, inbox) == "in a meeting, retry next tick"
    assert list(deferrals(db)) == []
    assert tick(db, c, S, at(MON, "11:05"), free, inbox).startswith("sent digest")


# --- schedule changes -----------------------------------------------------------


def test_new_event_over_the_slot_reschedules_the_reminder(db):
    owe(db, "send notes", MON)
    inbox = Inbox()
    assert tick(db, cal(), S, at(MON, "09:30"), free, inbox) == "waiting for 10:00"
    moved = cal((at(MON, "10:00"), at(MON, "12:00")))  # a lab gets added at 10
    assert tick(db, moved, S, at(MON, "09:45"), free, inbox) == "waiting for 12:00"
    row = db.execute("SELECT reschedules FROM days").fetchone()
    assert row[0] == 1
    assert tick(db, moved, S, at(MON, "10:15"), free, inbox) == "waiting for 12:00"
    assert tick(db, moved, S, at(MON, "12:00"), free, inbox).startswith("sent digest")
    assert stats(db)["reschedules"] == 1


def test_schedule_change_that_packs_the_day_defers_non_urgent(db):
    owe(db, "send notes", MON)
    owe(db, "return book", MON + timedelta(days=1))
    inbox = Inbox()
    tick(db, cal(), S, at(MON, "09:30"), free, inbox)
    assert tick(db, cal(*packed(MON)), S, at(MON, "09:45"), free, inbox) == "waiting for 13:00"
    assert dict(deferrals(db)) == {"calendar": 1}
    tick(db, cal(*packed(MON)), S, at(MON, "13:00"), free, inbox)
    assert inbox.sent == [(MON, "nudge", ["send notes"])]


# --- gentleness ---------------------------------------------------------------


def test_never_more_than_one_notification_a_day():
    conn = store.connect(":memory:")
    rng = random.Random(7)
    inbox = Inbox()
    for i in range(30):
        day = MON + timedelta(days=i)
        for j in range(rng.randint(0, 3)):
            due = day + timedelta(days=rng.randint(0, 5)) if rng.random() < 0.8 else None
            owe(conn, f"p{i}-{j}", due, today=day)
        spans = [
            (at(day, f"{h:02d}:00"), at(day, f"{h + 1:02d}:00"))
            for h in range(9, 21)
            if rng.random() < 0.5
        ]
        c = cal(*spans)
        t = at(day, "00:00")
        while t.date() == day:
            tick(conn, c, S, t, lambda: "dnd" if rng.random() < 0.15 else None, inbox)
            t += timedelta(minutes=30)
    per_day = Counter(d for d, _, _ in inbox.sent)
    assert per_day and max(per_day.values()) == 1
    assert len(per_day) >= 15  # and it still does its job on most days


def test_done_and_snoozed_promises_are_left_alone(db):
    owe(db, "send notes", MON)
    owe(db, "return book", MON)
    owe(db, "call mom", MON)
    store.set_done(db, 1)
    store.snooze(db, 2, MON + timedelta(days=3))
    inbox = Inbox()
    tick(db, None, S, at(MON, "10:00"), free, inbox)
    assert inbox.sent == [(MON, "digest", ["call mom"])]


def test_quiet_hours(db):
    owe(db, "send notes", MON)
    inbox = Inbox()
    assert tick(db, None, S, at(MON, "07:00"), free, inbox) == "quiet hours"
    assert tick(db, None, S, at(MON, "22:30"), free, inbox) == "quiet hours"


def test_next_remind():
    assert next_remind("2026-10-15", MON) == date(2026, 10, 15)  # before due: on the day
    assert next_remind("2026-10-12", MON) == MON + timedelta(days=7)  # due/overdue: weekly
    assert next_remind(None, MON) == MON + timedelta(days=7)


# --- calendar parsing -----------------------------------------------------------


def test_weekly_classes_expand_and_all_day_events_are_ignored():
    c = cal(
        (at(MON, "09:00"), at(MON, "17:00")),
        rrule={"freq": "weekly"},
        allday=MON + timedelta(days=7),
    )
    next_mon = MON + timedelta(days=7)
    assert events_on(c, next_mon) == [(at(next_mon, "09:00"), at(next_mon, "17:00"))]
    assert events_on(c, MON + timedelta(days=1)) == []


def test_free_slot_needs_a_real_gap():
    events = [(at(MON, "10:00"), at(MON, "11:00")), (at(MON, "11:10"), at(MON, "12:00"))]
    assert free_slot(events, MON, S, at(MON, "09:00")) == at(MON, "12:00")  # 10 min gap too short
