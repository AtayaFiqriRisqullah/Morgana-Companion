# Morgana Companion

Desktop companion bot themed after Morgana from Persona 5. Built with PyQt6.

## Features
- Floating pet overlay with speech bubble and radial menu
- Tasks, notes (with deadlines) and settings dialogs in Persona 5 style
- Bedtime reminders and Morgana quotes (edit `morgana_list.md`)
- Read-only Google Calendar agenda (sample events when not configured)
- Pomodoro timer and system monitor
- Auto-start on Windows and Linux

## Setup
```bash
pip install -r requirements.txt
python main.py        # see: python main.py --help
```

## Google Calendar (optional)
1. Follow the guide at the top of `calendar_service.py`.
2. Save your OAuth client as `credentials.json` (see `credentials.example.json`).
3. Click AGENDA in the pet menu and sign in. `token.json` is created automatically.

`credentials.json`, `token.json` and runtime data are git-ignored.

## Structure
| File | Purpose |
|------|---------|
| `main.py` | pet window, bubble, radial menu, controller |
| `ui_dialogs.py` | tasks, notes, settings dialogs |
| `theme.py` / `motion.py` | Persona 5 style and spring animation |
| `storage_manager.py` | JSON storage |
| `calendar_service.py` | Google Calendar |
| `lines.py` / `morgana_list.md` | dialogue |
| `autostart.py` | start on boot |
| `Morgana/` | sprite images |
