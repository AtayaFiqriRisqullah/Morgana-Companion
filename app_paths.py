# -*- coding: utf-8 -*-
"""Resolve writable user-data directory vs. bundled read-only assets.

When running from source:
  * `app_dir()` and `bundle_dir()` both point to the project directory.

When packaged with PyInstaller (`sys.frozen` is True):
  * `app_dir()` points to the folder beside the `.exe` (writable: stores
    `tasks.json`, `notes.json`, `settings.json`, `token.json`, `pet.log`, and
    lets users place `credentials.json` or a custom `morgana_list.md` there).
  * `bundle_dir()` points to `sys._MEIPASS` (read-only bundled `Morgana/`,
    `fonts/`, and default `morgana_list.md`).
"""
from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundle_dir() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_dir())).resolve()
    return Path(__file__).resolve().parent


def resource_path(rel: str | Path) -> Path:
    """Return `app_dir() / rel` if the user placed an override there, else `bundle_dir() / rel`."""
    override = app_dir() / rel
    if override.exists():
        return override
    return bundle_dir() / rel
