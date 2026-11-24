# -*- coding: utf-8 -*-
"""Zero-dependency cross-platform global hotkey manager (Windows & Linux X11).

Supports preset shortcuts such as ``Ctrl+Shift+M``, ``Ctrl+Alt+M``,
``Ctrl+Shift+Space``, ``Alt+Space``, or ``Off``.

Design:
  * **Windows**: Native ``RegisterHotKey`` / ``UnregisterHotKey`` via ``ctypes``
    + ``QAbstractNativeEventFilter`` handling ``WM_HOTKEY`` (0% CPU when idle).
  * **Linux (X11)**: Lightweight ``libX11`` ``XGrabKey`` listener thread with
    ``select`` wakeups and non-fatal X error guard (never aborts on ``BadAccess``).
  * **Wayland / Headless / Offscreen**: Degrades gracefully without crashing and
    reports honest status via ``status_text``.
"""
from __future__ import annotations

import ctypes
import logging
import os
import select
import sys
import threading

from PyQt6.QtCore import QAbstractNativeEventFilter, QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

log = logging.getLogger("pet.hotkeys")

# Normalized preset table: name -> (win_mods, win_vk, x11_mods, x11_keysym)
# Win32 modifiers: MOD_ALT=0x1, MOD_CONTROL=0x2, MOD_SHIFT=0x4, MOD_NOREPEAT=0x4000
# X11 modifiers: ShiftMask=0x1, ControlMask=0x4, Mod1Mask(Alt)=0x8
_PRESETS: dict[str, tuple[int, int, int, int]] = {
    "Ctrl+Shift+M":     (0x0002 | 0x0004, 0x4D, 0x4 | 0x1, 0x006D),
    "Ctrl+Alt+M":       (0x0002 | 0x0001, 0x4D, 0x4 | 0x8, 0x006D),
    "Ctrl+Shift+Space": (0x0002 | 0x0004, 0x20, 0x4 | 0x1, 0x0020),
    "Alt+Space":        (0x0001,          0x20, 0x8,       0x0020),
}
_WM_HOTKEY = 0x0312
_MOD_NOREPEAT = 0x4000

# Define Win32 MSG + POINTER type ONCE at module level.
# Creating ctypes.Structure + ctypes.POINTER inside nativeEventFilter() leaks
# every single Windows message forever into ctypes._pointer_type_cache (~700 MB/min)!
if sys.platform.startswith("win"):
    from ctypes import wintypes as _wintypes

    class _WIN_MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", _wintypes.HWND),
            ("message", _wintypes.UINT),
            ("wParam", _wintypes.WPARAM),
            ("lParam", _wintypes.LPARAM),
            ("time", _wintypes.DWORD),
            ("pt_x", _wintypes.LONG),
            ("pt_y", _wintypes.LONG),
        ]

    _LP_WIN_MSG = ctypes.POINTER(_WIN_MSG)
else:
    _WIN_MSG = None
    _LP_WIN_MSG = None

_X11_ERR_CB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


class _WinHotkeyFilter(QAbstractNativeEventFilter):
    """Zero-allocation Qt native event filter catching Win32 WM_HOTKEY messages."""

    def __init__(self, hotkey_id: int, on_trigger) -> None:
        super().__init__()
        self._hotkey_id = hotkey_id
        self._on_trigger = on_trigger

    def nativeEventFilter(self, event_type, message):
        if _LP_WIN_MSG is None:
            return False, 0
        try:
            if event_type == b"windows_generic_MSG" or event_type == b"windows_dispatcher_MSG":
                msg = ctypes.cast(int(message), _LP_WIN_MSG).contents
                if msg.message == _WM_HOTKEY and int(msg.wParam) == self._hotkey_id:
                    self._on_trigger()
                    return True, 0
        except Exception:
            pass
        return False, 0


class GlobalHotkeyManager(QObject):
    """Registers a single global OS shortcut for Morgana's Quick Command bar."""

    activated = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._preset = "Off"
        self._active = False
        self._status = "Disabled"

        # Windows state
        self._win_id = 0x4D4F   # 'MO'
        self._win_filter: _WinHotkeyFilter | None = None

        # Linux X11 state
        self._x_stop: threading.Event | None = None
        self._x_thread: threading.Thread | None = None

    @property
    def preset(self) -> str:
        return self._preset

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def status_text(self) -> str:
        return self._status

    def register(self, preset: str) -> bool:
        """Unregister any existing shortcut and attempt to register `preset`."""
        self.unregister()
        clean = (preset or "Off").strip()
        self._preset = clean
        if clean == "Off" or clean not in _PRESETS:
            self._status = "Disabled"
            return False

        spec = _PRESETS[clean]
        if sys.platform.startswith("win"):
            return self._register_win32(clean, spec[0], spec[1])
        if sys.platform.startswith("linux"):
            return self._register_linux_x11(clean, spec[2], spec[3])

        self._status = "Not supported on this OS (use Tray or Ctrl+K)"
        return False

    def unregister(self) -> None:
        self._active = False
        # Windows cleanup
        if self._win_filter is not None:
            app = QApplication.instance()
            if app is not None:
                try:
                    app.removeNativeEventFilter(self._win_filter)
                except Exception:
                    pass
            try:
                ctypes.windll.user32.UnregisterHotKey(None, self._win_id)
            except Exception:
                pass
            self._win_filter = None

        # Linux X11 cleanup
        if self._x_stop is not None:
            self._x_stop.set()
        if self._x_thread is not None and self._x_thread.is_alive():
            self._x_thread.join(timeout=0.6)
        self._x_stop = None
        self._x_thread = None

    # -- Windows implementation ------------------------------------------------
    def _register_win32(self, name: str, mods: int, vk: int) -> bool:
        try:
            ok = bool(ctypes.windll.user32.RegisterHotKey(None, self._win_id, mods | _MOD_NOREPEAT, vk))
            if not ok:
                # Retry without MOD_NOREPEAT just in case
                ok = bool(ctypes.windll.user32.RegisterHotKey(None, self._win_id, mods, vk))
            if not ok:
                self._status = f"Busy in another app — try a different preset ({name})"
                return False
            flt = _WinHotkeyFilter(self._win_id, self.activated.emit)
            app = QApplication.instance()
            if app is not None:
                app.installNativeEventFilter(flt)
            self._win_filter = flt
            self._active = True
            self._status = f"Active ({name})"
            return True
        except Exception as exc:
            log.debug("Win32 hotkey registration failed: %s", exc)
            self._status = "Unavailable (use Tray or Ctrl+K)"
            return False

    # -- Linux X11 implementation ----------------------------------------------
    def _register_linux_x11(self, name: str, mods: int, keysym: int) -> bool:
        if (not os.environ.get("DISPLAY")
                or os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen"):
            self._status = "Inactive in headless/Wayland (use Tray or Ctrl+K)"
            return False
        try:
            x11 = ctypes.CDLL("libX11.so.6")
        except OSError:
            self._status = "X11 unavailable (use Tray or Ctrl+K)"
            return False

        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        x11.XGrabKey.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_uint,
            ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ]
        x11.XUngrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong]
        x11.XConnectionNumber.argtypes = [ctypes.c_void_p]
        x11.XConnectionNumber.restype = ctypes.c_int
        x11.XPending.argtypes = [ctypes.c_void_p]
        x11.XPending.restype = ctypes.c_int
        x11.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]

        err_flag = {"bad": False}

        def _err_handler(_dpy, _ev):
            err_flag["bad"] = True
            return 0

        cb_ref = _X11_ERR_CB(_err_handler)
        x11.XSetErrorHandler.argtypes = [_X11_ERR_CB]
        x11.XSetErrorHandler.restype = ctypes.c_void_p

        dpy = x11.XOpenDisplay(None)
        if not dpy:
            self._status = "Display unavailable (use Tray or Ctrl+K)"
            return False

        root = x11.XDefaultRootWindow(dpy)
        keycode = int(x11.XKeysymToKeycode(dpy, keysym))
        if keycode == 0:
            x11.XCloseDisplay(dpy)
            self._status = f"Keycode unavailable ({name})"
            return False

        # Grab across CapsLock (0x2) & NumLock (0x10) lock masks
        lock_masks = (0, 0x2, 0x10, 0x12)
        prev_handler = x11.XSetErrorHandler(cb_ref)
        for lm in lock_masks:
            x11.XGrabKey(dpy, keycode, mods | lm, root, 0, 1, 1)
        x11.XSync(dpy, 0)
        x11.XSetErrorHandler(ctypes.cast(prev_handler, _X11_ERR_CB) if prev_handler else _X11_ERR_CB(0))

        if err_flag["bad"]:
            for lm in lock_masks:
                x11.XUngrabKey(dpy, keycode, mods | lm, root)
            x11.XSync(dpy, 0)
            x11.XCloseDisplay(dpy)
            self._status = f"Busy in another app — try another preset ({name})"
            return False

        stop_evt = threading.Event()
        self._x_stop = stop_evt
        self._active = True
        self._status = f"Active ({name})"

        def _loop() -> None:
            fd = x11.XConnectionNumber(dpy)
            ev_buf = (ctypes.c_char * 192)()
            try:
                while not stop_evt.is_set():
                    try:
                        ready, _, _ = select.select([fd], [], [], 0.25)
                    except (OSError, ValueError):
                        break
                    if not ready:
                        continue
                    while not stop_evt.is_set() and x11.XPending(dpy) > 0:
                        x11.XNextEvent(dpy, ev_buf)
                        ev_type = ctypes.c_int.from_buffer(ev_buf).value
                        if ev_type == 2:   # KeyPress
                            self.activated.emit()
            except Exception:
                pass
            finally:
                try:
                    for lm in lock_masks:
                        x11.XUngrabKey(dpy, keycode, mods | lm, root)
                    x11.XSync(dpy, 0)
                    x11.XCloseDisplay(dpy)
                except Exception:
                    pass

        t = threading.Thread(target=_loop, name="morgana-x11-hotkey", daemon=True)
        t.start()
        self._x_thread = t
        return True
