import 'rupiah.dart';

/// Offline natural-language Quick Command parser matching desktop `command_engine.py` v1.5.3.

enum CommandKind {
  addTask,
  addNote,
  addEvent,
  startFocus,
  startBreak,
  checkBalance,
  checkStatus,
  toggleDnd,
  toggleSleep,
  openTab,
  chat,
}

class ParsedCommand {
  final CommandKind kind;
  final String text;
  final int minutes;
  final bool? boolFlag;
  final String tab;
  final int? amount;

  const ParsedCommand({
    required this.kind,
    this.text = '',
    this.minutes = 0,
    this.boolFlag,
    this.tab = '',
    this.amount,
  });
}

ParsedCommand parseQuickCommand(String raw) {
  final trimmed = raw.trim();
  final low = trimmed.toLowerCase();

  // Balance / Finance check (/saldo, saldo, balance, cek saldo)
  if (RegExp(r'^/?(saldo|balance|cek saldo|uang|dompet)$').hasMatch(low)) {
    return const ParsedCommand(kind: CommandKind.checkBalance, tab: 'finance');
  }

  // Open specific tab directly
  final openM = RegExp(r'^(?:open|buka)\s+(finance|keuangan|tasks?|tugas|notes?|catatan|events?|jadwal|stats?|timer|settings?)$').firstMatch(low);
  if (openM != null) {
    final w = openM.group(1)!;
    final tab = (w == 'keuangan' || w == 'finance')
        ? 'finance'
        : (w.startsWith('task') || w == 'tugas')
            ? 'tasks'
            : (w.startsWith('note') || w == 'catatan')
                ? 'notes'
                : (w.startsWith('event') || w == 'jadwal')
                    ? 'events'
                    : w.startsWith('stat')
                        ? 'stats'
                        : w.startsWith('set')
                            ? 'settings'
                            : 'timer';
    return ParsedCommand(kind: CommandKind.openTab, tab: tab);
  }

  // Add Task
  final taskM = RegExp(r'^(?:/?task|tugas|todo|remind me(?:\s+to)?)\s+(.+)$', caseSensitive: false).firstMatch(trimmed);
  if (taskM != null) {
    return ParsedCommand(kind: CommandKind.addTask, text: taskM.group(1)!.trim(), tab: 'tasks');
  }

  // Add Note
  final noteM = RegExp(r'^(?:/?note|catatan|catat)\s+(.+)$', caseSensitive: false).firstMatch(trimmed);
  if (noteM != null) {
    return ParsedCommand(kind: CommandKind.addNote, text: noteM.group(1)!.trim(), tab: 'notes');
  }

  // Add Event
  final evM = RegExp(r'^(?:/?event|jadwal|acara)\s+(.+)$', caseSensitive: false).firstMatch(trimmed);
  if (evM != null) {
    return ParsedCommand(kind: CommandKind.addEvent, text: evM.group(1)!.trim(), tab: 'events');
  }

  // Focus timer
  final focM = RegExp(r'^(?:/?focus|fokus|pomodoro)(?:\s+(\d+))?m?$', caseSensitive: false).firstMatch(low);
  if (focM != null) {
    final mins = (int.tryParse(focM.group(1) ?? '25') ?? 25).clamp(1, 180);
    return ParsedCommand(kind: CommandKind.startFocus, minutes: mins, tab: 'timer');
  }

  // Break timer
  final brkM = RegExp(r'^(?:/?break|istirahat|rest)(?:\s+(\d+))?m?$', caseSensitive: false).firstMatch(low);
  if (brkM != null) {
    final mins = (int.tryParse(brkM.group(1) ?? '5') ?? 5).clamp(1, 60);
    return ParsedCommand(kind: CommandKind.startBreak, minutes: mins, tab: 'timer');
  }

  // Quick expense shorthand: "jajan 15rb kopi" or "beli 25k nasi"
  final expM = RegExp(r'^(?:beli|jajan|bayar|expense)\s+(\S+)\s*(.*)$', caseSensitive: false).firstMatch(trimmed);
  if (expM != null) {
    final amt = parseRupiah(expM.group(1));
    if (amt != null) {
      return ParsedCommand(
        kind: CommandKind.openTab,
        tab: 'finance',
        amount: amt,
        text: expM.group(2)?.trim() ?? '',
      );
    }
  }

  // Sleep Mode ("user is asleep") / Wake Up toggle (EN + ID)
  if (RegExp(r'^/?(sleep(?:\s+mode)?(?:\s+on)?|tidur|mau\s+tidur|aku\s+tidur|good\s*night|selamat\s+tidur)$').hasMatch(low)) {
    return const ParsedCommand(kind: CommandKind.toggleSleep, boolFlag: true);
  }
  if (RegExp(r'^/?(sleep(?:\s+mode)?\s+off|wake(?:\s+up)?|awake|i.?m\s+awake|bangun|aku\s+bangun)$').hasMatch(low)) {
    return const ParsedCommand(kind: CommandKind.toggleSleep, boolFlag: false);
  }

  // DND toggle
  if (RegExp(r'^/?dnd(?:\s+(on|off))?$').hasMatch(low)) {
    final flag = low.endsWith('on') ? true : (low.endsWith('off') ? false : null);
    return ParsedCommand(kind: CommandKind.toggleDnd, boolFlag: flag);
  }

  // Status / Wrap-up summary
  if (RegExp(r'^/?(status|wrapup|wrap-up|summary|rekap)$').hasMatch(low)) {
    return const ParsedCommand(kind: CommandKind.checkStatus);
  }

  return ParsedCommand(kind: CommandKind.chat, text: trimmed);
}
