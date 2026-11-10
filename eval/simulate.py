"""Replay 1 Oct - 8 Nov 2026 through oweit's real scheduler on a fake clock.

    uv run python eval/simulate.py                    # promises from the latest eval run
    uv run python eval/simulate.py --promises gold    # the hand labels instead

Inputs, all hand-written, in eval/sim/: a weekly college timetable (packed Thursdays, a packed
exam week, a hackathon weekend, a travel day), four events added to the calendar on the morning
they happen, and a trace of Do Not Disturb / full-screen windows.
Assumptions: a promise enters oweit when its message was sent, and gets kept (marked done) on the
evening of its due date, or 3 days after it was made if it had no date. tick() runs every 30 min.
"""

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path

import icalendar

from oweit import schedule, store
from oweit.extract import Promise

HERE = Path(__file__).parent
SIM = HERE / "sim"
START, END = date(2026, 10, 1), date(2026, 11, 8)
STEP = timedelta(minutes=30)


def load_promises(source: str) -> list[tuple]:
    rows = {r["id"]: r for r in map(json.loads, (HERE / "messages.jsonl").open(encoding="utf-8"))}
    if source == "gold":
        items = [(r, g["to"][0], g["what"], g["due"]) for r in rows.values() for g in r["gold"]]
    else:
        path = (
            Path(source)
            if source != "latest"
            else max(
                (
                    q
                    for q in (HERE / "results").glob("*.json")
                    if not q.stem.endswith(("-prefilter", "-simulation"))
                ),
                key=lambda q: q.stat().st_mtime,
            )
        )
        report = json.loads(path.read_text(encoding="utf-8"))
        items = [
            (rows[i], p["to"], p["what"], p["due"])
            for i, v in report["predictions"].items()
            if v["passed_filter"]
            for p in v["pred"]
        ]
    out = [
        (
            datetime.fromisoformat(r["ts"]),
            f"{r['id']}:{n}",
            to,
            what,
            date.fromisoformat(due) if due else None,
        )
        for n, (r, to, what, due) in enumerate(items)
    ]
    return sorted(out)


def calendars():
    base = (SIM / "timetable.ics").read_bytes()
    changes = [
        (datetime.strptime(str(ev["X-OWEIT-ADDED"]), "%Y%m%dT%H%M%S"), ev)
        for ev in icalendar.Calendar.from_ical((SIM / "changes.ics").read_bytes()).walk("VEVENT")
    ]
    cache: dict[int, icalendar.Calendar] = {}

    def at(t: datetime) -> icalendar.Calendar:
        added = [ev for when, ev in changes if when <= t]
        if len(added) not in cache:
            cal = icalendar.Calendar.from_ical(base)
            for ev in added:
                cal.add_component(ev)
            cache[len(added)] = cal
        return cache[len(added)]

    return at


def busy_trace():
    with (SIM / "busy_trace.csv").open() as f:
        spans = [
            (datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"]), r["reason"])
            for r in csv.DictReader(f)
        ]
    return lambda t: next((why for s, e, why in spans if s <= t < e), None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--promises", default="latest", help="'latest', 'gold' or a results .json")
    args = ap.parse_args()

    conn = store.connect(":memory:")
    pending = load_promises(args.promises)
    cal_at, busy_at = calendars(), busy_trace()
    s = schedule.Settings()
    sent: list[tuple[date, str, int]] = []

    def notify(day, rows, mode):
        sent.append((day, mode, len(rows)))

    now = datetime.combine(START, time.min)
    while now.date() <= END:
        while pending and pending[0][0] <= now:
            ts, key, to, what, due = pending.pop(0)
            store.add_promise(conn, key, "sim", ts, Promise(to, what, due, None, what), ts.date())
        t = now  # bind for the probe
        schedule.tick(conn, cal_at(now), s, now, lambda t=t: busy_at(t), notify)
        now += STEP
        if now.time() == time.min:  # end of a day: the user keeps what was due
            day = (now - STEP).date()
            conn.execute(
                "UPDATE promises SET status='done' WHERE status='open' AND "
                "(due=? OR (due IS NULL AND created<=?))",
                (day.isoformat(), (day - timedelta(days=3)).isoformat()),
            )

    per_promise = Counter({r[0]: 0 for r in conn.execute("SELECT id FROM promises")}) + Counter(
        r[0] for r in conn.execute("SELECT promise_id FROM log WHERE kind='reminded'")
    )
    counts = [per_promise.get(r[0], 0) for r in conn.execute("SELECT id FROM promises")]
    missed = conn.execute(
        "SELECT due=created, COUNT(*) FROM promises p WHERE due IS NOT NULL AND due>=created AND "
        "NOT EXISTS (SELECT 1 FROM log l WHERE l.promise_id=p.id AND l.kind='reminded' "
        "AND l.day<=p.due) GROUP BY due=created"
    ).fetchall()
    missed = {"made_and_due_same_day": 0, "due_a_later_day": 0} | {
        ("made_and_due_same_day" if same else "due_a_later_day"): n for same, n in missed
    }
    per_day = Counter(d for d, _, _ in sent)
    st = schedule.stats(conn)
    report = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "kind": "simulation (fake clock, hand-written calendar and busy trace)",
        "promises_from": args.promises,
        "days": (END - START).days + 1,
        "promises": len(counts),
        "notifications": len(sent),
        "notifications_by_mode": dict(Counter(m for _, m, _ in sent)),
        "max_notifications_in_a_day": max(per_day.values(), default=0),
        "days_with_a_notification": len(per_day),
        "reminders_per_promise_mean": round(sum(counts) / len(counts), 2) if counts else 0,
        "reminders_per_promise_max": max(counts, default=0),
        "reminders_per_promise_histogram": dict(sorted(Counter(counts).items())),
        "deferrals_by_reason": st["deferrals"],
        "promises_moved_because_busy": st["promises_moved"],
        "reschedules": st["reschedules"],
        "dated_promises_never_reminded_by_due_date": missed,
    }
    out = HERE / "results" / f"{date.today()}-simulation.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    for k, v in report.items():
        print(f"{k:<44} {v}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
