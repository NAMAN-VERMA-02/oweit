# oweit

[![CI](https://github.com/NAMAN-VERMA-02/oweit/actions/workflows/ci.yml/badge.svg)](https://github.com/NAMAN-VERMA-02/oweit/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

You said *"I'll send it by Sunday"*. oweit remembers so you don't have to.

oweit reads your WhatsApp chat exports and Gmail mbox, finds the promises **you** made
("I'll send the notes by Sunday", "kal bhej dunga"), and reminds you gently: **one notification
a day at most**, and never while you're in class, on Do Not Disturb, or in a full-screen game.

## Why

Promises made in chat slip, because nobody turns "will do!" into a to-do. Reminder apps fix
that by nagging. oweit does the opposite: it finds the promises for you, and makes the
reminder as quiet as it can be while still arriving in time.

## How it works

```
WhatsApp .txt / Gmail .mbox
  │  parse: Android + iOS export formats, quoted email replies stripped
  ▼
regex prefilter ── only YOUR messages that sound like a commitment (English + Hinglish)
  │                everything else never reaches the LLM
  ▼
Claude (claude-opus-5-5, structured JSON output)
  │  who it's owed to, what, and the deadline as a phrase ("Sunday", "kal" → "tomorrow")
  ▼
dateparser ── phrase → date, relative to when you sent the message (Claude never does date math)
  ▼
SQLite on your machine ── done / snooze
  ▼
daily planner ── one digest or one nudge, in a free slot of your calendar
```

## Not annoying, by design

| Situation | What oweit does |
|---|---|
| Normal day | One silent digest at the first free calendar gap after 10:00 |
| Packed day (≥ 70% of 09:00–22:00 booked) | Only promises due **today**, as one short nudge in a free gap. The rest move to the next free day, never past their deadline |
| Do Not Disturb / Focus on, or a full-screen app, at reminder time | Today's digest is skipped. The due-today nudge waits until you're free again |
| In a class or meeting | Waits for the next check |
| A new event lands on the day's slot | That day is re-planned around it |
| Any day | Never more than one notification. Enforced in the database, not by good intentions |

A promise comes up the day before it's due, on the day, then weekly until you mark it done or
snooze it. Toasts are silent.

## Install

Needs Python 3.12+ and an [Anthropic API key](https://console.anthropic.com/) in
`ANTHROPIC_API_KEY`.

```bash
uv tool install git+https://github.com/NAMAN-VERMA-02/oweit
```

## Quickstart

1. **Export a chat.** WhatsApp → open the chat → ⋮ → More → Export chat → Without media.
2. **Export your mail (optional).** [Google Takeout](https://takeout.google.com/) → Mail → the
   *Sent* label is enough.
3. **Tell oweit who you are and where your calendar lives.** Copy
   [examples/config.toml](examples/config.toml) to `%APPDATA%\oweit\config.toml`
   (`~/.config/oweit/config.toml` elsewhere). For Google Calendar use *Settings → your calendar →
   Secret address in iCal format*.
4. **Scan.**
   ```bash
   oweit scan "WhatsApp Chat with Riya.txt" Sent.mbox
   ```
   Rescanning the same export is free: messages already seen never go to Claude again.
5. **Look, tick off, snooze.**
   ```bash
   oweit list
   oweit done 3
   oweit snooze 4 2
   ```
6. **Turn on the daily reminder.**
   ```bash
   oweit tick --dry-run
   oweit schedule install
   ```
   `tick --dry-run` shows today's plan and what is busy right now, and changes nothing.
   `schedule install` registers a Windows Task Scheduler job that runs every 30 minutes while
   you're logged in, through the windowless `oweitw`, so no console window flashes. On macOS
   and Linux it prints a cron line instead.

`oweit stats` shows the live numbers: reminders per promise, promises moved because you were
busy, and Claude calls made.

## Numbers

All numbers come from real runs; the raw outputs are committed in
[eval/results/](eval/results/).

### Prefilter (real run, held-out test half of the eval set)

202 hand-written, hand-labelled messages ([eval/](eval/)); the regex was tuned on the `dev`
half only. Source: [eval/results/2026-11-09-prefilter.json](eval/results/2026-11-09-prefilter.json).

| Split | Messages | Sent to Claude | LLM calls avoided | Promises that got through |
|---|---|---|---|---|
| test | 101 | 61 | 39.6% | 87.8% |
| all | 202 | 127 | 37.1% | 93.8% |

Every message in this set is the user's own and nearly half contain a promise, so on a real
chat export, where most lines are other people's, far more calls are avoided.

### Extraction with Claude

Precision, recall and date accuracy need the first full run with an API key
(`uv run python eval/run_eval.py`). They will land here with the raw output in
[eval/results/](eval/results/).

### Reminders (simulation)

The real scheduler, replayed on a fake clock from 1 Oct to 8 Nov 2026 (39 days) against a
hand-written college timetable (packed Thursdays, a packed exam week, a hackathon weekend, a
travel day), 4 events added on the morning of, and 8 Do Not Disturb / full-screen windows.
The 97 promises are the eval set's hand labels, each kept on its due date.
Source: [eval/results/2026-11-09-simulation.json](eval/results/2026-11-09-simulation.json).

| | |
|---|---|
| Most notifications in one day | **1** |
| Notifications in 39 days | 30 (13 digests, 17 due-today nudges) |
| Reminders per promise | 0.9 on average, never more than 2 |
| Promises moved because you were busy | 24 (18 packed calendar, 4 Do Not Disturb, 2 full screen) |
| Days re-planned after a calendar change | 4 |
| Promises due on a later day that got no reminder by their due date | **0** |
| Promises made and due the same day that got no reminder | 18 |

The last row is on purpose: "sending in 5" or "tonight" said after the day's one reminder has
gone out doesn't get a second notification.

### Live use

A 7-day run on my own laptop and calendar is planned for v0.1.1.

## Privacy

- Your exports and the database stay on your machine.
- Only messages that pass the prefilter are sent to the Anthropic API, each with up to 3
  earlier lines of the chat (or the quoted part of an email) for context.
- Other people's messages are never sent on their own, only as context lines.

## Limits

- Chat exports are a snapshot: re-export and re-scan to pick up new promises.
- Busy detection (Do Not Disturb, full screen) is Windows-only. Elsewhere oweit relies on the
  calendar alone. The Do Not Disturb check reads an undocumented Windows state that Microsoft
  could change.
- The eval set is hand-written by the author, so it reflects one person's chat style.

## Development

```bash
uv sync
uv run pytest
uv run python eval/run_eval.py --no-llm   # prefilter numbers, no API key needed
uv run python eval/run_eval.py            # full eval, about 200 Claude calls
uv run python eval/simulate.py            # replays Oct 2026 through the scheduler
```

Questions go to [Discussions](https://github.com/NAMAN-VERMA-02/oweit/discussions/categories/q-a).

## License

[MIT](LICENSE)
