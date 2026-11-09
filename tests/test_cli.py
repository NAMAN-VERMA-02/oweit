import pytest

from oweit.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert "oweit 0.1.0" in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage: oweit" in capsys.readouterr().out


def test_schedule_install_registers_a_windowless_task(monkeypatch, tmp_path, capsys):
    import shutil
    import subprocess
    import sys

    calls = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(shutil, "which", lambda name: r"C:\tools\oweitw.exe")
    monkeypatch.setattr(subprocess, "run", lambda cmd, check: calls.append(cmd))
    args = ["--db", str(tmp_path / "o.db"), "--config", str(tmp_path / "none.toml")]
    assert main([*args, "schedule", "install", "--every", "15"]) == 0
    assert calls == [
        [
            "schtasks",
            "/Create",
            "/TN",
            "oweit",
            "/SC",
            "MINUTE",
            "/MO",
            "15",
            "/TR",
            r'"C:\tools\oweitw.exe" tick',
            "/F",
        ]
    ]
    assert main([*args, "schedule", "remove"]) == 0
    assert calls[1] == ["schtasks", "/Delete", "/TN", "oweit", "/F"]


def test_schedule_on_other_platforms_prints_a_cron_line(monkeypatch, tmp_path, capsys):
    import sys

    monkeypatch.setattr(sys, "platform", "linux")
    args = ["--db", str(tmp_path / "o.db"), "--config", str(tmp_path / "none.toml")]
    assert main([*args, "schedule", "install"]) == 0
    assert "*/30 * * * * oweit tick" in capsys.readouterr().out
