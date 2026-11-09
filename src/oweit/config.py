import os
import tomllib
from pathlib import Path

DEFAULTS = {
    "me": [],  # your name(s) as they appear in WhatsApp exports
    "emails": [],  # your address(es), to pick your own mails out of an mbox
    "calendar": "",  # .ics path or your Google Calendar "secret address in iCal format"
    "day_start": "09:00",
    "day_end": "22:00",
    "digest_after": "10:00",
    "packed_ratio": 0.7,  # day counts as packed when this share of day_start..day_end is booked
    "min_slot_minutes": 20,
    "db": "",
}


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or Path.home() / ".config"
    return Path(base) / "oweit"


def load(path: str | Path | None = None) -> dict:
    cfg = dict(DEFAULTS)
    p = Path(path) if path else app_dir() / "config.toml"
    if p.exists():
        cfg.update(tomllib.loads(p.read_text(encoding="utf-8")))
    cfg["db"] = cfg["db"] or str(app_dir() / "oweit.db")
    return cfg
