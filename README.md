# Morgana Companion

Desktop companion bot themed after Morgana from Persona 5. Built with PyQt6.

## Features
- Floating pet overlay with `assets/Morgana/textbox.png` (tilted nameplate + 3-slice dialogue box placed cleanly below Morgana without overlapping UI elements) and a configurable 6-wedge Persona 5 battle-menu style radial menu (3 left: `EVENTS`, `TIMER`, `SYSTEM`; 3 right: `TASKS`, `NOTES`, `STATS` — enable or disable individual tabs in **Settings**) with radiating red-black spikes behind Morgana and a spinning star on hover
- Context-aware Morgana sprites & dialogue: 10 expressions, blinking eyes, lip-sync typing, and dynamic lines that react to opening/closing the radial menu and dialog windows, task completions, timers, schedules, wall bounces, and time of day — paired naturally with 8 Japanese voicelines (`Dousuru?`, `Fumu...`, `Oi!`, `Naa naa`, `Oh?`, `Oooh!`, `Uwooooh!`, `Yare yare...`)
- **Persona 5 Audio Engine (`assets/audio/`)**: radial menu & in-window UI hover/select/cancel SFX, window open/close cues, 3-stage P5R Social Stats point-gain & level-up fanfare synced with pentagon animations, and 8 Morgana voicelines — with live volume control & quick mute in Settings and the right-click menu
- **Weekly Events window (`EVENTS`)**: dedicated 7-day schedule combining Google Calendar events and active **Note deadlines** (with `NOTE` & `OVERDUE` badges and live sync from Notes), grouped by day with an interactive day strip and a dedicated Morgana briefing banner announcing your next upcoming event or deadline (works with a 7-day sample calendar when not configured)
- **Social Stats (`STATS`)**: Persona 5 Royal-inspired 5-stat pentagon tracker (`Knowledge`, `Guts`, `Proficiency`, `Charm`, `Kindness`) for daily 2-hour time blocks (`Daytime` / `After School` / `Evening`), customizable point logging (`+1`/`+2`/`+3`/`+5` or custom), 20-point level intervals (`0–20` Lv 1, `21–40` Lv 2, …), animated pentagon & rank-up celebrations, and 5-second undo
- **Focus Mode & Manual Timers (`TIMER`)**: redesigned Persona 5 Focus Mode scoreboard (Pomodoro focus/break durations, live progress, session count) + multiple concurrent manual countdown timers with quick presets and Morgana alerts, backed by a slanted Persona 5 HUD tag under Morgana
- Cohesive Persona 5 dark-themed Notes (pin, categories, reminders, debounced autosave, undo), Tasks (priority, search, filters, undo), and Settings (bedtime, reminders, focus timer, audio volume, radial menu tab toggles, UI size, behavior) dialogs
- Live UI scaling (`75%–150%`) via right-click **UI SIZE** presets (`85%` / `100%` / `115%` / `130%`) or **Settings → UI size**, scaling dialogue box, radial menu, menus, timer HUD, and all dialog windows without restarting
- **Google Sheets Sync / WhatsApp Relay (opt-in, Settings)**: write-only cloud sync that pushes active task deadlines to a dedicated Google Sheets tab whenever tasks change (no VPN, LAN IP, or incoming ports required), allowing an external WhatsApp bot to periodically pull deadlines and send staged reminders even when your PC is off
- Bedtime reminders (default every 30 seconds during bedtime window), system monitor (CPU/RAM), and customizable Morgana quotes (edit `morgana_list.md`)
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
End users do **not** need any `.json` files to run the app (`tasks.json`, `notes.json`, `social_stats.json`, and `settings.json` are created automatically beside the executable, and `EVENTS` works with a 7-day sample schedule).
1. Follow the guide at the top of `calendar_service.py`.
2. Save your OAuth client as `credentials.json` beside `main.py` or the executable (see `credentials.example.json`).
3. Click **EVENTS** in the pet menu and sign in. `token.json` is created automatically.

`credentials.json`, `token.json`, `sheets_key.json`, `kunci-google.json` and runtime data are git-ignored.

## Structure
| File | Purpose |
|------|---------|
| `main.py` | pet window & P5 HUD, bubble, 6-wedge radial menu, timers, controller |
| `ui_dialogs.py` | tasks, notes, events, social stats, timer, and settings dialogs |
| `audio_manager.py` | cross-platform QtMultimedia SFX, Social Stats fanfare & Morgana voicelines |
| `theme.py` / `motion.py` | Persona 5 style (colors, fonts, spikes) and spring animation |
| `storage_manager.py` | atomic JSON storage (`tasks`, `notes`, `social_stats`, `settings`) |
| `calendar_service.py` | 7-day Google Calendar fetch, day grouping & Morgana briefing |
| `sheets_sync.py` | opt-in write-only Google Sheets task deadline relay for WhatsApp reminders |
| `lines.py` / `morgana_list.md` | dialogue |
| `emotions.py` | picks expression and voiceline from dialogue text |
| `sprites.py` | composes base sprite + eye/mouth patches at their pixel offsets |
| `autostart.py` | start on boot |
| `app_paths.py` | resolves writable app folder vs. PyInstaller bundle (`_MEIPASS`) |
| `assets/Morgana/` | sprite images, `morgana.ico`, and `textbox.png` (`_e1`/`_e2` eyes, `_m1`-`_m3` mouth) |
| `assets/fonts/` | bundled Persona 5-style SIL OFL fonts (`Anton`, `Barlow Condensed`, `Arsenal`) |
| `assets/audio/` | UI sound effects, Social Stats WAV fanfares, and Morgana voicelines |
