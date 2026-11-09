# oweit eval

`messages.jsonl` holds 202 messages written and labelled by hand by the author, in the style of
a 3rd-year engineering student's WhatsApp chats and email: 153 WhatsApp, 49 email, 49 in
Hinglish. 94 messages contain 97 promises. The rest are plain chat plus hard negatives that
*look* like promises ("I'll be honest…", "fail ho jaunga pakka", "Kabir will send them").

Rows alternate between `dev` and `test`. The prefilter regex was tuned on `dev` only, so the
`test` numbers are the honest ones.

## What counts as a promise

The same rules are in the extraction prompt ([src/oweit/extract.py](../src/oweit/extract.py)).

- **Yes**: something I told someone I will do: send, pay, bring, attend, check and get back.
  Casual wording counts ("on it", "will do", "yep, done 👍", "kal bhej dunga"). Accepting a
  meeting or slot counts ("4 works", "confirmed"). "I'll try to send it by Friday" counts.
- **No**: things already done, jokes and figures of speech, plans only about myself, hedges
  ("maybe", "if I get time", "no promises"), questions and offers, other people's promises.
- **to**: the person it's owed to. In a group chat, whoever asked; the group name is also
  accepted. Titles (Prof., Dr., Mr.) are ignored when matching.
- **due**: the calendar date the deadline falls on, read relative to when the message was
  sent. "tonight", "after dinner" and "in 5 min" mean the same day; "this weekend" means
  Saturday; a weekday means the next one after the message; no deadline means `null`.

## Metrics ([run_eval.py](run_eval.py))

- **Precision / recall / F1** per promise. A predicted promise matches a gold one in the same
  message. When a message holds two, they are paired in due-date order (3 messages do).
- **Recipient accuracy** and **date accuracy** (exact day; no date vs no date counts as
  correct) on the matched pairs.
- **LLM calls avoided**: share of messages the prefilter never sends to Claude.
  **Filter recall**: share of gold promises whose message passes the prefilter.
- Two arms: `pipeline` (prefilter, then Claude) and `no_filter` (Claude on every message).
  The gap between them is what the saved calls cost.

All messages here are the user's own, and a third contain promises, so "calls avoided" is far
lower than on a real chat export, where most messages are other people's.

## Simulation ([simulate.py](simulate.py))

Replays 1 Oct – 8 Nov 2026 through the real scheduler on a fake clock, with a hand-written
college timetable ([sim/timetable.ics](sim/timetable.ics)), four events added on the morning
they happen ([sim/changes.ics](sim/changes.ics)) and a hand-written Do Not Disturb /
full-screen trace ([sim/busy_trace.csv](sim/busy_trace.csv)). Promises enter when their message
was sent and are marked done on the evening they're due (3 days later if undated).
