import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

import 'audio_service.dart';
import 'command_engine.dart';
import 'emotions.dart';
import 'gemini_ai.dart';
import 'native_bridge.dart';
import 'rupiah.dart';
import 'sheets_sync.dart';
import 'storage.dart';

enum P5ToastTone { ok, gold, warn, err, info }

class P5ToastMessage {
  final int id;
  final String text;
  final P5ToastTone tone;
  final int durationMs;

  const P5ToastMessage({
    required this.id,
    required this.text,
    required this.tone,
    this.durationMs = 2400,
  });
}

class ManualTimerItem {
  final String id;
  final String label;
  final int totalSeconds;
  int remainingSeconds;

  ManualTimerItem({
    required this.id,
    required this.label,
    required this.totalSeconds,
    required this.remainingSeconds,
  });
}

/// Central ChangeNotifier orchestrating all 7 stores, live Finance Sheets sync,
/// Morgana state, Focus/Manual timers, Quick Command/AI, and floating toast notifications.
class MorganaAppState extends ChangeNotifier {
  late final FinanceStore finance;
  late final TaskStore tasks;
  late final NoteStore notes;
  late final LocalEventStore events;
  late final SocialStatsStore stats;
  late final HabitStore habits;
  late final SettingsStore settings;

  final MorganaAudioService audio;
  final GoogleSheetsFinanceClient sheetsClient;
  final MorganaAiClient aiClient;

  bool ready = false;
  String activeTab = 'home';

  // Morgana live stage state
  String morganaEmotion = 'neutral';
  String morganaSpeech = '';
  bool morganaTalking = false;
  int _quipIdx = 0;

  // Global floating Persona 5 toast
  P5ToastMessage? currentToast;
  int _toastSeq = 0;

  // Finance Google Sheets live sync state
  int financeYear = DateTime.now().year;
  int financeMonth = DateTime.now().month;
  bool sheetBusy = false;
  String sheetError = '';
  final Map<String, FinanceSheetMonthData> _sheetCache = {};
  FinanceSheetMonthData? sheetData;

  // Focus & Manual Timers
  bool focusRunning = false;
  String focusPhase = 'focus'; // 'focus' | 'rest'
  int focusLeftSec = 25 * 60;
  int focusTotalSec = 25 * 60;
  int focusCompleted = 0;
  final List<ManualTimerItem> manualTimers = [];

  // Floating Bubble Overlay status
  bool overlayPermission = false;
  bool overlayRunning = false;

  // Alerts deferred silently while user is asleep (Sleep Mode)
  final List<String> _deferredAlerts = [];

  Timer? _clockTimer;
  Timer? _sheetPollTimer;
  Timer? _talkTimer;
  bool _disposed = false;

  bool get sleepMode => ready && settings['sleep_mode'] == true;

  MorganaAppState({
    MorganaAudioService? audioService,
    GoogleSheetsFinanceClient? sheets,
    MorganaAiClient? ai,
  })  : audio = audioService ?? MorganaAudioService(),
        sheetsClient = sheets ?? GoogleSheetsFinanceClient(),
        aiClient = ai ?? MorganaAiClient();

  Future<void> init({Directory? storageDirOverride, bool enablePlatformHooks = true}) async {
    final dir = storageDirOverride ?? await getApplicationDocumentsDirectory();
    finance = FinanceStore(File('${dir.path}/finance.json'));
    tasks = TaskStore(File('${dir.path}/tasks.json'));
    notes = NoteStore(File('${dir.path}/notes.json'));
    events = LocalEventStore(File('${dir.path}/local_events.json'));
    stats = SocialStatsStore(File('${dir.path}/social_stats.json'));
    habits = HabitStore(File('${dir.path}/habits.json'));
    settings = SettingsStore(File('${dir.path}/settings.json'));

    await Future.wait([
      finance.load(),
      tasks.load(),
      notes.load(),
      events.load(),
      stats.load(),
      habits.load(),
      settings.load(),
    ]);

    audio.applySettings(
      soundEnabled: settings['sound_enabled'] == true,
      voiceVol: (settings['voice_volume'] as num?)?.toInt() ?? 80,
      sfxVol: (settings['sfx_volume'] as num?)?.toInt() ?? 75,
    );
    focusLeftSec = ((settings['focus_min'] as num?)?.toInt() ?? 25) * 60;
    focusTotalSec = focusLeftSec;

    if (settings['sleep_mode'] == true) {
      morganaSpeech = "Sleep Mode is on—I'll assume you're asleep and keep everything quiet. Tap AWAKE when you're back!";
      morganaEmotion = 'neutral';
    } else {
      morganaSpeech = greetingForHour(DateTime.now().hour, (settings['ai_call_user'] ?? 'Joker').toString());
      morganaEmotion = resolveReaction(
        morganaSpeech,
        fallback: 'wink',
        context: 'startup',
        hour: DateTime.now().hour,
      ).emotion;
    }
    ready = true;
    notifyListeners();

    if (enablePlatformHooks) {
      NativeBridge.listenOpenTab((tab) => navigateTo(tab));
      final launchTab = await NativeBridge.getPendingLaunchTab();
      if (launchTab != null && launchTab.isNotEmpty) {
        activeTab = launchTab;
      }
      await refreshOverlayStatus();
      if (settings['floating_bubble_enabled'] == true && overlayPermission && !overlayRunning) {
        await NativeBridge.startOverlay();
        await refreshOverlayStatus();
      }
      if (settings.financeSheetsActive) {
        unawaited(refreshFinanceSheet(silent: true));
      }
      _startSheetAutoRefresh();
    }
    notifyListeners();
  }

  void _safeNotify() {
    if (!_disposed) notifyListeners();
  }

  void notify(String text, {P5ToastTone tone = P5ToastTone.ok, int ms = 2400}) {
    final msg = text.trim();
    if (msg.isEmpty) return;
    currentToast = P5ToastMessage(id: ++_toastSeq, text: msg, tone: tone, durationMs: ms);
    _safeNotify();
  }

  void dismissToast(int id) {
    if (currentToast?.id == id) {
      currentToast = null;
      _safeNotify();
    }
  }

  void navigateTo(String tab) {
    activeTab = tab;
    if (tab == 'finance' && settings.financeSheetsActive) {
      unawaited(refreshFinanceSheet(silent: sheetData != null));
    }
    _safeNotify();
  }

  void morganaSay(
    String line, {
    String? emotion,
    String? voice,
    String context = '',
    bool playVoice = true,
  }) {
    morganaSpeech = line;
    final resolved = resolveReaction(
      line,
      emotion: emotion,
      voice: voice,
      context: context,
      hour: DateTime.now().hour,
    );
    morganaEmotion = resolved.emotion;
    morganaTalking = true;
    _talkTimer?.cancel();
    _talkTimer = Timer(const Duration(milliseconds: 1650), () {
      morganaTalking = false;
      if (sleepMode) {
        morganaEmotion = 'neutral';
      }
      _safeNotify();
    });
    final allowSleepVoice = context == 'sleep_on' || context == 'wake_up';
    if (playVoice && settings['manual_dnd'] != true && (!sleepMode || allowSleepVoice)) {
      audio.playVoice(resolved.voice);
    }
    _safeNotify();
  }

  Future<void> setSleepMode(bool enable, {bool announce = true}) async {
    final want = enable;
    if (sleepMode == want) return;
    await settings.updateAll({'sleep_mode': want});
    if (want) {
      if (announce) {
        morganaSay(
          "Sleep Mode is on—I'll assume you're already asleep and keep alerts quiet. Good night!",
          emotion: 'wink',
          voice: 'sleep_on',
          context: 'sleep_on',
        );
      } else {
        morganaTalking = false;
        morganaEmotion = 'neutral';
        morganaSpeech = "Sleep Mode is on—I'll assume you're asleep and keep everything quiet. Tap AWAKE when you're back!";
      }
      notify('Sleep Mode ON (Quiet while you sleep)', tone: P5ToastTone.gold);
    } else {
      final hour = DateTime.now().hour;
      final greet = (hour >= 4 && hour < 12) ? 'Good morning!' : 'Welcome back!';
      final vkey = (hour >= 4 && hour < 12) ? 'ohayou' : 'choushi';
      if (_deferredAlerts.isNotEmpty) {
        final queued = List<String>.from(_deferredAlerts);
        _deferredAlerts.clear();
        morganaSay(
          '$greet While you were asleep: ${queued.take(3).join(' · ')}',
          emotion: 'wink',
          voice: vkey,
          context: 'wake_up',
        );
      } else if (announce) {
        morganaSay(
          "$greet Sleep Mode is off—I'm wide awake and ready whenever you are!",
          emotion: 'wink',
          voice: vkey,
          context: 'wake_up',
        );
      } else {
        morganaEmotion = 'wink';
      }
      notify("Sleep Mode OFF — I'm awake!", tone: P5ToastTone.ok);
    }
    _safeNotify();
  }

  void tapMorgana() {
    if (sleepMode) {
      morganaSay(
        "Shh... Sleep Mode is on (assuming you're asleep). Tap AWAKE or type 'bangun' whenever you're up!",
        emotion: 'wink',
        playVoice: false,
      );
      return;
    }
    final line = kMorganaTapQuips[_quipIdx % kMorganaTapQuips.length];
    _quipIdx++;
    morganaSay(line);
  }

  // -- Finance Active Data & Google Sheets Live Sync -------------------------
  String _ymKey(int y, int m) => '$y-${m.toString().padLeft(2, '0')}';

  bool get isSheetMode => settings.financeSheetsActive;

  ({Map<String, int> balances, Map<String, dynamic> summary, List<Map<String, dynamic>> transactions})
      activeFinanceData() {
    if (isSheetMode) {
      final d = sheetData;
      if (d != null && d.ok && d.year == financeYear && d.month == financeMonth) {
        return (balances: d.balances, summary: d.summary, transactions: d.transactions);
      }
      return (
        balances: const {'Tunai': 0, 'BNI': 0, 'Mandiri': 0, 'Total': 0},
        summary: const {
          'income': 0,
          'expense': 0,
          'net': 0,
          'today_expense': 0,
          'count': 0,
          'by_category': {'Jajan': 0, 'Alat': 0, 'Mingguan&Bulanan': 0, 'Hiburan': 0},
        },
        transactions: const [],
      );
    }
    return (
      balances: finance.walletBalances(),
      summary: finance.monthSummary(financeYear, financeMonth),
      transactions: finance.monthTransactions(financeYear, financeMonth),
    );
  }

  void setFinancePeriod(int year, int month) {
    financeYear = year;
    financeMonth = month;
    if (isSheetMode) {
      sheetData = _sheetCache[_ymKey(year, month)];
      sheetError = '';
      unawaited(refreshFinanceSheet(silent: sheetData != null));
    }
    _safeNotify();
  }

  void shiftFinanceMonth(int delta) {
    var m = financeMonth + delta;
    var y = financeYear;
    if (m < 1) {
      m = 12;
      y--;
    } else if (m > 12) {
      m = 1;
      y++;
    }
    setFinancePeriod(y, m);
  }

  void _startSheetAutoRefresh() {
    _sheetPollTimer?.cancel();
    _sheetPollTimer = Timer.periodic(const Duration(seconds: 60), (_) {
      if (activeTab == 'finance' && isSheetMode && !sheetBusy) {
        unawaited(refreshFinanceSheet(silent: true));
      }
    });
  }

  Future<void> refreshFinanceSheet({bool silent = false}) async {
    if (!isSheetMode || sheetBusy) return;
    final y = financeYear;
    final m = financeMonth;
    sheetBusy = true;
    sheetError = '';
    if (!silent) {
      notify("Syncing '${monthTabName(m)}' from Google Sheets...", tone: P5ToastTone.info, ms: 1600);
    }
    _safeNotify();

    final res = await sheetsClient.fetchMonth(
      rawSheetId: (settings['sheets_sync_id'] ?? '').toString(),
      keyJson: (settings['sheets_key_json'] ?? '').toString(),
      year: y,
      month: m,
    );
    sheetBusy = false;
    if (res.ok) {
      _sheetCache[_ymKey(y, m)] = res;
      if (financeYear == y && financeMonth == m) {
        sheetData = res;
        sheetError = '';
      }
      if (!silent) {
        notify("Synced '${res.tab}' · ${res.transactions.length} entries", tone: P5ToastTone.ok);
      }
    } else {
      sheetError = res.error;
      if (!silent) {
        notify(res.error, tone: P5ToastTone.err, ms: 3200);
      }
    }
    _safeNotify();
  }

  Future<bool> enableFinanceSheetQuick() async {
    if (!settings.sheetsCredentialsReady) {
      navigateTo('settings');
      notify('Configure Sheet ID & kunci-google.json in Settings', tone: P5ToastTone.info);
      return false;
    }
    await settings.updateAll({'finance_sheets_enabled': true});
    notify('Switching to live Google Sheets sync...', tone: P5ToastTone.info, ms: 1600);
    await refreshFinanceSheet(silent: false);
    return true;
  }

  Future<FinanceSheetWriteResult> submitSheetTransaction({
    required String type,
    required int amount,
    required String title,
    required String category,
    required String wallet,
    required String toWallet,
    required String dateStr,
    Map<String, dynamic>? editingOldTx,
  }) async {
    sheetBusy = true;
    _safeNotify();
    final sheetId = (settings['sheets_sync_id'] ?? '').toString();
    final keyJson = (settings['sheets_key_json'] ?? '').toString();

    if (editingOldTx != null && editingOldTx['sheet_cells'] is List) {
      final oldCells = (editingOldTx['sheet_cells'] as List).cast<Map<String, dynamic>>();
      final clr = await sheetsClient.clearCells(rawSheetId: sheetId, keyJson: keyJson, cells: oldCells);
      if (!clr.ok) {
        sheetBusy = false;
        _safeNotify();
        notify(clr.error, tone: P5ToastTone.err, ms: 3200);
        return clr;
      }
    }

    final res = await sheetsClient.writeTransaction(
      rawSheetId: sheetId,
      keyJson: keyJson,
      type: type,
      amount: amount,
      title: title,
      category: category,
      wallet: wallet,
      toWallet: toWallet,
      dateStr: dateStr,
    );
    sheetBusy = false;
    if (res.ok) {
      audio.playSfx('select');
      notify(res.message, tone: editingOldTx != null ? P5ToastTone.gold : P5ToastTone.ok);
      await refreshFinanceSheet(silent: true);
    } else {
      notify(res.error, tone: P5ToastTone.err, ms: 3200);
      _safeNotify();
    }
    return res;
  }

  Future<bool> deleteSheetTransaction(Map<String, dynamic> tx) async {
    if (tx['sheet_kind'] == 'qris_pool') {
      notify('QRIS monthly pool rows update automatically.', tone: P5ToastTone.info);
      return false;
    }
    final cells = (tx['sheet_cells'] as List?)?.cast<Map<String, dynamic>>() ?? const [];
    if (cells.isEmpty) return false;
    sheetBusy = true;
    _safeNotify();
    final res = await sheetsClient.clearCells(
      rawSheetId: (settings['sheets_sync_id'] ?? '').toString(),
      keyJson: (settings['sheets_key_json'] ?? '').toString(),
      cells: cells,
    );
    sheetBusy = false;
    if (res.ok) {
      audio.playSfx('close');
      notify("Deleted '${tx['title']}' in Google Sheets", tone: P5ToastTone.info);
      await refreshFinanceSheet(silent: true);
      return true;
    }
    notify(res.error, tone: P5ToastTone.err, ms: 3200);
    _safeNotify();
    return false;
  }

  // -- Tasks + Auto Social Stat Reward ---------------------------------------
  Future<void> toggleTaskDone(String id, bool done) async {
    final task = tasks.get(id);
    if (task == null) return;
    await tasks.setDone(id, done);
    if (done) {
      audio.playSfx('select');
      var msg = "Completed '${task['title']}'!";
      if (settings['auto_stat_rewards'] == true && task['rewarded'] != true) {
        await tasks.markRewarded(id);
        final sk = TaskStore.statOf(task);
        final (_, oldLv, newLv) = await stats.add(
          sk,
          task['title'] as String,
          points: task['priority'] == 'high' ? 3 : 2,
          source: 'task',
          refId: 'task:$id',
        );
        final sShort = kSocialStats[sk]?.short ?? 'PRO';
        msg = "Completed! +${task['priority'] == 'high' ? 3 : 2} $sShort";
        if (newLv > oldLv) {
          audio.playSfx('rank_up');
          morganaSay('Rank up! ${kSocialStats[sk]!.label} reached Lv $newLv!', emotion: 'excited', context: 'rank_up');
        } else {
          morganaSay("Checked off '${task['title']}'!", emotion: 'excited', context: 'task_done');
        }
      }
      notify(msg, tone: P5ToastTone.ok);
    } else {
      audio.playSfx('cancel');
      notify('Marked task as pending', tone: P5ToastTone.info, ms: 1600);
    }
    _safeNotify();
  }

  // -- Focus & Manual Timers Engine ------------------------------------------
  void _ensureClockRunning() {
    if (_clockTimer != null) return;
    _clockTimer = Timer.periodic(const Duration(seconds: 1), (_) => _onClockTick());
  }

  void _stopClockIfIdle() {
    if (!focusRunning && manualTimers.isEmpty) {
      _clockTimer?.cancel();
      _clockTimer = null;
    }
  }

  void _onClockTick() {
    var changed = false;
    if (focusRunning) {
      focusLeftSec--;
      changed = true;
      if (focusLeftSec <= 0) {
        if (focusPhase == 'focus') {
          focusCompleted++;
          final fStat = (settings['focus_stat'] ?? 'knowledge').toString();
          if (fStat != 'none' && kSocialStats.containsKey(fStat)) {
            unawaited(stats.add(fStat, 'Focus session (${focusTotalSec ~/ 60}m)', points: 2, source: 'focus'));
          }
          focusPhase = 'rest';
          focusTotalSec = ((settings['rest_min'] as num?)?.toInt() ?? 5) * 60;
          focusLeftSec = focusTotalSec;
          if (sleepMode) {
            _deferredAlerts.add('Focus session complete!');
          } else {
            morganaSay("Focus complete! Take a ${focusTotalSec ~/ 60}-minute breather!", emotion: 'excited', voice: 'gokurou');
            notify('Focus session complete! Time for a break.', tone: P5ToastTone.ok, ms: 3200);
            unawaited(NativeBridge.showNotification(
              title: 'Focus Complete!',
              body: 'Great work! Take a ${focusTotalSec ~/ 60}-minute break and stretch.',
              tab: 'timer',
            ));
          }
        } else {
          focusRunning = false;
          focusPhase = 'focus';
          focusTotalSec = ((settings['focus_min'] as num?)?.toInt() ?? 25) * 60;
          focusLeftSec = focusTotalSec;
          if (sleepMode) {
            _deferredAlerts.add('Break finished!');
          } else {
            morganaSay("Break's over! Ready for another Focus sprint?", emotion: 'excited', voice: 'oh_excited');
            notify('Break finished — ready for next Focus!', tone: P5ToastTone.gold, ms: 3000);
            unawaited(NativeBridge.showNotification(
              title: 'Break Over!',
              body: "Morgana says: Ready for another Focus sprint?",
              tab: 'timer',
            ));
          }
        }
      }
    }

    if (manualTimers.isNotEmpty) {
      final finished = <ManualTimerItem>[];
      for (final tm in manualTimers) {
        tm.remainingSeconds--;
        if (tm.remainingSeconds <= 0) finished.add(tm);
      }
      changed = true;
      for (final tm in finished) {
        manualTimers.remove(tm);
        if (sleepMode) {
          _deferredAlerts.add("Timer '${tm.label}' finished!");
        } else {
          morganaSay("Time's up for '${tm.label}'!", emotion: 'shout', context: 'timer_up');
          notify("Time's up: ${tm.label}!", tone: P5ToastTone.gold, ms: 3500);
          unawaited(NativeBridge.showNotification(
            title: "Time's Up!",
            body: "Timer finished: ${tm.label}",
            tab: 'timer',
          ));
        }
      }
    }

    _stopClockIfIdle();
    if (changed) _safeNotify();
  }

  void startFocus(int focusMin, int restMin) {
    final fm = focusMin.clamp(1, 180);
    final rm = restMin.clamp(1, 60);
    settings.updateAll({'focus_min': fm, 'rest_min': rm});
    focusRunning = true;
    focusPhase = 'focus';
    focusTotalSec = fm * 60;
    focusLeftSec = focusTotalSec;
    _ensureClockRunning();
    audio.playSfx('select');
    morganaSay("Locked in for ${fm}m! I'll watch the clock!", emotion: 'smug', context: 'focus_start');
    notify('Focus started: ${fm}m focus / ${rm}m break!', tone: P5ToastTone.ok);
    _safeNotify();
  }

  void stopFocus() {
    focusRunning = false;
    focusPhase = 'focus';
    focusTotalSec = ((settings['focus_min'] as num?)?.toInt() ?? 25) * 60;
    focusLeftSec = focusTotalSec;
    _stopClockIfIdle();
    audio.playSfx('cancel');
    notify('Focus session stopped', tone: P5ToastTone.info);
    _safeNotify();
  }

  void addManualTimer(int totalSeconds, String label) {
    if (totalSeconds <= 0) return;
    final clean = label.trim().isEmpty ? 'Manual Timer' : label.trim();
    manualTimers.add(ManualTimerItem(
      id: '${DateTime.now().microsecondsSinceEpoch}',
      label: clean,
      totalSeconds: totalSeconds,
      remainingSeconds: totalSeconds,
    ));
    _ensureClockRunning();
    audio.playSfx('select');
    notify("Started timer: $clean (${formatHms(totalSeconds)})", tone: P5ToastTone.ok);
    _safeNotify();
  }

  void cancelManualTimer(String id) {
    manualTimers.removeWhere((t) => t.id == id);
    _stopClockIfIdle();
    audio.playSfx('cancel');
    notify('Timer cancelled', tone: P5ToastTone.info, ms: 1500);
    _safeNotify();
  }

  static String formatHms(int seconds) {
    final s = seconds < 0 ? 0 : seconds;
    final h = s ~/ 3600;
    final m = (s % 3600) ~/ 60;
    final sec = s % 60;
    if (h > 0) {
      return '${h.toString().padLeft(2, '0')}:${m.toString().padLeft(2, '0')}:${sec.toString().padLeft(2, '0')}';
    }
    return '${m.toString().padLeft(2, '0')}:${sec.toString().padLeft(2, '0')}';
  }

  // -- Quick Command & AI Dispatcher -----------------------------------------
  Future<({String reply, String badge, String actionLabel, String actionTab, bool accentRed})> runQuickCommand(
      String input) async {
    final cmd = parseQuickCommand(input);
    switch (cmd.kind) {
      case CommandKind.addTask:
        final t = await tasks.add(cmd.text);
        notify("Added task: ${cmd.text}", tone: P5ToastTone.ok);
        morganaSay("Got it! Added '${cmd.text}' to your Tasks!", emotion: 'smug', voice: 'kettei');
        _safeNotify();
        return (
          reply: "Added '${t?['title'] ?? cmd.text}' to Tasks! I'll keep an eye on it.",
          badge: 'TASK ADDED',
          actionLabel: 'OPEN TASKS',
          actionTab: 'tasks',
          accentRed: false,
        );
      case CommandKind.addNote:
        await notes.add(cmd.text.length > 44 ? cmd.text.substring(0, 44) : cmd.text, cmd.text);
        notify("Saved note: ${cmd.text}", tone: P5ToastTone.ok);
        morganaSay("Jotted that down in Notes!", emotion: 'wink', context: 'note_save');
        _safeNotify();
        return (
          reply: "Saved quick note '${cmd.text}' in your Notebook!",
          badge: 'NOTE SAVED',
          actionLabel: 'OPEN NOTES',
          actionTab: 'notes',
          accentRed: false,
        );
      case CommandKind.addEvent:
        final st = DateTime.now().add(const Duration(hours: 1));
        await events.add(cmd.text, DateTime(st.year, st.month, st.day, st.hour, 0));
        notify("Added event: ${cmd.text}", tone: P5ToastTone.ok);
        morganaSay("Scheduled '${cmd.text}' on your 7-day timeline!", emotion: 'wink', voice: 'kettei');
        _safeNotify();
        return (
          reply: "Scheduled '${cmd.text}' starting at ${st.hour.toString().padLeft(2, '0')}:00!",
          badge: 'EVENT ADDED',
          actionLabel: 'OPEN EVENTS',
          actionTab: 'events',
          accentRed: false,
        );
      case CommandKind.startFocus:
        startFocus(cmd.minutes, (settings['rest_min'] as num?)?.toInt() ?? 5);
        return (
          reply: "Focus sprint started for ${cmd.minutes} minutes! Stay locked in!",
          badge: 'FOCUS ${cmd.minutes}M',
          actionLabel: 'OPEN TIMER',
          actionTab: 'timer',
          accentRed: true,
        );
      case CommandKind.startBreak:
        addManualTimer(cmd.minutes * 60, '${cmd.minutes}m Break');
        morganaSay("${cmd.minutes}-minute break started! Stretch and hydrate!", emotion: 'wink', voice: 'gokurou');
        return (
          reply: "${cmd.minutes}-minute break started! Stretch your shoulders and grab some water.",
          badge: 'BREAK ${cmd.minutes}M',
          actionLabel: 'OPEN TIMER',
          actionTab: 'timer',
          accentRed: false,
        );
      case CommandKind.checkBalance:
        final d = activeFinanceData();
        final b = d.balances;
        final src = isSheetMode ? 'Google Sheets' : 'Local';
        final line =
            "[$src] Total: ${formatRupiah(b['Total'] ?? 0)} — Tunai ${formatRupiah(b['Tunai'] ?? 0)} · BNI ${formatRupiah(b['BNI'] ?? 0)} · Mandiri ${formatRupiah(b['Mandiri'] ?? 0)}";
        morganaSay(line, emotion: 'smug');
        return (
          reply: line,
          badge: isSheetMode ? 'SHEETS SALDO' : 'LOCAL SALDO',
          actionLabel: 'OPEN FINANCE',
          actionTab: 'finance',
          accentRed: false,
        );
      case CommandKind.toggleDnd:
        final nxt = cmd.boolFlag ?? (settings['manual_dnd'] != true);
        await settings.updateAll({'manual_dnd': nxt});
        notify(nxt ? 'Do Not Disturb enabled' : 'Do Not Disturb disabled', tone: P5ToastTone.gold);
        _safeNotify();
        return (
          reply: nxt
              ? "Got it—I'll keep quiet while Do Not Disturb is active."
              : "Do Not Disturb is off! I'm back on duty!",
          badge: nxt ? 'DND ON' : 'DND OFF',
          actionLabel: '',
          actionTab: '',
          accentRed: nxt,
        );
      case CommandKind.toggleSleep:
        final nxt = cmd.boolFlag ?? !sleepMode;
        await setSleepMode(nxt, announce: true);
        return (
          reply: nxt
              ? "Sleep Mode is on—I'll assume you're asleep and keep everything quiet. Good night!"
              : "Welcome back! Sleep Mode is off—I'm wide awake!",
          badge: nxt ? 'SLEEP ON' : 'AWAKE',
          actionLabel: '',
          actionTab: '',
          accentRed: false,
        );
      case CommandKind.checkStatus:
        final pend = tasks.pending().length;
        final todPts = stats.todayPoints();
        final hSum = habits.todaySummary();
        final bal = activeFinanceData().balances['Total'] ?? 0;
        final line =
            "Status recap: $pend pending tasks · ${hSum.done}/${hSum.total} habits done · +$todPts stat pts today · Balance ${formatRupiah(bal)}.";
        morganaSay(line, emotion: 'wink');
        return (
          reply: line,
          badge: 'DAILY RECAP',
          actionLabel: 'OPEN STATS',
          actionTab: 'stats',
          accentRed: false,
        );
      case CommandKind.openTab:
        if (cmd.amount != null && cmd.tab == 'finance') {
          await finance.addTransaction(
            type: 'expense',
            amount: cmd.amount!,
            title: cmd.text.isEmpty ? 'Quick Expense' : cmd.text,
          );
          notify('Recorded -${formatRupiah(cmd.amount!)}', tone: P5ToastTone.ok);
          _safeNotify();
          return (
            reply: "Recorded expense ${formatRupiah(cmd.amount!)} (${cmd.text.isEmpty ? 'Jajan' : cmd.text})!",
            badge: 'RECORDED',
            actionLabel: 'OPEN FINANCE',
            actionTab: 'finance',
            accentRed: false,
          );
        }
        navigateTo(cmd.tab);
        return (
          reply: "Opening ${cmd.tab.toUpperCase()}!",
          badge: 'NAVIGATE',
          actionLabel: '',
          actionTab: '',
          accentRed: false,
        );
      case CommandKind.chat:
        if (settings['ai_enabled'] == true) {
          final ctx =
              'Pending tasks: ${tasks.pending().length}, Balance: ${formatRupiah(activeFinanceData().balances['Total'] ?? 0)}, Today stat pts: ${stats.todayPoints()}';
          final res = await aiClient.ask(
            provider: (settings['ai_provider'] ?? 'gemini').toString(),
            apiKey: (settings['ai_api_key'] ?? '').toString(),
            model: (settings['ai_model'] ?? 'gemini-3.5-flash-lite').toString(),
            endpoint: (settings['ai_endpoint'] ?? '').toString(),
            callUser: (settings['ai_call_user'] ?? 'Joker').toString(),
            userText: cmd.text,
            contextNote: ctx,
          );
          morganaSay(res.text, emotion: res.emotion, voice: res.voice);
          var jumpTab = '';
          var jumpLbl = '';
          if (res.action.startsWith('open_')) {
            jumpTab = res.action.replaceFirst('open_', '');
            jumpLbl = 'OPEN ${jumpTab.toUpperCase()}';
          }
          return (
            reply: res.text,
            badge: res.badge,
            actionLabel: jumpLbl,
            actionTab: jumpTab,
            accentRed: !res.ok,
          );
        }
        final low = cmd.text.toLowerCase();
        final isCat = low.contains('cat') || low.contains('kucing');
        final reply = isCat
            ? "Hey! For the last time, I am NOT a cat! I'm Morgana—and I'm here to keep you sharp!"
            : "Try commands like 'task finish report', 'note great idea #work', 'saldo', 'sleep', 'bangun', or enable Gemini AI in Settings!";
        morganaSay(
          reply,
          emotion: isCat ? 'angry' : 'wink',
          voice: isCat ? 'chigau' : null,
          context: isCat ? 'cat_tease' : '',
        );
        return (
          reply: reply,
          badge: 'MORGANA',
          actionLabel: 'SETTINGS',
          actionTab: 'settings',
          accentRed: false,
        );
    }
  }

  // -- Floating Bubble Overlay -----------------------------------------------
  Future<void> refreshOverlayStatus() async {
    overlayPermission = await NativeBridge.canDrawOverlays();
    overlayRunning = await NativeBridge.isOverlayRunning();
    _safeNotify();
  }

  Future<void> setFloatingBubbleEnabled(bool enable) async {
    if (enable) {
      final ok = await NativeBridge.canDrawOverlays();
      if (!ok) {
        notify('Grant "Display over other apps" permission for Morgana', tone: P5ToastTone.gold, ms: 3200);
        await NativeBridge.requestOverlayPermission();
        return;
      }
      final started = await NativeBridge.startOverlay();
      await settings.updateAll({'floating_bubble_enabled': started});
      await refreshOverlayStatus();
      notify(started ? 'Floating Morgana bubble is live!' : 'Could not start floating bubble',
          tone: started ? P5ToastTone.ok : P5ToastTone.warn);
    } else {
      await NativeBridge.stopOverlay();
      await settings.updateAll({'floating_bubble_enabled': false});
      await refreshOverlayStatus();
      notify('Floating Morgana bubble hidden', tone: P5ToastTone.info);
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _clockTimer?.cancel();
    _sheetPollTimer?.cancel();
    _talkTimer?.cancel();
    unawaited(audio.dispose());
    super.dispose();
  }
}
