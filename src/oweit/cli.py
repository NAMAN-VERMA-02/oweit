import argparse
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from oweit import __version__, config, prefilter, sources, store


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oweit", description="Find promises you made; get reminded gently."
    )
    p.add_argument("--version", action="version", version=f"oweit {__version__}")
    p.add_argument("--config", help="config.toml path (default: %%APPDATA%%/oweit/config.toml)")
    p.add_argument("--db", help="SQLite path (default: next to config.toml)")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("scan", help="find promises in WhatsApp .txt exports or Gmail .mbox files")
    s.add_argument("files", nargs="+")
    s.add_argument("--me", action="append", help="your WhatsApp name (repeatable)")
    s.add_argument("--email", action="append", help="your email address (repeatable)")

    s = sub.add_parser("list", help="show open promises")
    s.add_argument("--all", action="store_true", help="include done ones")

    s = sub.add_parser("done", help="mark promises as kept")
    s.add_argument("ids", nargs="+", type=int)

    s = sub.add_parser("snooze", help="hide a promise for N days")
    s.add_argument("id", type=int)
    s.add_argument("days", type=int)

    s = sub.add_parser("tick", help="send today's reminder if it's time and you're free")
    s.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    s.add_argument("--ics", help="write the reminder to this .ics file instead of a toast")

    sub.add_parser("stats", help="reminders per promise, moves because busy, LLM calls")

    s = sub.add_parser("schedule", help="run `oweit tick` every N minutes (Task Scheduler)")
    s.add_argument("action", choices=["install", "remove"])
    s.add_argument("--every", type=int, default=30, help="minutes (default 30)")
    return p


def cmd_scan(args, cfg, conn) -> None:
    import anthropic

    from oweit import extract

    client = None
    today = date.today()
    for path in args.files:
        msgs = sources.read(path, args.me or cfg["me"], args.email or cfg["emails"])
        mine = [m for m in msgs if m.from_me and m.text]
        fresh = [m for m in mine if not store.is_seen(conn, m.key)]
        cands = [m for m in fresh if prefilter.is_candidate(m.text)]
        found = 0
        for m in cands:
            client = client or anthropic.Anthropic()
            for i, p in enumerate(extract.extract(m, client).promises):
                if store.add_promise(conn, f"{m.key}:{i}", m.source, m.ts, p, today):
                    found += 1
                    due = f"  (due {p.due:%a %d %b})" if p.due else ""
                    print(f"  + {p.to}: {p.what}{due}")
            store.mark_seen(conn, [m.key])
            conn.commit()  # per message, so a crash halfway never pays for the same call twice
        store.mark_seen(conn, [m.key for m in fresh])
        store.log_scan(
            conn,
            msgs[0].source if msgs else "?",
            len(msgs),
            len(mine),
            len(cands),
            len(cands),
            found,
            datetime.now(),
        )
        conn.commit()
        skipped = 1 - len(cands) / len(msgs) if msgs else 0
        print(
            f"{path}: {len(msgs)} messages, {len(mine)} new from you, {len(cands)} sent to "
            f"Claude ({skipped:.0%} needed no call), {found} new promises"
        )


def cmd_list(args, conn) -> None:
    rows = store.promises(conn, include_done=args.all)
    if not rows:
        print("Nothing owed. Nice.")
    for r in rows:
        due = date.fromisoformat(r["due"]).strftime("%a %d %b") if r["due"] else "no date"
        done = "  [done]" if r["status"] == "done" else ""
        print(f"{r['id']:>4}  {due:<10}  {r['to_whom']}: {r['what']}{done}")


def cmd_tick(args, cfg, conn) -> None:
    from oweit import busy, notify, schedule

    s = schedule.Settings.from_config(cfg)
    cal = schedule.load_calendar(cfg["calendar"], config.app_dir() / "calendar-cache.ics")
    now = datetime.now()
    if args.dry_run:
        events = schedule.events_on(cal, now.date())
        ratio = schedule.busy_ratio(events, now.date(), s)
        print(
            f"{len(events)} events today, {ratio:.0%} of {s.day_start:%H:%M}-{s.day_end:%H:%M}"
            f" booked ({'packed' if ratio >= s.packed_ratio else 'not packed'})"
        )
        print(f"busy right now: {busy.reason() or 'no'}")
        print(f"{len(schedule.pool(conn, now.date()))} promise(s) due for a reminder today")
    ics = Path(args.ics) if args.ics else None
    if ics is None and sys.platform != "win32":
        ics = config.app_dir() / "digest.ics"
    send = notify.notifier(ics)
    if args.dry_run:

        def send(day, rows, mode):  # noqa: F811 - preview instead of notifying
            title, body = notify.compose(day, rows, mode)
            print(f"would send: {title} | {body.replace(chr(10), ' | ')}")

    result = schedule.tick(conn, cal, s, now, busy.reason, send)
    label = "tick (dry run, nothing saved)" if args.dry_run else "tick"
    print(f"{now:%Y-%m-%d %H:%M} {label}: {result}")
    if args.dry_run:
        conn.rollback()


def cmd_schedule(args) -> None:
    if sys.platform != "win32":
        print(f"Add this line with `crontab -e`:\n*/{args.every} * * * * oweit tick")
        return
    if args.action == "remove":
        subprocess.run(["schtasks", "/Delete", "/TN", "oweit", "/F"], check=True)
        return
    exe = shutil.which("oweitw")  # windowless twin of `oweit`, so no console flashes
    pyw = Path(sys.executable).with_name("pythonw.exe")
    cmd = f'"{exe}" tick' if exe else f'"{pyw}" -m oweit tick'
    task = ["/TN", "oweit", "/SC", "MINUTE", "/MO", str(args.every), "/TR", cmd, "/F"]
    subprocess.run(["schtasks", "/Create", *task], check=True)
    print(
        f"oweit will check every {args.every} min while you're logged in. "
        f"Log: {config.app_dir() / 'oweit.log'}"
    )


def main(argv: list[str] | None = None) -> int:
    if sys.stdout is None:  # started by Task Scheduler through windowless oweitw: log to a file
        config.app_dir().mkdir(parents=True, exist_ok=True)
        # shortcut: the log is never rotated (~2 KB/day); rotate if anyone runs this for years
        sys.stdout = sys.stderr = open(config.app_dir() / "oweit.log", "a", encoding="utf-8")
    sys.stdout.reconfigure(errors="replace")  # Hinglish/emoji on a cp1252 console
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 0
    cfg = config.load(args.config)
    conn = store.connect(args.db or cfg["db"])
    try:
        if args.cmd == "scan":
            cmd_scan(args, cfg, conn)
        elif args.cmd == "list":
            cmd_list(args, conn)
        elif args.cmd == "done":
            for pid in args.ids:
                print(f"{pid}: {'kept' if store.set_done(conn, pid) else 'no such promise'}")
        elif args.cmd == "snooze":
            until = date.today() + timedelta(days=args.days)
            ok = store.snooze(conn, args.id, until)
            print(f"{args.id}: snoozed until {until:%a %d %b}" if ok else "no such promise")
        elif args.cmd == "tick":
            cmd_tick(args, cfg, conn)
        elif args.cmd == "stats":
            from oweit import schedule

            for k, v in schedule.stats(conn).items():
                print(f"{k:<28} {v}")
        elif args.cmd == "schedule":
            cmd_schedule(args)
        conn.commit()
    except Exception as e:  # API/auth/network errors: say what happened, no traceback wall
        import anthropic  # imported lazily: `tick` runs every 30 min and never needs it

        if not isinstance(e, anthropic.AnthropicError):
            raise
        print(f"oweit: Claude API error: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0
