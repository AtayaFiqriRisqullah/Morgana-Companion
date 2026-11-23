# -*- coding: utf-8 -*-
"""Auto-start saat login/boot, lintas OS. Tanpa dependensi Qt.

  * Windows : nilai di HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run,
              menjalankan skrip lewat pythonw.exe (tanpa jendela console).
  * Linux   : file .desktop di $XDG_CONFIG_HOME/autostart (default ~/.config/autostart),
              dibaca oleh KDE Plasma, GNOME, XFCE, dan desktop XDG lainnya.

API: is_enabled() -> bool ; set_enabled(on, script) -> (ok, pesan)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ID = "DesktopCodingPet"
DESKTOP_FILE = "desktop-coding-pet.desktop"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")


# -- pembentuk perintah (murni, mudah diuji) --------------------------------------
def windows_python(exe: str | None = None) -> str:
    """Pilih pythonw.exe di folder interpreter yang sama (fallback: interpreter saat ini)."""
    exe_path = Path(exe or sys.executable)
    if exe_path.name.lower() == "pythonw.exe":
        return str(exe_path)
    candidate = exe_path.with_name("pythonw.exe")
    return str(candidate) if candidate.is_file() else str(exe_path)


def build_windows_command(python_exe: str, script: Path) -> str:
    return f'"{python_exe}" "{script}"'


def _quote_exec_arg(arg: str) -> str:
    """Quoting argumen untuk baris Exec= di file .desktop (spesifikasi freedesktop)."""
    if not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`%'):
        return arg
    esc = (arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`")
              .replace("$", "\\$").replace("%", "%%"))
    esc = esc.replace("\\", "\\\\")     # lapisan escape string pada file .desktop
    return f'"{esc}"'


def build_desktop_entry(python_exe: str, script: Path) -> str:
    exec_line = f"{_quote_exec_arg(python_exe)} {_quote_exec_arg(str(script))}"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=Desktop Coding Pet\n"
        "Comment=Persona 5 style desktop companion\n"
        f"Exec={exec_line}\n"
        f"Path={script.parent}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _linux_entry_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / DESKTOP_FILE


# -- API publik --------------------------------------------------------------------
def is_enabled() -> bool:
    try:
        if IS_WINDOWS:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, APP_ID)
            return True
        if IS_LINUX:
            return _linux_entry_path().is_file()
    except OSError:
        pass
    return False


def set_enabled(on: bool, script: Path) -> tuple[bool, str]:
    """Aktifkan/nonaktifkan auto-start untuk `script` (path main.py)."""
    script = Path(script).resolve()
    try:
        if IS_WINDOWS:
            return _set_windows(on, script)
        if IS_LINUX:
            return _set_linux(on, script)
    except OSError as exc:
        return False, str(exc)
    return False, "Auto-start is not supported on this OS yet"


def _set_windows(on: bool, script: Path) -> tuple[bool, str]:
    import winreg
    if on:
        cmd = build_windows_command(windows_python(), script)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, cmd)
        return True, cmd
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_ID)
    except FileNotFoundError:
        pass                                           # memang belum terpasang
    return True, "removed"


def _set_linux(on: bool, script: Path) -> tuple[bool, str]:
    path = _linux_entry_path()
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_desktop_entry(sys.executable, script), encoding="utf-8")
        return True, str(path)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return True, "removed"