"""Claude pulls out who/what/when; dateparser turns 'when' into a date."""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import anthropic
import dateparser

from oweit.sources import Message

MODEL = "claude-opus-5-5"

SYSTEM = """You find commitments that I (the user) made in one of my chat messages or emails.

A commitment is something I told someone I will do: send, share, pay, call, bring, finish, \
attend, check and get back, and so on. Casual wording counts ("on it", "will do", "sure, by \
friday", "kal bhej dunga", "haan kar dunga"). "I'll try to send it by Friday" counts. Messages \
can be English, Hinglish (romanized Hindi) or a mix.

Not a commitment:
- something already done ("sent it", "bhej diya")
- figures of speech and jokes ("I'll be honest", "I'll die", "I'll never understand DP")
- plans only about myself that nobody is waiting on ("I'll sleep early today")
- non-committal answers ("maybe", "might", "if I get time", "dekhta hu shayad")
- questions and offers I haven't committed to ("should I send it?", "want me to call?")
- things other people promised or asked for

The earlier messages are context only. Only extract commitments from the message marked as \
mine.

For each commitment return:
- to: the person it is owed to (a name from the chat or email; in a group, whoever asked, else \
the group name)
- what: a short action, max 8 words, e.g. "send the DBMS notes"
- due_phrase: the deadline as a plain English date expression that a date parser can read, e.g. \
"tomorrow", "tonight", "Sunday", "next Friday", "10 November", "in 2 days", "tomorrow 5pm". \
Translate Hinglish ("kal" in a promise = "tomorrow", "parso" = "in 2 days", "agle hafte" = \
"next week"). Do not work out calendar dates yourself. null if there is no deadline.
- quote: my exact words that make the commitment

Return an empty list when there is no commitment."""

SCHEMA = {
    "type": "object",
    "properties": {
        "promises": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "what": {"type": "string"},
                    "due_phrase": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "quote": {"type": "string"},
                },
                "required": ["to", "what", "due_phrase", "quote"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["promises"],
    "additionalProperties": False,
}


@dataclass
class Promise:
    to: str
    what: str
    due: date | None
    due_phrase: str | None
    quote: str


@dataclass
class Result:
    promises: list[Promise]
    input_tokens: int = 0
    output_tokens: int = 0
    refused: bool = False


def render(msg: Message) -> str:
    where = (
        f'WhatsApp chat "{msg.chat}"'
        if msg.source == "whatsapp"
        else f'Email to {msg.to}, subject "{msg.chat}"'
    )
    lines = [f"Source: {where}", f"Sent: {msg.ts:%A %Y-%m-%d %H:%M}", ""]
    if msg.context:
        lines += ["Earlier messages:", *msg.context, ""]
    lines += ["My message:", msg.text]
    return "\n".join(lines)


# phrases dateparser can't read on its own
_ALIASES = {
    "tonight": "today",
    "eod": "today",
    "end of day": "today",
    "end of the day": "today",
    "today evening": "today",
    "this evening": "today",
    "tomorrow morning": "tomorrow",
    "tomorrow evening": "tomorrow",
    "tomorrow night": "tomorrow",
    "this weekend": "saturday",
    "weekend": "saturday",
    "the weekend": "saturday",
    "next week": "in 7 days",
}


def resolve_date(phrase: str | None, base: datetime) -> date | None:
    if not phrase:
        return None
    p = re.sub(r"^(by|before|on|until|till|latest by)\s+", "", phrase.strip().lower())
    p = _ALIASES.get(p, p)
    if re.fullmatch(r"(the )?end of (the |this )?month", p):
        nxt = (base.replace(day=28) + timedelta(days=4)).replace(day=1)
        return (nxt - timedelta(days=1)).date()
    d = dateparser.parse(
        p, languages=["en"], settings={"RELATIVE_BASE": base, "PREFER_DATES_FROM": "future"}
    )
    return d.date() if d else None


def extract(msg: Message, client: anthropic.Anthropic) -> Result:
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # a refused request reruns on Anthropic's default fallback model
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": render(msg)}],
    )
    usage = (resp.usage.input_tokens, resp.usage.output_tokens)
    if resp.stop_reason == "refusal":
        return Result([], *usage, refused=True)
    text = next(b.text for b in resp.content if b.type == "text")
    items = json.loads(text)["promises"]
    promises = [
        Promise(
            i["to"], i["what"], resolve_date(i["due_phrase"], msg.ts), i["due_phrase"], i["quote"]
        )
        for i in items
    ]
    return Result(promises, *usage)
