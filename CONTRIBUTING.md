# Contributing & Feature Development Guidelines

This document outlines the architecture, UI/UX, packaging, and Git rules for developing new features in **Morgana Companion**.

---

## 1. Architecture & Performance

- **Keep core logic free of PyQt6:** Business logic, JSON persistence, parsers, and external APIs must remain Qt-free (see `storage_manager.py`, `emotions.py`, `lines.py`, `calendar_service.py`) so they stay lightweight and testable.
- **Never block the UI thread:**
  - Network calls, OAuth flows, or heavy I/O must run on a background thread and report back via Qt Signals (follow the `CalendarFetcher` pattern in `main.py`).
  - Never use `time.sleep()` for UI animations; use `Spring` and `FrameTicker` from `motion.py` so timers stop automatically when idle.
- **Zero-config, crash-safe local storage:**
  - Users must never have to create `.json` files manually—initialize missing files automatically with safe defaults.
  - Use `write_json_atomic()` and range validation (see `SETTINGS_DEFAULTS` in `storage_manager.py`) to prevent corrupted state.
- **Respect PyInstaller paths (`app_paths.py`):**
  - Writable runtime data (`.json`, logs, tokens) → always use `app_dir()`.
  - Read-only bundled assets (`Morgana/`, `fonts/`) → always use `bundle_dir()` or `resource_path()`.
  - Register any newly added asset folders in `datas` inside `MorganaCompanion.spec`.

---

## 2. UI/UX & Persona 5 Theme

- **Cohesive dark Persona 5 palette (`theme.py`):**
  - Stick to obsidian (`#0E0E12`), crimson (`#E60012`), crisp white (`#FFFFFF`), and Morgana gold (`#F5C400`).
  - Use the bundled SIL OFL fonts in `fonts/` (`Anton`, `Barlow Condensed`, `Arsenal`) so typography stays consistent across Windows and Linux.
- **Hover text color:** Text color **may** change on hover **only with explicit approval from the project owner**; by default, keep hover feedback limited to background, border, or scale changes.
- **Live `ui_scale` support (75%–150%):**
  - Every new dialog or widget must accept `ui_scale`, scale typography/spacing via `_sc()`, `dialog_qss()`, or `menu_qss()`, and rebuild cleanly inside `PetController.set_ui_scale()`.
- **Visual & Windows Qt6 rules:**
  - Keep `Morgana/textbox.png` uniformly scaled (no vertical stretching; rely on 3-line pagination).
  - Do not add controller icons to `RadialMenu` (keep the spinning hover star).
  - For frameless translucent dialogs on Windows, avoid `setWindowOpacity(0.0)` before `show()` and ensure clean teardown/recreation on reopen (see `SettingsDialog` in `ui_dialogs.py`).
- **Language & tone:** All user-facing UI labels, menus, dialogs, and Morgana lines must be in **English**, written in a supportive everyday-companion tone (avoid out-of-context Persona 5 game-specific character/location references).

---

## 3. Git, Security & Cross-Platform Releases

- **Commit format:** Use **Conventional Commits** in English, imperative mood, summary ≤ 72 characters, grouped into logical incremental commits (e.g., `feat(ui): ...`, `fix(dialogs): ...`, `ci(release): ...`).
- **Never commit secrets or runtime files:** Keep `credentials.json`, `token.json`, `settings.json`, `tasks.json`, `notes.json`, `pet.log`, `__pycache__/`, `build/`, `dist/`, `*.zip`, and `*.tar.gz` ignored in `.gitignore`.
- **Windows & Linux compatibility:**
  - Guard OS-specific code with `sys.platform` (e.g., `autostart.py`, `MorganaCompanion.spec`) so both `build-windows` and `build-linux` jobs in `.github/workflows/release.yml` pass cleanly.
- **Pre-push verification:** Run `python3 -m py_compile *.py` and verify UI rendering (including `85%` and `130%` UI scales) before pushing to `main`.
