"""Read WhatsApp .txt exports and Gmail mbox files into Message records."""

import email.utils
import hashlib
import mailbox
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.header import decode_header, make_header
from pathlib import Path

CONTEXT_LINES = 3


@dataclass
class Message:
    source: str  # "whatsapp" | "gmail"
    chat: str  # chat name, or email subject
    ts: datetime  # local time, naive
    sender: str
    text: str
    from_me: bool
    to: str = ""  # who the message is addressed to, best guess
    context: list[str] = field(default_factory=list)  # earlier lines as "Sender: text"

    @property
    def key(self) -> str:
        raw = f"{self.source}|{self.chat}|{self.ts.isoformat()}|{self.sender}|{self.text}"
        return hashlib.sha1(raw.encode()).hexdigest()


def read(path: str | Path, me: list[str], emails: list[str]) -> list[Message]:
    path = Path(path)
    if path.suffix.lower() == ".mbox":
        return read_mbox(path, emails)
    return read_whatsapp(path, me)


# --- WhatsApp -------------------------------------------------------------

# Android: "12/10/26, 9:05 pm - Riya: text"   iOS: "[12/10/26, 9:05:12 PM] Riya: text"
_WA_LINE = re.compile(
    r"^\[?(?P<a>\d{1,2})[/.-](?P<b>\d{1,2})[/.-](?P<y>\d{2,4}),?\s+"
    r"(?P<h>\d{1,2}):(?P<mi>\d{2})(?::\d{2})?\s*(?P<ap>[ap]\.?\s?m\.?)?\]?\s*(?:-\s*)?(?P<rest>.*)$",
    re.I,
)
_SKIP = {
    "<media omitted>",
    "image omitted",
    "video omitted",
    "sticker omitted",
    "gif omitted",
    "audio omitted",
    "document omitted",
    "this message was deleted",
    "you deleted this message",
    "null",
    "<this message was edited>",
}


def _clean(line: str) -> str:
    return line.replace(" ", " ").replace("\xa0", " ").replace("‎", "").replace("‏", "")


def read_whatsapp(path: Path, me: list[str]) -> list[Message]:
    chat = path.stem.removeprefix("WhatsApp Chat with ").removeprefix("WhatsApp Chat - ")
    rows: list[list] = []  # [a, b, y, h, mi, ampm, sender, text]
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = _clean(raw)
        m = _WA_LINE.match(line)
        if m:
            sender, sep, text = m["rest"].partition(": ")
            if not sep:  # system line ("Messages and calls are end-to-end encrypted")
                rows.append(None)
                continue
            rows.append(
                [
                    int(m["a"]),
                    int(m["b"]),
                    int(m["y"]),
                    int(m["h"]),
                    int(m["mi"]),
                    (m["ap"] or "").lower().replace(".", "").replace(" ", ""),
                    sender.strip(),
                    text,
                ]
            )
        elif rows and rows[-1] is not None:
            rows[-1][7] += "\n" + line  # continuation of a multi-line message
    rows = [r for r in rows if r]
    # dd/mm vs mm/dd: whichever field ever exceeds 12 is the day; default day-first (India)
    month_first = any(r[1] > 12 for r in rows) and not any(r[0] > 12 for r in rows)

    me_l = {n.lower() for n in me} | {"you"}
    senders = {r[6] for r in rows}
    if not any(s.lower() in me_l for s in senders) and len(senders) == 2 and chat in senders:
        me_l |= {
            s.lower() for s in senders - {chat}
        }  # 1:1 chat: the sender who isn't the chat name

    out: list[Message] = []
    for a, b, y, h, mi, ap, sender, text in rows:
        text = text.strip()
        if text.lower() in _SKIP:
            continue
        day, month = (b, a) if month_first else (a, b)
        if ap == "pm" and h != 12:
            h += 12
        elif ap == "am" and h == 12:
            h = 0
        ts = datetime(y + 2000 if y < 100 else y, month, day, h, mi)
        ctx = [f"{p.sender}: {p.text}" for p in out[-CONTEXT_LINES:]]
        out.append(Message("whatsapp", chat, ts, sender, text, sender.lower() in me_l, chat, ctx))
    return out


# --- Gmail mbox -----------------------------------------------------------

_QUOTE_START = re.compile(r"^(On .{5,200}wrote:|-+ ?Original Message ?-+|From: .+)$", re.I)


def _header(msg, name: str) -> str:
    return str(make_header(decode_header(msg.get(name, "") or "")))


def _body(msg) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/plain" and not part.get_filename():
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def split_reply(body: str) -> tuple[str, list[str]]:
    """Return (my new text, quoted lines) from an email body."""
    mine: list[str] = []
    quoted: list[str] = []
    state = "mine"  # mine -> (signature) -> quote
    for line in body.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if _QUOTE_START.match(s) or line.startswith(">"):
            state = "quote"
        elif state == "mine" and s == "--":
            state = "sig"
        if state == "mine":
            mine.append(line)
        elif state == "quote":
            q = line.lstrip("> ").strip()
            if q and not _QUOTE_START.match(q):
                quoted.append(q)
    return "\n".join(mine).strip(), quoted


def read_mbox(path: Path, emails: list[str]) -> list[Message]:
    mine_addrs = {e.lower() for e in emails}
    out: list[Message] = []
    for msg in mailbox.mbox(path):
        _, addr = email.utils.parseaddr(_header(msg, "From"))
        try:
            ts = email.utils.parsedate_to_datetime(msg["Date"]).astimezone().replace(tzinfo=None)
        except (TypeError, ValueError):
            continue
        subject = _header(msg, "Subject")
        from_me = addr.lower() in mine_addrs
        if not from_me:  # only my mails can hold my promises; skip decoding the rest
            out.append(Message("gmail", subject, ts, addr, "", False))
            continue
        to_name, to_addr = email.utils.parseaddr(_header(msg, "To"))
        text, quoted = split_reply(_body(msg))
        out.append(
            Message(
                "gmail",
                subject,
                ts,
                addr,
                text,
                True,
                to_name or to_addr,
                quoted[: CONTEXT_LINES * 2],
            )
        )
    return out
