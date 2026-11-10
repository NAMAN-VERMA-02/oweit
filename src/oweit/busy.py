"""Is the user heads-down right now? Windows Do Not Disturb / Focus, or a full-screen app.

Other OSes report "not busy"; the calendar check in schedule.py still applies there.
"""

import ctypes
import sys

# SHQueryUserNotificationState results that mean "don't interrupt"
QUNS_BUSY = 2  # full-screen app (video, game, F11 browser)
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4
# WNF_SHEL_QUIETHOURS_ACTIVE_PROFILE_CHANGED: 0 = off, 1 = priority only, 2 = alarms only
WNF_DND = 0x0D83063EA3BF1C75


def dnd_on() -> bool:
    if sys.platform != "win32":
        return False
    # shortcut: undocumented WNF state (same one the Action Center reads), re-check on new builds
    name, stamp = ctypes.c_uint64(WNF_DND), ctypes.c_ulong()
    value, size = ctypes.c_uint32(), ctypes.c_ulong(4)
    status = ctypes.windll.ntdll.NtQueryWnfStateData(
        ctypes.byref(name), None, None, ctypes.byref(stamp), ctypes.byref(value), ctypes.byref(size)
    )
    return status == 0 and value.value != 0


def fullscreen() -> bool:
    if sys.platform != "win32":
        return False
    state = ctypes.c_int()
    if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
        return False
    return state.value in (QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE)


def reason() -> str | None:
    """'dnd', 'fullscreen' or None. Never raises: a broken probe must not block reminders."""
    try:
        return "dnd" if dnd_on() else "fullscreen" if fullscreen() else None
    except (OSError, AttributeError):
        return None
