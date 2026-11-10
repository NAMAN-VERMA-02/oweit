import json
from datetime import date, datetime
from types import SimpleNamespace

from oweit.cli import main
from oweit.extract import extract, render, resolve_date
from oweit.sources import Message

WED = datetime(2026, 10, 14, 21, 5)  # a Wednesday


class FakeClient:
    """Stands in for anthropic.Anthropic(); returns canned structured output."""

    def __init__(self, payload=None, stop_reason="end_turn"):
        self.calls = []
        payload = payload if payload is not None else {"promises": []}
        resp = SimpleNamespace(
            stop_reason=stop_reason,
            usage=SimpleNamespace(input_tokens=500, output_tokens=60),
            content=[SimpleNamespace(type="text", text=json.dumps(payload))],
        )
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self._resp = resp

    def _create(self, **kw):
        self.calls.append(kw)
        return self._resp


def msg(text, ts=WED):
    return Message(
        "whatsapp", "Riya", ts, "Naman", text, True, "Riya", ["Riya: can you send the dbms notes?"]
    )


def test_dates_resolve_relative_to_the_message_not_today():
    assert resolve_date("Sunday", WED) == date(2026, 10, 18)
    assert resolve_date("by tomorrow 5pm", WED) == date(2026, 10, 15)
    assert resolve_date("tonight", WED) == date(2026, 10, 14)
    assert resolve_date("this weekend", WED) == date(2026, 10, 17)
    assert resolve_date("end of the month", WED) == date(2026, 10, 31)
    assert resolve_date(None, WED) is None
    assert resolve_date("someday", WED) is None


def test_extract_maps_output_and_sends_context():
    fake = FakeClient(
        {
            "promises": [
                {
                    "to": "Riya",
                    "what": "send the DBMS notes",
                    "due_phrase": "Sunday",
                    "quote": "will send by sunday",
                }
            ]
        }
    )
    res = extract(msg("will send by sunday"), fake)
    assert res.promises[0].due == date(2026, 10, 18)
    assert res.promises[0].to == "Riya"
    assert (res.input_tokens, res.output_tokens) == (500, 60)
    sent = fake.calls[0]
    assert sent["model"] == "claude-opus-5-5"
    assert "Wednesday 2026-10-14" in sent["messages"][0]["content"]
    assert "Riya: can you send the dbms notes?" in sent["messages"][0]["content"]


def test_refusal_returns_nothing():
    res = extract(msg("I'll send it"), FakeClient(stop_reason="refusal"))
    assert res.refused and res.promises == []


def test_render_email():
    m = Message(
        "gmail", "Lab report", WED, "me@x.com", "Will submit tomorrow.", True, "Prof Sharma", []
    )
    assert 'Email to Prof Sharma, subject "Lab report"' in render(m)


def test_scan_end_to_end_with_fake_claude(tmp_path, monkeypatch, capsys):
    chat = tmp_path / "WhatsApp Chat with Riya.txt"
    chat.write_text(
        "14/10/26, 9:02 pm - Riya: can you send the dbms notes?\n"
        "14/10/26, 9:05 pm - Naman: will send by sunday\n"
        "14/10/26, 9:06 pm - Naman: btw sent you the lab file\n",
        encoding="utf-8",
    )
    fake = FakeClient(
        {
            "promises": [
                {
                    "to": "Riya",
                    "what": "send the DBMS notes",
                    "due_phrase": "Sunday",
                    "quote": "will send by sunday",
                }
            ]
        }
    )
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", lambda: fake)
    db, cfg = str(tmp_path / "o.db"), str(tmp_path / "none.toml")

    def run(*a):
        return main(["--db", db, "--config", cfg, *a])

    assert run("scan", str(chat), "--me", "Naman") == 0
    assert len(fake.calls) == 1  # "sent you the lab file" never reached Claude
    assert run("scan", str(chat), "--me", "Naman") == 0
    assert len(fake.calls) == 1  # rescan: already seen, zero calls
    assert run("list") == 0
    out = capsys.readouterr().out
    assert "Sun 18 Oct  Riya: send the DBMS notes" in out


def test_gemini_backend_parses_json_and_resolves_dates(monkeypatch):
    import io
    import json as _json
    from datetime import datetime

    from oweit import extract as ex
    from oweit.sources import Message

    reply = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {
                            "text": _json.dumps(
                                {
                                    "promises": [
                                        {
                                            "to": "Riya",
                                            "what": "send notes",
                                            "due_phrase": "tomorrow",
                                            "quote": "kal bhej dunga",
                                        }
                                    ]
                                }
                            )
                        }
                    ]
                },
            }
        ],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20},
    }
    seen = {}

    def fake_urlopen(req, timeout):
        seen["key"] = req.get_header("X-goog-api-key")
        seen["url"] = req.full_url
        return io.BytesIO(_json.dumps(reply).encode())

    monkeypatch.setattr(ex.urllib.request, "urlopen", fake_urlopen)
    msg = Message("whatsapp", "Riya", datetime(2026, 10, 5, 20, 0), "Me", "kal bhej dunga", True)
    r = ex.extract(msg, ex.GeminiClient("k"))
    assert seen["key"] == "k" and "k" not in seen["url"]
    assert r.promises[0].to == "Riya" and str(r.promises[0].due) == "2026-10-06"
    assert (r.input_tokens, r.output_tokens, r.refused) == (100, 20, False)
