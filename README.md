# Morgana Companion

Desktop companion bot themed after Morgana from Persona 5. Built with PyQt6.

## Features
- Floating pet overlay with `Morgana/textbox.png` (tilted nameplate + 3-slice dialogue box) and a Persona 5 battle-menu style radial menu with radiating red-black spikes behind Morgana and a spinning star on hover
- Persona 5 Morgana sprites: 10 expressions, blinking eyes, and a mouth that moves while text is typed. The expression follows the dialogue (e.g. shouting for "Go to bed!", excited for "Ooh, we can do this!")
- Cohesive Persona 5 dark-themed Notes (pin, categories, reminders, debounced autosave, undo), Tasks (priority, search, filters, undo), and Settings (bedtime, reminders, focus timer, UI size, behavior) dialogs
- Live UI scaling (`75%–150%`) via right-click **UI SIZE** presets (`85%` / `100%` / `115%` / `130%`) or **Settings → UI size**, scaling dialogue box, radial menu, menus, Pomodoro badge, and dialog windows without restarting
- Bedtime reminders and Morgana quotes (edit `morgana_list.md`)
- Read-only Google Calendar agenda (sample events when not configured)
- Supportive focus timer (Pomodoro) and system monitor
- Auto-start on Windows and Linux

## Setup
```bash
pip install -r requirements.txt
python main.py        # see: python main.py --help
```

## Building executables (Windows & Linux)
- **Windows**: Run `build_exe.bat` (or `pyinstaller --noconfirm --clean MorganaCompanion.spec`) to produce `dist/Morgana Companion/Morgana Companion.exe` and `dist/Morgana-Companion-win64.zip`.
- **Linux**: Run `./build_linux.sh` (install Qt6/XCB runtime libs if missing) to produce `dist/Morgana Companion/Morgana Companion` and `dist/Morgana-Companion-linux-x64.tar.gz`.
- **Automated Releases**: Pushing a version tag (e.g. `git tag v1.0.0 && git push origin v1.0.0`) or running the **Build & Release (Windows & Linux)** workflow manually in GitHub Actions builds and publishes both Windows (`.zip`) and Linux (`.tar.gz`) archives to GitHub Releases automatically.

## Google Calendar (optional)
End users do **not** need any `.json` files to run the app (`tasks.json`, `notes.json`, and `settings.json` are created automatically beside the executable, and `AGENDA` works with sample events).
1. Follow the guide at the top of `calendar_service.py`.
2. Save your OAuth client as `credentials.json` beside `main.py` or the executable (see `credentials.example.json`).
3. Click AGENDA in the pet menu and sign in. `token.json` is created automatically.

`credentials.json`, `token.json` and runtime data are git-ignored.

## Structure
| File | Purpose |
|------|---------|
| `main.py` | pet window, bubble, radial menu, controller |
| `ui_dialogs.py` | tasks, notes, settings dialogs |
| `theme.py` / `motion.py` | Persona 5 style (colors, fonts, spikes) and spring animation |
| `storage_manager.py` | JSON storage |
| `calendar_service.py` | Google Calendar |
| `lines.py` / `morgana_list.md` | dialogue |
| `emotions.py` | picks an expression from the dialogue text |
| `sprites.py` | composes base sprite + eye/mouth patches at their pixel offsets |
| `autostart.py` | start on boot |
| `fonts/` | bundled Persona 5-style SIL OFL fonts (`Anton`, `Barlow Condensed`, `Arsenal`) |
| `app_paths.py` | resolves writable app folder vs. PyInstaller bundle (`_MEIPASS`) |
| `Morgana/` | sprite images and `textbox.png` (`_e1` closed eyes, `_e2` half-closed, `_m1`-`_m3` mouth) |
