import ctypes
import sys
from types import SimpleNamespace

import pytest

from oweit import busy


class FakeWindll:
    """Fills the out-parameters the way ntdll/shell32 would."""

    def __init__(self, dnd=0, quns=5, fail=False):
        self.dnd, self.quns, self.fail = dnd, quns, fail
        self.ntdll = SimpleNamespace(NtQueryWnfStateData=self._wnf)
        self.shell32 = SimpleNamespace(SHQueryUserNotificationState=self._quns)

    def _wnf(self, name, _type, _scope, stamp, value, size):
        if self.fail:
            raise OSError("access denied")
        value._obj.value = self.dnd
        return 0

    def _quns(self, state):
        state._obj.value = self.quns
        return 0


@pytest.mark.parametrize(
    "fake, expected",
    [
        (FakeWindll(dnd=0, quns=5), None),  # accepts notifications
        (FakeWindll(dnd=1, quns=5), "dnd"),  # priority only
        (FakeWindll(dnd=2, quns=5), "dnd"),  # alarms only
        (FakeWindll(dnd=0, quns=2), "fullscreen"),  # full-screen app
        (FakeWindll(dnd=0, quns=3), "fullscreen"),  # full-screen game
        (FakeWindll(dnd=0, quns=4), "fullscreen"),  # presentation mode
        (FakeWindll(fail=True), None),  # a broken probe never blocks reminders
    ],
)
def test_windows_signals(monkeypatch, fake, expected):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", fake, raising=False)
    assert busy.reason() == expected


def test_other_platforms_are_never_busy(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert busy.reason() is None


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows APIs")
def test_real_windows_probe_runs():
    assert isinstance(busy.dnd_on(), bool)
    assert isinstance(busy.fullscreen(), bool)
