"""Deliver the day's single reminder: a silent Windows toast, or one .ics event."""

import os
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

import icalendar

MAX_LINES = 5

_TOAST_PS = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($env:OWEIT_TOAST)
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show(
  [Windows.UI.Notifications.ToastNotification]::new($xml))
"""  # noqa: E501


def _when(due: str | None, day: date) -> str:
    if not due:
        return ""
    d = date.fromisoformat(due)
    if d == day:
        return " (today)"
    if d < day:
        return f" (was due {d:%a %d %b})"
    return f" (due {d:%a})" if (d - day).days < 7 else f" (due {d:%d %b})"


def compose(day: date, rows, mode: str) -> tuple[str, str]:
    n = len(rows)
    if mode == "nudge":
        title = "Due today: " + rows[0]["what"] if n == 1 else f"{n} things due today"
    else:
        title = f"{n} thing{'s' if n > 1 else ''} you said you'd do"
    lines = [f"{r['to_whom']}: {r['what']}{_when(r['due'], day)}" for r in rows[:MAX_LINES]]
    if n > MAX_LINES:
        lines.append(f"+{n - MAX_LINES} more  (oweit list)")
    return title, "\n".join(lines)


def toast(title: str, body: str) -> None:
    xml = (
        '<toast><visual><binding template="ToastGeneric">'
        f"<text>{escape(title)}</text><text>{escape(body)}</text>"
        '</binding></visual><audio silent="true"/></toast>'  # gentle: no sound
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS],
        env={**os.environ, "OWEIT_TOAST": xml},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=60,
        check=True,
    )


def write_ics(path: Path, when: datetime, title: str, body: str) -> None:
    cal = icalendar.Calendar()
    cal.add("prodid", "-//oweit//EN")
    cal.add("version", "2.0")
    ev = icalendar.Event()
    ev.add("uid", f"oweit-{when:%Y%m%d}@oweit")
    ev.add("dtstamp", datetime.now().astimezone())
    ev.add("dtstart", when)
    ev.add("dtend", when + timedelta(minutes=15))
    ev.add("summary", f"oweit: {title}")
    ev.add("description", body)
    ev.add("transp", "TRANSPARENT")  # shows up, never blocks time
    cal.add_component(ev)
    path.write_bytes(cal.to_ical())


def notifier(ics: Path | None = None, now=datetime.now):
    """Build the callback tick() uses: a toast, or a one-event .ics when `ics` is set."""

    def send(day: date, rows, mode: str) -> None:
        title, body = compose(day, rows, mode)
        print(f"{title}\n{body}")
        if ics:
            write_ics(ics, now(), title, body)
        else:
            toast(title, body)

    return send
