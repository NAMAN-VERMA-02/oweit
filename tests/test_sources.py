import mailbox
from datetime import datetime
from email.message import EmailMessage

from oweit.sources import read, split_reply

ANDROID = """\
12/10/26, 9:01 pm - Messages and calls are end-to-end encrypted. Tap to learn more.
12/10/26, 9:02 pm - Riya: hey can you send the dbms notes?
12/10/26, 9:05 pm - Naman: yes
will send by sunday
12/10/26, 9:06 pm - Riya: <Media omitted>
13/10/26, 12:15 am - Riya: thanks!!
"""

IOS = """\
[10/12/26, 9:02:11 PM] Riya: hey can you send the dbms notes?
[10/12/26, 9:05:40 PM] ‎Naman: kal bhej dunga
[10/13/26, 12:15:00 AM] Riya: thanks!!
"""


def test_android_export(tmp_path):
    f = tmp_path / "WhatsApp Chat with Riya.txt"
    f.write_text(ANDROID, encoding="utf-8")
    msgs = read(f, me=["Naman"], emails=[])
    assert [m.sender for m in msgs] == ["Riya", "Naman", "Riya"]  # system line + media skipped
    mine = msgs[1]
    assert mine.from_me and mine.text == "yes\nwill send by sunday"
    assert mine.ts == datetime(2026, 10, 12, 21, 5)  # day-first, pm
    assert mine.to == "Riya"
    assert mine.context == ["Riya: hey can you send the dbms notes?"]
    assert msgs[2].ts == datetime(2026, 10, 13, 0, 15)  # 12 am is midnight


def test_ios_export_month_first_and_me_autodetect(tmp_path):
    f = tmp_path / "WhatsApp Chat with Riya.txt"
    f.write_text(IOS, encoding="utf-8")
    msgs = read(f, me=[], emails=[])  # no name configured: 1:1 chat, other sender is me
    assert msgs[1].from_me and not msgs[0].from_me
    assert msgs[1].sender == "Naman"
    assert msgs[1].ts == datetime(2026, 10, 12, 21, 5)  # 13 in second field => month-first


def test_split_reply_keeps_quote_as_context():
    body = (
        "Sure, I'll send the report by Friday.\n\n-- \nNaman\n\n"
        "On Mon, 12 Oct 2026 at 10:00, Prof Sharma <ps@kiet.edu> wrote:\n"
        "> Please send the lab report.\n> Thanks\n"
    )
    mine, quoted = split_reply(body)
    assert mine == "Sure, I'll send the report by Friday."
    assert quoted == ["Please send the lab report.", "Thanks"]


def test_mbox_only_my_mails_are_decoded(tmp_path):
    box = mailbox.mbox(tmp_path / "mail.mbox")
    for frm, to, body in [
        ("Naman <me@example.com>", "Prof Sharma <ps@kiet.edu>", "Will submit it tomorrow."),
        ("Prof Sharma <ps@kiet.edu>", "me@example.com", "Reminder: submit the report."),
    ]:
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = frm, to, "Lab report"
        m["Date"] = "Mon, 12 Oct 2026 10:00:00 +0530"
        m.set_content(body)
        box.add(m)
    box.flush()
    msgs = read(tmp_path / "mail.mbox", me=[], emails=["ME@example.com"])
    assert [m.from_me for m in msgs] == [True, False]
    assert msgs[0].text == "Will submit it tomorrow." and msgs[0].to == "Prof Sharma"
    assert msgs[1].text == ""  # not mine: body never decoded
