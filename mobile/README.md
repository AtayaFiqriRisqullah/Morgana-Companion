# Morgana Companion — Android (`mobile/`)

Persona 5 Royal themed Android companion app with full feature parity to Morgana Companion Desktop v1.5.3, built with **Flutter** + native **Kotlin** Android Floating Bubble Overlay.

## Features
- **Interactive Morgana Stage & Joker Assist Menu**: Animated blinking/talking Morgana sprite, stepped crimson Joker spikes, adaptive Persona 5 radial ribbons, and quick command spotlight bar.
- **Finance Ledger (`bot-wa` / `Monthly_Budget` Sync)**: Local mode OR live Google Sheets v4 sync directly from Android via pure-Dart RS256 Service Account JWT (`kunci-google.json` import via Android Storage Access Framework). Includes semantic Expense/Income/Transfer colors, 1-tap `+5rb`–`+100rb` accumulator, live wallet balance chips, budgets, shortcuts, and recurring bills.
- **Tasks, Notes, 7-Day Events, Social Stats & Daily Habits**: Auto Social Stat rewards on task/habit completion, Golden Star Pentagon chart, Markdown preview & `#tag` extraction, and unified 7-day timeline.
- **Focus Pomodoro & Manual Timers**: High-priority Android notifications when Focus sprints, breaks, or manual timers finish.
- **Ask Morgana**: Offline Quick Command engine + opt-in Gemini / OpenAI-compatible AI chat.
- **Optional Floating Morgana Bubble**: Native Kotlin `SYSTEM_ALERT_WINDOW` + Android 14+ `specialUse` Foreground Service with draggable edge-snapping avatar and quick dock (`FINANCE`, `TASKS`, `ASK`).

## Development & Build
```bash
cd mobile
flutter pub get
flutter analyze
flutter test
flutter build apk --release
```

Release APK output: `mobile/build/app/outputs/flutter-apk/app-release.apk`.
