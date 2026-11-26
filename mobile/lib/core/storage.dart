import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'rupiah.dart';

/// Atomic local JSON storage + all 7 domain stores matching desktop v1.5.3 schemas.

const List<String> kRadialTabKeys = [
  'tasks',
  'notes',
  'events',
  'stats',
  'timer',
  'finance',
  'ask',
  'settings',
];

const List<String> kSocialStatKeys = ['knowledge', 'guts', 'proficiency', 'charm', 'kindness'];
const List<int> kStatRankThresholds = [0, 30, 85, 175, 310];
const int kStatMasteryStep = 180;
const List<int> kQuickStatPoints = [1, 2, 3];
const int kDefaultStatPoints = 2;
const List<String> kTimeBlocks = ['Morning', 'Daytime', 'After School', 'Evening'];
const List<String> kNoteCategories = ['Personal', 'Study', 'Work', 'Ideas', 'Journal'];

class SocialStatMeta {
  final String key;
  final String label;
  final String short;
  final String examples;
  final String placeholder;
  final List<String> ranks;

  const SocialStatMeta({
    required this.key,
    required this.label,
    required this.short,
    required this.examples,
    required this.placeholder,
    required this.ranks,
  });
}

const Map<String, SocialStatMeta> kSocialStats = {
  'knowledge': SocialStatMeta(
    key: 'knowledge',
    label: 'Knowledge',
    short: 'KNO',
    examples: 'Study, lectures, coursework, deep focus',
    placeholder: 'e.g. Lecture review, 2h study session...',
    ranks: ['Oblivious', 'Learned', 'Scholarly', 'Encyclopedic', 'Erudite'],
  ),
  'guts': SocialStatMeta(
    key: 'guts',
    label: 'Guts',
    short: 'GUT',
    examples: 'Gym, workouts, tough challenges, physical training',
    placeholder: 'e.g. Gym session, strength workout...',
    ranks: ['Milquetoast', 'Bold', 'Staunch', 'Dauntless', 'Lionhearted'],
  ),
  'proficiency': SocialStatMeta(
    key: 'proficiency',
    label: 'Proficiency',
    short: 'PRO',
    examples: 'Craft, coding projects, sports technique, tasks',
    placeholder: 'e.g. Coding sprint, project polish...',
    ranks: ['Bumbling', 'Decent', 'Skilled', 'Expert', 'Transcendent'],
  ),
  'charm': SocialStatMeta(
    key: 'charm',
    label: 'Charm',
    short: 'CHA',
    examples: 'Movies, books, art, design, presentations',
    placeholder: 'e.g. Presentation prep, design work...',
    ranks: ['Existent', 'Head-Turning', 'Suave', 'Charismatic', 'Debonair'],
  ),
  'kindness': SocialStatMeta(
    key: 'kindness',
    label: 'Kindness',
    short: 'KIN',
    examples: 'Cleaning, laundry, groceries, helping others',
    placeholder: 'e.g. Room cleaning, laundry, grocery run...',
    ranks: ['Inoffensive', 'Considerate', 'Empathetic', 'Selfless', 'Angelic'],
  ),
};

int statLevel(int points) {
  final pts = max(0, points);
  final maxBase = kStatRankThresholds.length;
  final topReq = kStatRankThresholds.last;
  if (pts >= topReq) {
    return maxBase + (pts - topReq) ~/ kStatMasteryStep;
  }
  var lv = 1;
  for (var i = 1; i < maxBase; i++) {
    if (pts >= kStatRankThresholds[i]) {
      lv = i + 1;
    } else {
      break;
    }
  }
  return lv;
}

({int level, int into, int span, int toNext}) statLevelProgress(int points) {
  final pts = max(0, points);
  final lv = statLevel(pts);
  final maxBase = kStatRankThresholds.length;
  late final int bandStart;
  late final int nextAt;
  if (lv < maxBase) {
    bandStart = kStatRankThresholds[lv - 1];
    nextAt = kStatRankThresholds[lv];
  } else {
    final mIdx = lv - maxBase;
    bandStart = kStatRankThresholds.last + mIdx * kStatMasteryStep;
    nextAt = bandStart + kStatMasteryStep;
  }
  final span = max(1, nextAt - bandStart);
  return (level: lv, into: pts - bandStart, span: span, toNext: max(0, nextAt - pts));
}

double statPentagonFraction(num points) {
  final pts = max(0.0, points.toDouble());
  final topReq = kStatRankThresholds.last.toDouble();
  if (pts >= topReq) return 1.0;
  for (var i = 0; i < kStatRankThresholds.length - 1; i++) {
    final lo = kStatRankThresholds[i].toDouble();
    final hi = kStatRankThresholds[i + 1].toDouble();
    if (pts < hi) {
      final frac = (pts - lo) / max(1.0, hi - lo);
      final rankFloat = (i + 1) + frac;
      return (rankFloat / 5.0).clamp(0.16, 1.0);
    }
  }
  return 1.0;
}

String statRankTitle(String stat, int level) {
  final ranks = kSocialStats[stat]?.ranks ?? const ['Rank 1'];
  final lv = max(1, level);
  if (lv <= ranks.length) return ranks[lv - 1];
  return '${ranks.last} ★${lv - ranks.length}';
}

({int total, int maxBase}) overallConfidantLevel(Map<String, int> totals) {
  var sum = 0;
  for (final k in kSocialStatKeys) {
    sum += statLevel(totals[k] ?? 0);
  }
  return (total: sum, maxBase: kSocialStatKeys.length * kStatRankThresholds.length);
}

String defaultTimeBlock([int? hour]) {
  final h = hour ?? DateTime.now().hour;
  if (h < 12) return kTimeBlocks[0];
  if (h < 15) return kTimeBlocks[1];
  if (h < 18) return kTimeBlocks[2];
  return kTimeBlocks[3];
}

final Random _rng = Random();
String _newId([int len = 10]) {
  const chars = '0123456789abcdef';
  final buf = StringBuffer();
  for (var i = 0; i < len; i++) {
    buf.write(chars[_rng.nextInt(chars.length)]);
  }
  return buf.toString();
}

String todayIso([DateTime? now]) {
  final d = now ?? DateTime.now();
  return '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
}

String nowIso() => DateTime.now().toIso8601String().split('.').first;

DateTime? parseIsoDate(String? raw) {
  if (raw == null || raw.trim().isEmpty) return null;
  return DateTime.tryParse(raw.trim());
}

Future<bool> writeJsonAtomic(File file, Object payload) async {
  final tmp = File('${file.path}.${_newId(6)}.tmp');
  try {
    await file.parent.create(recursive: true);
    await tmp.writeAsString(const JsonEncoder.withIndent('  ').convert(payload));
    await tmp.rename(file.path);
    return true;
  } catch (_) {
    try {
      if (await tmp.exists()) await tmp.delete();
    } catch (_) {}
    return false;
  }
}

Future<dynamic> readJsonSafe(File file) async {
  try {
    if (!await file.exists()) return null;
    final text = await file.readAsString();
    return jsonDecode(text);
  } catch (_) {
    try {
      if (await file.exists()) {
        final stamp = DateTime.now().millisecondsSinceEpoch;
        await file.rename('${file.path}.corrupt-$stamp');
      }
    } catch (_) {}
    return null;
  }
}

// ---------------------------------------------------------------------------
// 1. FINANCE STORE (Transactions, Budgets, Shortcuts, Recurring Bills)
// ---------------------------------------------------------------------------
const List<Map<String, dynamic>> kDefaultFinanceShortcuts = [
  {'name': 'Galon', 'category': 'Mingguan&Bulanan', 'amount': 7000},
  {'name': 'Kopi', 'category': 'Jajan', 'amount': null},
  {'name': 'Tempe', 'category': 'Jajan', 'amount': null},
  {'name': 'Billiard', 'category': 'Hiburan', 'amount': null},
];

class FinanceStore {
  static const int maxTransactions = 3000;
  final File file;
  List<Map<String, dynamic>> _transactions = [];
  Map<String, int> _budgets = {};
  List<Map<String, dynamic>> _shortcuts = [];
  List<Map<String, dynamic>> _bills = [];

  FinanceStore(this.file) {
    _shortcuts = kDefaultFinanceShortcuts.map((e) => Map<String, dynamic>.from(e)).toList();
  }

  Future<void> load() async {
    _transactions = [];
    _budgets = {};
    _shortcuts = kDefaultFinanceShortcuts.map((e) => Map<String, dynamic>.from(e)).toList();
    _bills = [];
    final raw = await readJsonSafe(file);
    if (raw is! Map) return;

    final txList = raw['transactions'];
    if (txList is List) {
      for (final item in txList) {
        final cleaned = _cleanTx(item);
        if (cleaned != null) _transactions.add(cleaned);
      }
      if (_transactions.length > maxTransactions) {
        _transactions = _transactions.sublist(_transactions.length - maxTransactions);
      }
    }
    final rawBud = raw['budgets'];
    if (rawBud is Map) {
      rawBud.forEach((k, v) {
        final cat = normalizeFinanceCategory(k.toString());
        final amt = parseRupiah(v);
        if (amt != null) _budgets[cat] = amt;
      });
    }
    final rawSc = raw['shortcuts'];
    if (rawSc is List) {
      final seen = <String>{};
      final list = <Map<String, dynamic>>[];
      for (final item in rawSc) {
        if (item is! Map) continue;
        final name = (item['name'] ?? '').toString().trim();
        if (name.isEmpty || seen.contains(name.toLowerCase())) continue;
        seen.add(name.toLowerCase());
        list.add({
          'name': name.length > 40 ? name.substring(0, 40) : name,
          'category': normalizeFinanceCategory(item['category']?.toString()),
          'amount': item['amount'] != null ? parseRupiah(item['amount']) : null,
        });
      }
      _shortcuts = list;
    }
    final rawBills = raw['bills'];
    if (rawBills is List) {
      for (final item in rawBills) {
        if (item is! Map) continue;
        final name = (item['name'] ?? '').toString().trim();
        final amt = parseRupiah(item['amount']);
        if (name.isEmpty || amt == null) continue;
        final dueDay = (int.tryParse('${item['day']}') ?? 1).clamp(1, 31);
        final paidRaw = item['paid_months'];
        final paid = paidRaw is List
            ? paidRaw.map((m) => m.toString().trim()).where((m) => RegExp(r'^\d{4}-\d{2}$').hasMatch(m)).toSet().toList()
            : <String>[];
        paid.sort();
        _bills.add({
          'id': (item['id'] ?? _newId(8)).toString(),
          'name': name.length > 60 ? name.substring(0, 60) : name,
          'day': dueDay,
          'amount': amt,
          'category': normalizeFinanceCategory(item['category']?.toString()),
          'wallet': normalizeFinanceWallet(item['wallet']?.toString()),
          'paid_months': paid.length > 24 ? paid.sublist(paid.length - 24) : paid,
        });
      }
    }
  }

  Future<bool> save() {
    final trimmed = _transactions.length > maxTransactions
        ? _transactions.sublist(_transactions.length - maxTransactions)
        : _transactions;
    return writeJsonAtomic(file, {
      'transactions': trimmed,
      'budgets': _budgets,
      'shortcuts': _shortcuts,
      'bills': _bills,
    });
  }

  Map<String, dynamic>? _cleanTx(Object? raw) {
    if (raw is! Map) return null;
    final txType = (raw['type'] ?? 'expense').toString().trim().toLowerCase();
    if (txType != 'expense' && txType != 'income' && txType != 'transfer') return null;
    final amt = parseRupiah(raw['amount']);
    if (amt == null) return null;
    var dayS = (raw['date'] ?? '').toString().trim();
    if (!RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(dayS) || DateTime.tryParse(dayS) == null) {
      dayS = todayIso();
    }
    final wallet = normalizeFinanceWallet(raw['wallet']?.toString());
    final toWallet = txType == 'transfer' ? normalizeFinanceWallet(raw['to_wallet']?.toString()) : '';
    if (txType == 'transfer' && toWallet == wallet) return null;
    final category = txType == 'expense' ? normalizeFinanceCategory(raw['category']?.toString()) : '';
    var title = (raw['title'] ?? '').toString().trim();
    if (title.length > 120) title = title.substring(0, 120);
    if (title.isEmpty) {
      title = txType == 'transfer' ? 'Transfer $wallet → $toWallet' : (txType == 'income' ? 'Income' : category);
    }
    return {
      'id': (raw['id'] ?? _newId(8)).toString(),
      'type': txType,
      'amount': amt,
      'title': title,
      'category': category,
      'wallet': wallet,
      'to_wallet': toWallet,
      'date': dayS,
      'created': (raw['created'] ?? nowIso()).toString(),
      'source': (raw['source'] ?? 'manual').toString(),
    };
  }

  List<Map<String, dynamic>> allTransactions() => _transactions.map((e) => Map<String, dynamic>.from(e)).toList();

  Map<String, dynamic>? getTransaction(String id) {
    for (final t in _transactions) {
      if (t['id'] == id) return Map<String, dynamic>.from(t);
    }
    return null;
  }

  Future<Map<String, dynamic>?> addTransaction({
    required String type,
    required Object amount,
    required String title,
    String category = 'Jajan',
    String wallet = 'Tunai',
    String toWallet = '',
    String? day,
    String source = 'manual',
  }) async {
    final cleaned = _cleanTx({
      'id': _newId(8),
      'type': type,
      'amount': amount,
      'title': title,
      'category': category,
      'wallet': wallet,
      'to_wallet': toWallet,
      'date': day ?? todayIso(),
      'created': nowIso(),
      'source': source,
    });
    if (cleaned == null) return null;
    _transactions.add(cleaned);
    await save();
    return Map<String, dynamic>.from(cleaned);
  }

  Future<(Map<String, dynamic>?, Map<String, dynamic>?)> updateTransaction({
    required String id,
    required String type,
    required Object amount,
    required String title,
    String category = 'Jajan',
    String wallet = 'Tunai',
    String toWallet = '',
    String? day,
  }) async {
    final idx = _transactions.indexWhere((t) => t['id'] == id);
    if (idx < 0) return (null, null);
    final prev = Map<String, dynamic>.from(_transactions[idx]);
    final cleaned = _cleanTx({
      'id': id,
      'type': type,
      'amount': amount,
      'title': title,
      'category': category,
      'wallet': wallet,
      'to_wallet': toWallet,
      'date': day ?? prev['date'],
      'created': prev['created'],
      'source': prev['source'],
    });
    if (cleaned == null) return (null, null);
    _transactions[idx] = cleaned;
    await save();
    return (Map<String, dynamic>.from(cleaned), prev);
  }

  Future<Map<String, dynamic>?> deleteTransaction(String id) async {
    final idx = _transactions.indexWhere((t) => t['id'] == id);
    if (idx < 0) return null;
    final removed = Map<String, dynamic>.from(_transactions.removeAt(idx));
    await save();
    return removed;
  }

  Future<Map<String, dynamic>?> restoreTransaction(Map<String, dynamic> payload) async {
    final cleaned = _cleanTx(payload);
    if (cleaned == null) return null;
    final idx = _transactions.indexWhere((t) => t['id'] == cleaned['id']);
    if (idx >= 0) {
      _transactions[idx] = cleaned;
    } else {
      _transactions.add(cleaned);
    }
    await save();
    return Map<String, dynamic>.from(cleaned);
  }

  Map<String, int> walletBalances() {
    final bal = {for (final w in kFinanceWallets) w: 0};
    for (final t in _transactions) {
      final amt = (t['amount'] as int?) ?? 0;
      final w = t['wallet'] as String? ?? 'Tunai';
      final tp = t['type'] as String? ?? 'expense';
      if (tp == 'income') {
        bal[w] = (bal[w] ?? 0) + amt;
      } else if (tp == 'expense') {
        bal[w] = (bal[w] ?? 0) - amt;
      } else if (tp == 'transfer') {
        final tw = t['to_wallet'] as String? ?? '';
        bal[w] = (bal[w] ?? 0) - amt;
        if (bal.containsKey(tw)) {
          bal[tw] = (bal[tw] ?? 0) + amt;
        }
      }
    }
    final total = bal.values.fold<int>(0, (a, b) => a + b);
    return {...bal, 'Total': total};
  }

  List<Map<String, dynamic>> monthTransactions(int year, int month) {
    final prefix = '${year.toString().padLeft(4, '0')}-${month.toString().padLeft(2, '0')}-';
    final list = _transactions
        .where((t) => (t['date'] as String? ?? '').startsWith(prefix))
        .map((t) => Map<String, dynamic>.from(t))
        .toList();
    list.sort((a, b) {
      final dc = (b['date'] as String).compareTo(a['date'] as String);
      if (dc != 0) return dc;
      return (b['created'] as String).compareTo(a['created'] as String);
    });
    return list;
  }

  Map<String, dynamic> monthSummary(int year, int month) {
    final items = monthTransactions(year, month);
    var inc = 0;
    var exp = 0;
    var tod = 0;
    final todayS = todayIso();
    final byCat = {for (final c in kFinanceCategories) c: 0};
    for (final t in items) {
      final amt = (t['amount'] as int?) ?? 0;
      if (t['type'] == 'income') {
        inc += amt;
      } else if (t['type'] == 'expense') {
        exp += amt;
        final cat = t['category'] as String? ?? 'Jajan';
        if (byCat.containsKey(cat)) byCat[cat] = byCat[cat]! + amt;
        if (t['date'] == todayS) tod += amt;
      }
    }
    return {
      'income': inc,
      'expense': exp,
      'net': inc - exp,
      'today_expense': tod,
      'count': items.length,
      'by_category': byCat,
    };
  }

  // Budgets
  Map<String, int> budgets() => Map<String, int>.from(_budgets);

  Future<bool> setBudget(String category, Object? amount) async {
    final cat = normalizeFinanceCategory(category);
    if (amount == null || amount.toString().trim().isEmpty) {
      _budgets.remove(cat);
      await save();
      return true;
    }
    final amt = parseRupiah(amount);
    if (amt == null) return false;
    _budgets[cat] = amt;
    await save();
    return true;
  }

  // Shortcuts
  List<Map<String, dynamic>> shortcuts() => _shortcuts.map((s) => Map<String, dynamic>.from(s)).toList();

  Future<Map<String, dynamic>?> addShortcut(String name, String category, Object? amount) async {
    final clean = name.trim();
    if (clean.isEmpty) return null;
    final hasAmt = amount != null && amount.toString().trim().isNotEmpty;
    final amt = hasAmt ? parseRupiah(amount) : null;
    if (hasAmt && amt == null) return null;
    final entry = <String, dynamic>{
      'name': clean.length > 40 ? clean.substring(0, 40) : clean,
      'category': normalizeFinanceCategory(category),
      'amount': amt,
    };
    final idx = _shortcuts.indexWhere((s) => (s['name'] as String).toLowerCase() == clean.toLowerCase());
    if (idx >= 0) {
      _shortcuts[idx] = entry;
    } else {
      _shortcuts.add(entry);
    }
    await save();
    return Map<String, dynamic>.from(entry);
  }

  Future<bool> deleteShortcut(String name) async {
    final before = _shortcuts.length;
    _shortcuts.removeWhere((s) => (s['name'] as String).toLowerCase() == name.trim().toLowerCase());
    if (_shortcuts.length != before) {
      await save();
      return true;
    }
    return false;
  }

  // Bills
  List<Map<String, dynamic>> bills() {
    final list = _bills
        .map((b) => {...b, 'paid_months': List<String>.from(b['paid_months'] as List? ?? const [])})
        .toList();
    list.sort((a, b) {
      final dc = (a['day'] as int).compareTo(b['day'] as int);
      return dc != 0 ? dc : (a['name'] as String).toLowerCase().compareTo((b['name'] as String).toLowerCase());
    });
    return list;
  }

  static bool isBillPaid(Map<String, dynamic> bill, int year, int month) {
    final ym = '${year.toString().padLeft(4, '0')}-${month.toString().padLeft(2, '0')}';
    final paid = bill['paid_months'] as List? ?? const [];
    return paid.contains(ym);
  }

  Future<Map<String, dynamic>?> addBill({
    required String name,
    required int day,
    required Object amount,
    String category = 'Mingguan&Bulanan',
    String wallet = 'BNI',
  }) async {
    final clean = name.trim();
    final amt = parseRupiah(amount);
    if (clean.isEmpty || amt == null) return null;
    final bill = <String, dynamic>{
      'id': _newId(8),
      'name': clean.length > 60 ? clean.substring(0, 60) : clean,
      'day': day.clamp(1, 31),
      'amount': amt,
      'category': normalizeFinanceCategory(category),
      'wallet': normalizeFinanceWallet(wallet),
      'paid_months': <String>[],
    };
    _bills.add(bill);
    await save();
    return Map<String, dynamic>.from(bill);
  }

  Future<bool> deleteBill(String id) async {
    final before = _bills.length;
    _bills.removeWhere((b) => b['id'] == id);
    if (_bills.length != before) {
      await save();
      return true;
    }
    return false;
  }

  Future<Map<String, dynamic>?> markBillPaid(String id, int year, int month) async {
    final ym = '${year.toString().padLeft(4, '0')}-${month.toString().padLeft(2, '0')}';
    for (final b in _bills) {
      if (b['id'] == id) {
        final paid = List<String>.from(b['paid_months'] as List? ?? const []);
        if (!paid.contains(ym)) {
          paid.add(ym);
          paid.sort();
          b['paid_months'] = paid.length > 24 ? paid.sublist(paid.length - 24) : paid;
          await save();
        }
        return Map<String, dynamic>.from(b);
      }
    }
    return null;
  }

  Future<(Map<String, dynamic>?, Map<String, dynamic>?)> payBill(String id, int year, int month) async {
    final bIdx = _bills.indexWhere((b) => b['id'] == id);
    if (bIdx < 0) return (null, null);
    final bill = _bills[bIdx];
    final now = DateTime.now();
    final String payDay;
    if (now.year == year && now.month == month) {
      payDay = todayIso(now);
    } else {
      final lastD = DateTime(year, month + 1, 0).day;
      final d = min(bill['day'] as int, lastD);
      payDay = '${year.toString().padLeft(4, '0')}-${month.toString().padLeft(2, '0')}-${d.toString().padLeft(2, '0')}';
    }
    final tx = await addTransaction(
      type: 'expense',
      amount: bill['amount'],
      title: bill['name'] as String,
      category: bill['category'] as String,
      wallet: bill['wallet'] as String,
      day: payDay,
      source: 'bill',
    );
    if (tx == null) return (null, null);
    final updated = await markBillPaid(id, year, month);
    return (updated, tx);
  }
}

// ---------------------------------------------------------------------------
// 2. TASK STORE
// ---------------------------------------------------------------------------
class TaskStore {
  final File file;
  List<Map<String, dynamic>> _items = [];

  TaskStore(this.file);

  Future<void> load() async {
    _items = [];
    final raw = await readJsonSafe(file);
    final list = raw is Map ? raw['items'] : raw;
    if (list is List) {
      _items = list
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e))
          .where((e) => (e['id'] ?? '').toString().isNotEmpty)
          .toList();
    }
  }

  Future<bool> save() => writeJsonAtomic(file, {'version': 1, 'items': _items});

  List<Map<String, dynamic>> all() => _items.map((e) => Map<String, dynamic>.from(e)).toList();

  List<Map<String, dynamic>> pending() => all().where((t) => t['done'] != true).toList();

  Map<String, dynamic>? get(String id) {
    for (final t in _items) {
      if (t['id'] == id) return Map<String, dynamic>.from(t);
    }
    return null;
  }

  static String statOf(Map<String, dynamic> task) {
    final s = (task['stat'] ?? '').toString().trim().toLowerCase();
    return kSocialStats.containsKey(s) ? s : 'proficiency';
  }

  Future<Map<String, dynamic>?> add(
    String title, {
    String priority = 'normal',
    DateTime? deadline,
    String stat = 'proficiency',
  }) async {
    final clean = title.trim();
    if (clean.isEmpty) return null;
    final ts = nowIso();
    final st = kSocialStats.containsKey(stat.toLowerCase()) ? stat.toLowerCase() : 'proficiency';
    final task = <String, dynamic>{
      'id': _newId(12),
      'title': clean,
      'done': false,
      'priority': priority == 'high' ? 'high' : 'normal',
      'stat': st,
      'deadline': deadline?.toIso8601String().substring(0, 16),
      'rewarded': false,
      'created': ts,
      'done_at': null,
      'updated_at': ts,
    };
    _items.add(task);
    await save();
    return Map<String, dynamic>.from(task);
  }

  Future<bool> setDone(String id, bool done) async {
    final idx = _items.indexWhere((t) => t['id'] == id);
    if (idx < 0) return false;
    final ts = nowIso();
    _items[idx]['done'] = done;
    _items[idx]['done_at'] = done ? ts : null;
    _items[idx]['updated_at'] = ts;
    await save();
    return true;
  }

  Future<bool> markRewarded(String id) async {
    final idx = _items.indexWhere((t) => t['id'] == id);
    if (idx < 0 || _items[idx]['rewarded'] == true) return false;
    _items[idx]['rewarded'] = true;
    await save();
    return true;
  }

  Future<bool> rename(String id, String title) async {
    final clean = title.trim();
    if (clean.isEmpty) return false;
    final idx = _items.indexWhere((t) => t['id'] == id);
    if (idx < 0) return false;
    _items[idx]['title'] = clean;
    _items[idx]['updated_at'] = nowIso();
    await save();
    return true;
  }

  Future<bool> setPriority(String id, String priority) async {
    final idx = _items.indexWhere((t) => t['id'] == id);
    if (idx < 0) return false;
    _items[idx]['priority'] = priority == 'high' ? 'high' : 'normal';
    _items[idx]['updated_at'] = nowIso();
    await save();
    return true;
  }

  Future<bool> setStat(String id, String stat) async {
    if (!kSocialStats.containsKey(stat)) return false;
    final idx = _items.indexWhere((t) => t['id'] == id);
    if (idx < 0) return false;
    _items[idx]['stat'] = stat;
    _items[idx]['updated_at'] = nowIso();
    await save();
    return true;
  }

  Future<bool> setDeadline(String id, DateTime? dl) async {
    final idx = _items.indexWhere((t) => t['id'] == id);
    if (idx < 0) return false;
    _items[idx]['deadline'] = dl?.toIso8601String().substring(0, 16);
    _items[idx]['updated_at'] = nowIso();
    await save();
    return true;
  }

  Future<Map<String, dynamic>?> delete(String id) async {
    final idx = _items.indexWhere((t) => t['id'] == id);
    if (idx < 0) return null;
    final removed = Map<String, dynamic>.from(_items.removeAt(idx));
    await save();
    return removed;
  }

  Future<List<Map<String, dynamic>>> clearDone() async {
    final removed = _items.where((t) => t['done'] == true).map((e) => Map<String, dynamic>.from(e)).toList();
    if (removed.isEmpty) return [];
    _items.removeWhere((t) => t['done'] == true);
    await save();
    return removed;
  }

  Future<void> restore(List<Map<String, dynamic>> items) async {
    final ids = _items.map((e) => e['id']).toSet();
    for (final it in items) {
      if (!ids.contains(it['id'])) _items.add(Map<String, dynamic>.from(it));
    }
    await save();
  }
}

// ---------------------------------------------------------------------------
// 3. NOTE STORE (with automatic #tag extraction)
// ---------------------------------------------------------------------------
final RegExp _hashTagRe = RegExp(r'(?<![\w/#])#([A-Za-z][A-Za-z0-9_-]{0,23})');

class NoteStore {
  final File file;
  List<Map<String, dynamic>> _items = [];

  NoteStore(this.file);

  Future<void> load() async {
    _items = [];
    final raw = await readJsonSafe(file);
    final list = raw is Map ? raw['items'] : raw;
    if (list is List) {
      _items = list
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e))
          .where((e) => (e['id'] ?? '').toString().isNotEmpty)
          .toList();
    }
  }

  Future<bool> save() => writeJsonAtomic(file, {'version': 1, 'items': _items});

  static List<String> extractTags(String title, String content) {
    final seen = <String>{};
    final out = <String>[];
    for (final m in _hashTagRe.allMatches('$title\n$content')) {
      final tag = m.group(1)!.toLowerCase();
      if (seen.add(tag)) out.add(tag);
    }
    return out;
  }

  List<Map<String, dynamic>> all() => _items.map((e) => Map<String, dynamic>.from(e)).toList();

  List<String> allTags() {
    final counts = <String, int>{};
    for (final n in _items) {
      if (n['archived'] == true) continue;
      for (final t in noteTags(n)) {
        counts[t] = (counts[t] ?? 0) + 1;
      }
    }
    final list = counts.keys.toList()..sort((a, b) => counts[b]!.compareTo(counts[a]!));
    return list;
  }

  static List<String> noteTags(Map<String, dynamic> n) =>
      extractTags((n['title'] ?? '').toString(), (n['content'] ?? '').toString());

  Map<String, dynamic>? get(String id) {
    for (final n in _items) {
      if (n['id'] == id) return Map<String, dynamic>.from(n);
    }
    return null;
  }

  Future<Map<String, dynamic>> add(String title, String content, {String category = 'Personal'}) async {
    final ts = nowIso();
    final t = title.trim();
    final c = content.trim();
    final note = <String, dynamic>{
      'id': _newId(12),
      'title': t.isEmpty ? 'Untitled' : t,
      'content': c,
      'category': kNoteCategories.contains(category) ? category : 'Personal',
      'tags': extractTags(t, c),
      'pinned': false,
      'archived': false,
      'created': ts,
      'updated': ts,
    };
    _items.add(note);
    await save();
    return Map<String, dynamic>.from(note);
  }

  Future<Map<String, dynamic>?> update(String id, String title, String content, {String? category}) async {
    final idx = _items.indexWhere((n) => n['id'] == id);
    if (idx < 0) return null;
    final t = title.trim();
    final c = content.trim();
    _items[idx]['title'] = t.isEmpty ? 'Untitled' : t;
    _items[idx]['content'] = c;
    if (category != null && kNoteCategories.contains(category)) {
      _items[idx]['category'] = category;
    }
    _items[idx]['tags'] = extractTags(t, c);
    _items[idx]['updated'] = nowIso();
    await save();
    return Map<String, dynamic>.from(_items[idx]);
  }

  Future<void> setPinned(String id, bool pinned) async {
    final idx = _items.indexWhere((n) => n['id'] == id);
    if (idx >= 0) {
      _items[idx]['pinned'] = pinned;
      await save();
    }
  }

  Future<void> setArchived(String id, bool archived) async {
    final idx = _items.indexWhere((n) => n['id'] == id);
    if (idx >= 0) {
      _items[idx]['archived'] = archived;
      await save();
    }
  }

  Future<Map<String, dynamic>?> delete(String id) async {
    final idx = _items.indexWhere((n) => n['id'] == id);
    if (idx < 0) return null;
    final removed = Map<String, dynamic>.from(_items.removeAt(idx));
    await save();
    return removed;
  }

  Future<Map<String, dynamic>> restore(Map<String, dynamic> note) async {
    final copy = Map<String, dynamic>.from(note);
    _items.add(copy);
    await save();
    return copy;
  }
}

// ---------------------------------------------------------------------------
// 4. LOCAL EVENT STORE
// ---------------------------------------------------------------------------
class LocalEventStore {
  final File file;
  List<Map<String, dynamic>> _items = [];

  LocalEventStore(this.file);

  Future<void> load() async {
    _items = [];
    final raw = await readJsonSafe(file);
    final list = raw is Map ? raw['items'] : raw;
    if (list is List) {
      _items = list
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e))
          .where((e) => (e['id'] ?? '').toString().isNotEmpty)
          .toList();
    }
  }

  Future<bool> save() => writeJsonAtomic(file, {'version': 1, 'items': _items});

  List<Map<String, dynamic>> all() => _items.map((e) => Map<String, dynamic>.from(e)).toList();

  Future<Map<String, dynamic>?> add(
    String title,
    DateTime start, {
    DateTime? end,
    bool allDay = false,
    String location = '',
  }) async {
    final clean = title.trim();
    if (clean.isEmpty) return null;
    final ev = <String, dynamic>{
      'id': _newId(10),
      'title': clean,
      'start': start.toIso8601String().substring(0, 16),
      'end': allDay ? null : (end ?? start.add(const Duration(hours: 1))).toIso8601String().substring(0, 16),
      'all_day': allDay,
      'location': location.trim(),
      'created': nowIso(),
    };
    _items.add(ev);
    await save();
    return Map<String, dynamic>.from(ev);
  }

  Future<Map<String, dynamic>?> update(
    String id,
    String title,
    DateTime start, {
    DateTime? end,
    bool allDay = false,
    String location = '',
  }) async {
    final clean = title.trim();
    final idx = _items.indexWhere((e) => e['id'] == id);
    if (clean.isEmpty || idx < 0) return null;
    _items[idx]['title'] = clean;
    _items[idx]['start'] = start.toIso8601String().substring(0, 16);
    _items[idx]['end'] = allDay ? null : (end ?? start.add(const Duration(hours: 1))).toIso8601String().substring(0, 16);
    _items[idx]['all_day'] = allDay;
    _items[idx]['location'] = location.trim();
    await save();
    return Map<String, dynamic>.from(_items[idx]);
  }

  Future<Map<String, dynamic>?> delete(String id) async {
    final idx = _items.indexWhere((e) => e['id'] == id);
    if (idx < 0) return null;
    final removed = Map<String, dynamic>.from(_items.removeAt(idx));
    await save();
    return removed;
  }

  Future<void> restore(Map<String, dynamic> ev) async {
    _items.add(Map<String, dynamic>.from(ev));
    await save();
  }
}

// ---------------------------------------------------------------------------
// 5. SOCIAL STATS STORE & 6. HABIT STORE
// ---------------------------------------------------------------------------
class SocialStatsStore {
  final File file;
  List<Map<String, dynamic>> _items = [];

  SocialStatsStore(this.file);

  Future<void> load() async {
    _items = [];
    final raw = await readJsonSafe(file);
    final list = raw is Map ? raw['items'] : raw;
    if (list is List) {
      _items = list
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e))
          .where((e) => (e['id'] ?? '').toString().isNotEmpty)
          .toList();
    }
  }

  Future<bool> save() => writeJsonAtomic(file, {'version': 1, 'items': _items});

  Map<String, int> totals() {
    final out = {for (final k in kSocialStatKeys) k: 0};
    for (final e in _items) {
      final k = e['stat'] as String?;
      if (k != null && out.containsKey(k)) {
        out[k] = out[k]! + ((e['points'] as num?)?.toInt() ?? 0);
      }
    }
    return out;
  }

  int totalFor(String stat) => totals()[stat] ?? 0;

  List<Map<String, dynamic>> todayEntries() {
    final tod = todayIso();
    return _items.where((e) => e['date'] == tod).map((e) => Map<String, dynamic>.from(e)).toList();
  }

  int todayPoints() => todayEntries().fold<int>(0, (s, e) => s + ((e['points'] as num?)?.toInt() ?? 0));

  int streakDays() {
    final dates = _items.map((e) => (e['date'] ?? '').toString()).toSet();
    if (dates.isEmpty) return 0;
    var cur = DateTime.now();
    if (!dates.contains(todayIso(cur))) {
      cur = cur.subtract(const Duration(days: 1));
      if (!dates.contains(todayIso(cur))) return 0;
    }
    var count = 0;
    while (dates.contains(todayIso(cur))) {
      count++;
      cur = cur.subtract(const Duration(days: 1));
    }
    return count;
  }

  List<Map<String, dynamic>> recent({int limit = 60, String statFilter = 'all'}) {
    final list = _items
        .where((e) => statFilter == 'all' || e['stat'] == statFilter)
        .map((e) => Map<String, dynamic>.from(e))
        .toList();
    list.sort((a, b) => (b['created'] ?? '').toString().compareTo((a['created'] ?? '').toString()));
    return list.length > limit ? list.sublist(0, limit) : list;
  }

  Future<(Map<String, dynamic>?, int, int)> add(
    String stat,
    String activity, {
    int points = kDefaultStatPoints,
    String? block,
    String source = 'manual',
    String refId = '',
  }) async {
    if (!kSocialStats.containsKey(stat)) return (null, 1, 1);
    final pts = points.clamp(1, 500);
    final oldLv = statLevel(totalFor(stat));
    final text = activity.trim().isEmpty ? '${kSocialStats[stat]!.label} block' : activity.trim();
    final entry = <String, dynamic>{
      'id': _newId(12),
      'stat': stat,
      'activity': text,
      'points': pts,
      'date': todayIso(),
      'block': (block != null && kTimeBlocks.contains(block)) ? block : defaultTimeBlock(),
      'source': source,
      'ref_id': refId,
      'created': nowIso(),
    };
    _items.add(entry);
    await save();
    final newLv = statLevel(totalFor(stat));
    return (Map<String, dynamic>.from(entry), oldLv, newLv);
  }

  Future<(Map<String, dynamic>?, Map<String, dynamic>?)> update(
    String id, {
    required String stat,
    required String activity,
    required int points,
    required String block,
  }) async {
    final idx = _items.indexWhere((e) => e['id'] == id);
    if (idx < 0 || !kSocialStats.containsKey(stat) || activity.trim().isEmpty) return (null, null);
    final prev = Map<String, dynamic>.from(_items[idx]);
    _items[idx]['stat'] = stat;
    _items[idx]['activity'] = activity.trim();
    _items[idx]['points'] = points.clamp(1, 500);
    _items[idx]['block'] = kTimeBlocks.contains(block) ? block : defaultTimeBlock();
    await save();
    return (Map<String, dynamic>.from(_items[idx]), prev);
  }

  Future<Map<String, dynamic>?> delete(String id) async {
    final idx = _items.indexWhere((e) => e['id'] == id);
    if (idx < 0) return null;
    final removed = Map<String, dynamic>.from(_items.removeAt(idx));
    await save();
    return removed;
  }

  Future<void> deleteByRef(String refId, String dateStr) async {
    _items.removeWhere((e) => e['ref_id'] == refId && e['date'] == dateStr);
    await save();
  }

  Future<void> restore(Map<String, dynamic> entry) async {
    final idx = _items.indexWhere((e) => e['id'] == entry['id']);
    if (idx >= 0) {
      _items[idx] = Map<String, dynamic>.from(entry);
    } else {
      _items.add(Map<String, dynamic>.from(entry));
    }
    await save();
  }
}

class HabitStore {
  final File file;
  List<Map<String, dynamic>> _items = [];

  HabitStore(this.file);

  Future<void> load() async {
    _items = [];
    final raw = await readJsonSafe(file);
    final list = raw is Map ? raw['items'] : raw;
    if (list is List) {
      _items = list
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e))
          .where((e) => (e['id'] ?? '').toString().isNotEmpty)
          .toList();
    }
  }

  Future<bool> save() => writeJsonAtomic(file, {'version': 1, 'items': _items});

  List<Map<String, dynamic>> all() => _items.map((e) => Map<String, dynamic>.from(e)).toList();

  static bool isDoneOn(Map<String, dynamic> habit, [String? dayIso]) {
    final target = dayIso ?? todayIso();
    final dates = habit['dates'] as List? ?? const [];
    return dates.contains(target);
  }

  static int streak(Map<String, dynamic> habit) {
    final dates = (habit['dates'] as List? ?? const []).map((e) => e.toString()).toSet();
    if (dates.isEmpty) return 0;
    var cur = DateTime.now();
    if (!dates.contains(todayIso(cur))) {
      cur = cur.subtract(const Duration(days: 1));
      if (!dates.contains(todayIso(cur))) return 0;
    }
    var count = 0;
    while (dates.contains(todayIso(cur))) {
      count++;
      cur = cur.subtract(const Duration(days: 1));
    }
    return count;
  }

  ({int done, int total}) todaySummary() {
    final tod = todayIso();
    final done = _items.where((h) => isDoneOn(h, tod)).length;
    return (done: done, total: _items.length);
  }

  Future<Map<String, dynamic>?> add(String title, {String stat = 'knowledge', int points = 2}) async {
    final clean = title.trim();
    if (clean.isEmpty) return null;
    final st = kSocialStats.containsKey(stat) ? stat : 'knowledge';
    final h = <String, dynamic>{
      'id': _newId(10),
      'title': clean,
      'stat': st,
      'points': points.clamp(1, 5),
      'dates': <String>[],
      'created': nowIso(),
    };
    _items.add(h);
    await save();
    return Map<String, dynamic>.from(h);
  }

  Future<(bool, Map<String, dynamic>?, int, int)> toggleToday(String id, SocialStatsStore stats) async {
    final idx = _items.indexWhere((h) => h['id'] == id);
    if (idx < 0) return (false, null, 1, 1);
    final h = _items[idx];
    final tod = todayIso();
    final dates = List<String>.from(h['dates'] as List? ?? const []);
    if (dates.contains(tod)) {
      dates.remove(tod);
      h['dates'] = dates;
      await save();
      await stats.deleteByRef('habit:$id', tod);
      return (false, null, 1, 1);
    }
    dates.add(tod);
    h['dates'] = dates.length > 90 ? dates.sublist(dates.length - 90) : dates;
    await save();
    final (entry, oldLv, newLv) = await stats.add(
      h['stat'] as String? ?? 'knowledge',
      h['title'] as String? ?? 'Daily Habit',
      points: (h['points'] as num?)?.toInt() ?? 2,
      source: 'habit',
      refId: 'habit:$id',
    );
    return (true, entry, oldLv, newLv);
  }

  Future<Map<String, dynamic>?> delete(String id) async {
    final idx = _items.indexWhere((h) => h['id'] == id);
    if (idx < 0) return null;
    final removed = Map<String, dynamic>.from(_items.removeAt(idx));
    await save();
    return removed;
  }

  Future<void> restore(Map<String, dynamic> h) async {
    _items.add(Map<String, dynamic>.from(h));
    await save();
  }
}

// ---------------------------------------------------------------------------
// 7. SETTINGS STORE
// ---------------------------------------------------------------------------
Map<String, dynamic> defaultSettings() => {
      'bedtime': '23:00',
      'wake': '06:00',
      'curfew_enabled': true,
      'focus_min': 25,
      'rest_min': 5,
      'focus_stat': 'knowledge',
      'sound_enabled': true,
      'voice_volume': 80,
      'sfx_volume': 75,
      'auto_stat_rewards': true,
      'floating_bubble_enabled': false,
      'manual_dnd': false,
      'sleep_mode': false,
      'enabled_tabs': List<String>.from(kRadialTabKeys),
      'ai_enabled': false,
      'ai_provider': 'gemini',
      'ai_endpoint': 'http://localhost:11434/v1/chat/completions',
      'ai_model': 'gemini-3.5-flash-lite',
      'ai_api_key': '',
      'ai_call_user': 'Joker',
      'finance_sheets_enabled': false,
      'sheets_sync_id': '',
      'sheets_key_name': '',
      'sheets_key_json': '',
    };

class SettingsStore {
  final File file;
  Map<String, dynamic> data = defaultSettings();

  SettingsStore(this.file);

  Future<void> load() async {
    data = defaultSettings();
    final raw = await readJsonSafe(file);
    if (raw is Map) {
      raw.forEach((k, v) {
        if (data.containsKey(k)) data[k.toString()] = v;
      });
    }
    final tabs = data['enabled_tabs'];
    if (tabs is! List || tabs.isEmpty) {
      data['enabled_tabs'] = List<String>.from(kRadialTabKeys);
    } else {
      final clean = tabs.map((e) => e.toString()).where((k) => kRadialTabKeys.contains(k)).toList();
      data['enabled_tabs'] = clean.isEmpty ? List<String>.from(kRadialTabKeys) : clean;
    }
  }

  Future<bool> save() => writeJsonAtomic(file, data);

  dynamic operator [](String key) => data[key];

  Future<void> updateAll(Map<String, dynamic> patch) async {
    patch.forEach((k, v) {
      if (data.containsKey(k)) data[k] = v;
    });
    await save();
  }

  bool get sheetsCredentialsReady =>
      (data['sheets_sync_id'] ?? '').toString().trim().isNotEmpty &&
      (data['sheets_key_json'] ?? '').toString().trim().isNotEmpty;

  bool get financeSheetsActive => data['finance_sheets_enabled'] == true && sheetsCredentialsReady;

  List<String> get enabledTabs => List<String>.from(data['enabled_tabs'] as List? ?? kRadialTabKeys);
}
