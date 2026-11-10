"""An LLM (Claude, or Gemini as a fallback) pulls out who/what/when; dateparser turns 'when'
into a date."""

import json
import os
import re
import time
import urllib.error
import urllib.request
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


GEMINI_MODEL = os.environ.get("OWEIT_GEMINI_MODEL", "gemini-2.5-flash")
# Gemini's responseSchema is OpenAPI-style: nullable instead of anyOf, no additionalProperties
_GEMINI_SCHEMA = {
    "type": "object",
    "properties": {
        "promises": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "what": {"type": "string"},
                    "due_phrase": {"type": "string", "nullable": True},
                    "quote": {"type": "string"},
                },
                "required": ["to", "what", "due_phrase", "quote"],
            },
        }
    },
    "required": ["promises"],
}


class GeminiClient:
    """Minimal Gemini REST client (stdlib only). The key goes in a header, never the URL."""

    def __init__(self, key: str, model: str = GEMINI_MODEL):
        self.key, self.model = key, model

    def generate(self, user: str) -> tuple[dict | None, int, int]:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        )
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _GEMINI_SCHEMA,
                "temperature": 0,
            },
        }
        req = urllib.request.Request(
            url,
            json.dumps(body).encode(),
            {"Content-Type": "application/json", "x-goog-api-key": self.key},
        )
        for attempt in range(8):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 503) or attempt == 7:
                    raise RuntimeError(f"Gemini API error {e.code}: {e.read()[:300]!r}") from None
                time.sleep(min(60, 2**attempt * 2))  # free tier rate limits: back off and retry
        u = data.get("usageMetadata", {})
        tin = u.get("promptTokenCount", 0)
        tout = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
        cands = data.get("candidates") or []
        if not cands or cands[0].get("finishReason") in ("SAFETY", "PROHIBITED_CONTENT"):
            return None, tin, tout  # blocked = refusal
        text = "".join(p.get("text", "") for p in cands[0]["content"]["parts"])
        return json.loads(text), tin, tout


def make_client():
    """Gemini only when GEMINI_API_KEY is set and ANTHROPIC_API_KEY is not; Claude otherwise."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key and not os.environ.get("ANTHROPIC_API_KEY"):
        return GeminiClient(key)
    return anthropic.Anthropic()


def model_name(client) -> str:
    return client.model if isinstance(client, GeminiClient) else MODEL


def _promises(items: list[dict], msg: Message) -> list[Promise]:
    return [
        Promise(
            i["to"],
            i["what"],
            resolve_date(i.get("due_phrase"), msg.ts),
            i.get("due_phrase"),
            i["quote"],
        )
        for i in items
    ]


def extract(msg: Message, client) -> Result:
    if isinstance(client, GeminiClient):
        data, tin, tout = client.generate(render(msg))
        if data is None:
            return Result([], tin, tout, refused=True)
        return Result(_promises(data["promises"], msg), tin, tout)
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
    return Result(_promises(json.loads(text)["promises"], msg), *usage)
