import argparse
import sys
from datetime import date, datetime, timedelta

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


def main(argv: list[str] | None = None) -> int:
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
