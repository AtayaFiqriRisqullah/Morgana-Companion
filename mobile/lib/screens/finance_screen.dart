import 'dart:async';

// ignore: deprecated_member_use
import 'package:flutter/material.dart';

import '../core/app_state.dart';
import '../core/rupiah.dart';
import '../core/sheets_sync.dart';
import '../core/storage.dart';
import '../theme/p5_theme.dart';
import '../widgets/p5_widgets.dart';

/// Full v1.5.3 Finance Ledger on Android:
/// - Local OR Live Google Sheets (bot-wa Monthly_Budget) sync
/// - Interactive pulsing KPI cards
/// - Semantic Expense/Income/Transfer colors, 1-tap +5rb..+100rb accumulator, dynamic CTA, live wallet chips
/// - Searchable/filterable transaction history with filtered subtotal, inline Edit/Duplicate/Delete, Undo
/// - Budgets & Bills page with 80%/100% progress warnings, wallet breakdown, bills & shortcuts manager
class FinanceScreen extends StatefulWidget {
  final MorganaAppState app;

  const FinanceScreen({super.key, required this.app});

  @override
  State<FinanceScreen> createState() => _FinanceScreenState();
}

class _FinanceScreenState extends State<FinanceScreen> {
  static const List<String> _monthShort = [
    'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
    'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC',
  ];

  String _activePage = 'tx'; // 'tx' | 'plan'
  String _txType = 'expense'; // 'expense' | 'income' | 'transfer'
  String _selectedCat = 'Jajan';
  String _selectedWallet = 'Tunai';
  String _selectedToWallet = 'BNI';
  DateTime _selectedDate = DateTime.now();
  String _txFilter = 'all';
  String _activeShortcutName = '';

  String? _editingTxId;
  Map<String, dynamic>? _editingSheetTx;

  // Undo & nudge timers
  Map<String, dynamic>? _undoPayload;
  String _undoLabel = '';
  Timer? _undoTimer;
  Timer? _nudgeTimer;

  // Nudge highlight states
  bool _amtNudged = false;
  bool _titleNudged = false;
  bool _billNameNudged = false;
  bool _billAmtNudged = false;
  bool _scNameNudged = false;

  final TextEditingController _amtCtrl = TextEditingController();
  final TextEditingController _titleCtrl = TextEditingController();
  final TextEditingController _searchCtrl = TextEditingController();

  // Page 2 controllers
  final Map<String, TextEditingController> _budgetCtrls = {
    for (final c in kFinanceCategories) c: TextEditingController(),
  };
  final TextEditingController _billNameCtrl = TextEditingController();
  final TextEditingController _billAmtCtrl = TextEditingController();
  int _billDay = 1;
  String _billCat = 'Mingguan&Bulanan';
  String _billWal = 'BNI';
  final TextEditingController _scNameCtrl = TextEditingController();
  final TextEditingController _scAmtCtrl = TextEditingController();
  String _scCat = 'Jajan';

  @override
  void initState() {
    super.initState();
    _syncBudgetInputs();
  }

  @override
  void dispose() {
    _undoTimer?.cancel();
    _nudgeTimer?.cancel();
    _amtCtrl.dispose();
    _titleCtrl.dispose();
    _searchCtrl.dispose();
    for (final c in _budgetCtrls.values) {
      c.dispose();
    }
    _billNameCtrl.dispose();
    _billAmtCtrl.dispose();
    _scNameCtrl.dispose();
    _scAmtCtrl.dispose();
    super.dispose();
  }

  void _syncBudgetInputs() {
    final buds = widget.app.finance.budgets();
    for (final c in kFinanceCategories) {
      final lim = buds[c];
      _budgetCtrls[c]!.text = lim != null ? '$lim' : '';
    }
  }

  void _triggerNudge(void Function(bool) setter, String msg) {
    widget.app.audio.playSfx('cancel');
    widget.app.notify(msg, tone: P5ToastTone.warn);
    setState(() => setter(true));
    _nudgeTimer?.cancel();
    _nudgeTimer = Timer(const Duration(milliseconds: 450), () {
      if (mounted) setState(() => setter(false));
    });
  }

  void _triggerUndo(String label, Map<String, dynamic> payload) {
    _undoTimer?.cancel();
    setState(() {
      _undoLabel = label;
      _undoPayload = payload;
    });
    _undoTimer = Timer(const Duration(seconds: 6), () {
      if (mounted) setState(() => _undoPayload = null);
    });
  }

  void _bumpAmount(int delta) {
    final cur = parseRupiah(_amtCtrl.text) ?? 0;
    final nxt = cur + delta;
    setState(() => _amtCtrl.text = '$nxt');
  }

  void _selectTxType(String kind) {
    setState(() {
      _txType = kind;
      if (kind == 'transfer' && _selectedToWallet == _selectedWallet) {
        _selectedToWallet = kFinanceWallets.firstWhere((w) => w != _selectedWallet, orElse: () => 'BNI');
      }
    });
  }

  void _applyShortcut(Map<String, dynamic> sc) {
    if (_editingTxId != null) _cancelEdit();
    setState(() {
      _txType = 'expense';
      _selectedCat = sc['category'] as String? ?? 'Jajan';
      _activeShortcutName = sc['name'] as String? ?? '';
      _titleCtrl.text = _activeShortcutName;
      final amt = sc['amount'] as int?;
      if (amt != null) {
        _amtCtrl.text = '$amt';
      } else {
        _amtCtrl.clear();
      }
    });
    final amt = sc['amount'] as int?;
    widget.app.notify(
      amt != null
          ? "Loaded '$_activeShortcutName' · ${formatRupiah(amt)} — tap Record!"
          : "Loaded '$_activeShortcutName' — enter amount",
      tone: amt != null ? P5ToastTone.gold : P5ToastTone.info,
      ms: 1900,
    );
  }

  void _onKpiTap(String which) {
    if (which == 'bal') {
      final nxt = _activePage == 'plan' ? 'tx' : 'plan';
      setState(() => _activePage = nxt);
      if (nxt == 'plan') _syncBudgetInputs();
      widget.app.notify(
        nxt == 'plan' ? 'Viewing Wallet Balances & Monthly Budgets' : 'Back to Transactions',
        tone: nxt == 'plan' ? P5ToastTone.gold : P5ToastTone.info,
        ms: 1700,
      );
      return;
    }
    setState(() {
      _activePage = 'tx';
      if (which == 'tod') {
        final now = DateTime.now();
        if (widget.app.financeYear != now.year || widget.app.financeMonth != now.month) {
          widget.app.setFinancePeriod(now.year, now.month);
        }
        _txFilter = _txFilter == 'today' ? 'all' : 'today';
      } else {
        final target = which == 'inc' ? 'income' : 'expense';
        _txFilter = _txFilter == target ? 'all' : target;
      }
    });
    final msg = _txFilter == 'all'
        ? 'Filter cleared: All entries'
        : (_txFilter == 'today'
            ? "Filtering: Today's transactions"
            : 'Filtering: ${_txFilter == "income" ? "Income" : "Expense"} entries');
    widget.app.notify(msg, tone: _txFilter == 'income' ? P5ToastTone.ok : P5ToastTone.info, ms: 1700);
  }

  Future<void> _pickDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _selectedDate,
      firstDate: DateTime(2020),
      lastDate: DateTime(2035),
      builder: (ctx, child) => Theme(
        data: Theme.of(ctx).copyWith(
          colorScheme: const ColorScheme.dark(primary: P5Colors.red, surface: P5Colors.card),
        ),
        child: child!,
      ),
    );
    if (picked != null) setState(() => _selectedDate = picked);
  }

  Future<void> _submitTx() async {
    final app = widget.app;
    if (app.isSheetMode && app.sheetBusy) {
      app.notify('Still syncing with Google Sheets...', tone: P5ToastTone.info, ms: 1400);
      return;
    }
    final amt = parseRupiah(_amtCtrl.text);
    if (amt == null) {
      _triggerNudge((v) => _amtNudged = v, 'Enter a valid amount (e.g. 15rb, 1.5jt, 25000)');
      return;
    }
    if (_txType == 'transfer' && _selectedWallet == _selectedToWallet) {
      app.audio.playSfx('cancel');
      app.notify('Choose two different wallets for a transfer.', tone: P5ToastTone.warn);
      return;
    }
    final title = _titleCtrl.text.trim();
    final dayStr = todayIso(_selectedDate);

    if (app.isSheetMode) {
      if (_txType == 'income') {
        final allowed = kBankWords.contains(_selectedWallet) ? _selectedWallet : null;
        final bad = forbiddenBankWord(title, allowedBank: allowed);
        if (bad != null) {
          _triggerNudge(
            (v) => _titleNudged = v,
            allowed != null
                ? "Cannot include '$bad' in a $allowed deposit note."
                : "Cash income cannot include '$bad' (protects Sheet formulas).",
          );
          return;
        }
      } else if (_txType == 'transfer') {
        final bad = forbiddenBankWord(title);
        if (bad != null) {
          _triggerNudge((v) => _titleNudged = v, "Transfer note cannot include '$bad'.");
          return;
        }
      }
      final res = await app.submitSheetTransaction(
        type: _txType,
        amount: amt,
        title: title,
        category: _selectedCat,
        wallet: _selectedWallet,
        toWallet: _selectedToWallet,
        dateStr: dayStr,
        editingOldTx: _editingSheetTx,
      );
      if (res.ok && mounted) {
        _cancelEdit();
        app.setFinancePeriod(_selectedDate.year, _selectedDate.month);
      }
      return;
    }

    if (_editingTxId != null) {
      final (updated, prev) = await app.finance.updateTransaction(
        id: _editingTxId!,
        type: _txType,
        amount: amt,
        title: title,
        category: _selectedCat,
        wallet: _selectedWallet,
        toWallet: _selectedToWallet,
        day: dayStr,
      );
      _cancelEdit();
      if (updated != null && prev != null) {
        app.audio.playSfx('select');
        _triggerUndo("Updated '${updated['title']}'", prev);
        app.notify("Saved edit: ${updated['title']} · ${formatRupiah(updated['amount'])}", tone: P5ToastTone.gold);
        app.setFinancePeriod(_selectedDate.year, _selectedDate.month);
      }
      return;
    }

    final tx = await app.finance.addTransaction(
      type: _txType,
      amount: amt,
      title: title,
      category: _selectedCat,
      wallet: _selectedWallet,
      toWallet: _selectedToWallet,
      day: dayStr,
    );
    if (tx == null) {
      _triggerNudge((v) => _amtNudged = v, 'Could not save transaction. Check inputs.');
      return;
    }
    app.audio.playSfx('select');
    setState(() {
      _activeShortcutName = '';
      _amtCtrl.clear();
      _titleCtrl.clear();
    });
    app.setFinancePeriod(_selectedDate.year, _selectedDate.month);
    if (tx['type'] == 'income') {
      app.notify("+${formatRupiah(tx['amount'])} · ${tx['title']} (${tx['wallet']})", tone: P5ToastTone.ok);
    } else if (tx['type'] == 'transfer') {
      app.notify("Transferred ${formatRupiah(tx['amount'])} · ${tx['wallet']} → ${tx['to_wallet']}",
          tone: P5ToastTone.gold);
    } else {
      app.notify("-${formatRupiah(tx['amount'])} · ${tx['title']} (${tx['category']})", tone: P5ToastTone.ok);
    }
  }

  void _startEdit(Map<String, dynamic> tx) {
    if (tx['sheet_kind'] == 'qris_pool') {
      widget.app.notify('QRIS monthly pool rows update automatically.', tone: P5ToastTone.info);
      return;
    }
    setState(() {
      _activePage = 'tx';
      _editingTxId = tx['id'].toString();
      _editingSheetTx = widget.app.isSheetMode ? Map<String, dynamic>.from(tx) : null;
      _txType = tx['type'] as String? ?? 'expense';
      if (_txType == 'expense' && (tx['category'] ?? '').toString().isNotEmpty) {
        _selectedCat = normalizeFinanceCategory(tx['category'].toString());
      }
      _selectedWallet = normalizeFinanceWallet(tx['wallet']?.toString());
      if (_txType == 'transfer' && (tx['to_wallet'] ?? '').toString().isNotEmpty) {
        _selectedToWallet = normalizeFinanceWallet(tx['to_wallet'].toString());
      }
      _amtCtrl.text = '${tx['amount']}';
      _titleCtrl.text = (tx['edit_note'] ?? tx['title'] ?? '').toString();
      _selectedDate = DateTime.tryParse((tx['date'] ?? '').toString()) ?? DateTime.now();
    });
    widget.app.notify("Editing '${tx['title']}' — tap SAVE EDIT or CANCEL", tone: P5ToastTone.gold, ms: 2000);
  }

  void _duplicateTx(Map<String, dynamic> tx) {
    if (tx['sheet_kind'] == 'qris_pool') return;
    _cancelEdit();
    setState(() {
      _txType = tx['type'] as String? ?? 'expense';
      if (_txType == 'expense') _selectedCat = normalizeFinanceCategory(tx['category']?.toString());
      _selectedWallet = normalizeFinanceWallet(tx['wallet']?.toString());
      if (_txType == 'transfer') _selectedToWallet = normalizeFinanceWallet(tx['to_wallet']?.toString());
      _amtCtrl.text = '${tx['amount']}';
      _titleCtrl.text = (tx['edit_note'] ?? tx['title'] ?? '').toString();
      _selectedDate = DateTime.now();
    });
    widget.app.notify("Duplicated '${tx['title']}' into form!", tone: P5ToastTone.gold);
  }

  void _cancelEdit() {
    setState(() {
      _editingTxId = null;
      _editingSheetTx = null;
      _activeShortcutName = '';
      _amtCtrl.clear();
      _titleCtrl.clear();
      _selectedDate = DateTime.now();
    });
  }

  Future<void> _deleteTx(Map<String, dynamic> tx) async {
    if (widget.app.isSheetMode) {
      await widget.app.deleteSheetTransaction(tx);
      return;
    }
    if (_editingTxId == tx['id']) _cancelEdit();
    final removed = await widget.app.finance.deleteTransaction(tx['id'].toString());
    if (removed != null) {
      widget.app.audio.playSfx('close');
      _triggerUndo("Deleted '${removed['title']}'", removed);
      widget.app.notify("Deleted '${removed['title']}' · Undo available", tone: P5ToastTone.info);
      setState(() {});
    }
  }

  Future<void> _undoLast() async {
    final p = _undoPayload;
    if (p == null) return;
    _undoTimer?.cancel();
    setState(() => _undoPayload = null);
    final restored = await widget.app.finance.restoreTransaction(p);
    if (restored != null) {
      widget.app.audio.playSfx('select');
      widget.app.notify("Restored '${restored['title']}'", tone: P5ToastTone.ok);
      setState(() {});
    }
  }

  @override
  Widget build(BuildContext context) {
    final app = widget.app;
    final data = app.activeFinanceData();
    final bal = data.balances;
    final summ = data.summary;
    final sheetWaiting = app.isSheetMode && app.sheetData == null;

    final (badgeTxt, badgeBg, badgeFg, actionTxt, actionVar) = _sheetBadgeSpec(app);

    return Column(
      children: [
        P5Header(
          title: 'FINANCE',
          onAvatarTap: () => app.navigateTo('home'),
          actions: [
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
              decoration: BoxDecoration(
                color: badgeBg,
                borderRadius: BorderRadius.circular(5),
                border: Border.all(color: badgeFg.withValues(alpha: 0.45)),
              ),
              child: Text(badgeTxt, style: P5Text.display(10, color: badgeFg)),
            ),
            const SizedBox(width: 5),
            P5Button(
              label: actionTxt,
              variant: actionVar,
              height: 28,
              fontSize: 10,
              onTap: app.sheetBusy
                  ? null
                  : () {
                      if (!app.isSheetMode) {
                        app.enableFinanceSheetQuick();
                      } else {
                        app.refreshFinanceSheet(silent: false);
                      }
                    },
            ),
          ],
        ),
        // Month navigation bar
        Container(
          padding: const EdgeInsets.fromLTRB(12, 6, 12, 4),
          color: P5Colors.bgDeep,
          child: Row(
            children: [
              P5Button(label: '◀', height: 28, fontSize: 12, onTap: () => app.shiftFinanceMonth(-1)),
              const SizedBox(width: 6),
              Expanded(
                child: Center(
                  child: Text(
                    '${_monthShort[app.financeMonth - 1]} ${app.financeYear}',
                    style: P5Text.display(15, color: P5Colors.goldFg),
                  ),
                ),
              ),
              const SizedBox(width: 6),
              P5Button(label: '▶', height: 28, fontSize: 12, onTap: () => app.shiftFinanceMonth(1)),
              const SizedBox(width: 6),
              P5Button(
                label: 'TODAY',
                variant: P5BtnVariant.gold,
                height: 28,
                fontSize: 10.5,
                onTap: () {
                  final now = DateTime.now();
                  app.setFinancePeriod(now.year, now.month);
                  app.notify('Jumped to ${_monthShort[now.month - 1]} ${now.year}', tone: P5ToastTone.gold, ms: 1500);
                },
              ),
            ],
          ),
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(12, 6, 12, 84),
            children: [
              // 4 Interactive Pulsing KPI Cards (2x2 on phone)
              Row(
                children: [
                  Expanded(
                    child: P5KpiCard(
                      title: 'TOTAL BALANCE',
                      value: sheetWaiting ? (app.sheetBusy ? '...' : '—') : formatRupiah(bal['Total'] ?? 0),
                      subtitle: sheetWaiting
                          ? (app.sheetError.isNotEmpty ? app.sheetError : 'Tap SYNC to load Sheets')
                          : 'Tunai ${shortRupiah(bal['Tunai'] ?? 0)} · BNI ${shortRupiah(bal['BNI'] ?? 0)}',
                      accent: P5Colors.goldFg,
                      tint: const Color(0xFF241E0F),
                      active: _activePage == 'plan',
                      actionHint: 'WALLETS ▸',
                      onTap: () => _onKpiTap('bal'),
                    ),
                  ),
                  const SizedBox(width: 7),
                  Expanded(
                    child: P5KpiCard(
                      title: 'MONTH INCOME',
                      value: sheetWaiting ? '—' : '+${formatRupiah((summ['income'] as num?) ?? 0)}',
                      subtitle: sheetWaiting
                          ? 'Google Sheets'
                          : 'Net: ${((summ['net'] as num?) ?? 0) >= 0 ? "+" : ""}${formatRupiah((summ['net'] as num?) ?? 0)}',
                      accent: P5Colors.mint,
                      tint: const Color(0xFF11241C),
                      active: _activePage == 'tx' && _txFilter == 'income',
                      actionHint: 'IN ▸',
                      onTap: () => _onKpiTap('inc'),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 7),
              Row(
                children: [
                  Expanded(
                    child: P5KpiCard(
                      title: 'MONTH EXPENSE',
                      value: sheetWaiting ? '—' : '-${formatRupiah((summ['expense'] as num?) ?? 0)}',
                      subtitle: '${summ['count'] ?? 0} entries this month',
                      accent: P5Colors.redFg,
                      tint: const Color(0xFF281116),
                      active: _activePage == 'tx' && _txFilter == 'expense',
                      actionHint: 'OUT ▸',
                      onTap: () => _onKpiTap('exp'),
                    ),
                  ),
                  const SizedBox(width: 7),
                  Expanded(
                    child: P5KpiCard(
                      title: 'TODAY SPEND',
                      value: sheetWaiting ? '—' : formatRupiah((summ['today_expense'] as num?) ?? 0),
                      subtitle: (app.isSheetMode && app.sheetData?.syncedAt.isNotEmpty == true)
                          ? 'Synced ${app.sheetData!.syncedAt}'
                          : 'Spent today',
                      accent: P5Colors.white,
                      tint: const Color(0xFF1F1F2E),
                      active: _activePage == 'tx' && _txFilter == 'today',
                      actionHint: 'TODAY ▸',
                      onTap: () => _onKpiTap('tod'),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 10),

              // Segmented Tabs: TRANSACTIONS | BUDGETS & BILLS
              Row(
                children: [
                  Expanded(
                    child: P5Button(
                      label: 'TRANSACTIONS',
                      active: _activePage == 'tx',
                      height: 34,
                      fontSize: 12,
                      onTap: () => setState(() => _activePage = 'tx'),
                    ),
                  ),
                  const SizedBox(width: 7),
                  Expanded(
                    child: P5Button(
                      label: 'BUDGETS & BILLS',
                      active: _activePage == 'plan',
                      height: 34,
                      fontSize: 12,
                      onTap: () {
                        _syncBudgetInputs();
                        setState(() => _activePage = 'plan');
                      },
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 10),

              if (_activePage == 'tx') ...[
                _buildRecordFormCard(bal),
                const SizedBox(height: 10),
                _buildHistoryCard(data.transactions),
                if (_undoPayload != null) P5UndoBar(message: _undoLabel, onUndo: _undoLast),
              ] else ...[
                _buildPlanPage(bal, summ),
              ],
            ],
          ),
        ),
      ],
    );
  }

  (String, Color, Color, String, P5BtnVariant) _sheetBadgeSpec(MorganaAppState app) {
    if (!app.isSheetMode) {
      return app.settings.sheetsCredentialsReady
          ? ('LOCAL', const Color(0xFF222634), const Color(0xFF9FA6BA), 'USE SHEET', P5BtnVariant.gold)
          : ('LOCAL', const Color(0xFF222634), const Color(0xFF9FA6BA), 'CONNECT', P5BtnVariant.soft);
    }
    if (app.sheetBusy) {
      return ('SYNCING', const Color(0xFF332910), P5Colors.gold, 'SYNC', P5BtnVariant.soft);
    }
    if (app.sheetError.isNotEmpty) {
      return ('SHEET ERR', const Color(0xFF3A1218), P5Colors.redFg, 'RETRY', P5BtnVariant.gold);
    }
    final t = app.sheetData?.tab ?? monthTabName(app.financeMonth);
    final shortT = t.length >= 3 ? t.substring(0, 3).toUpperCase() : t.toUpperCase();
    return ('SHEET · $shortT', const Color(0xFF123322), P5Colors.mint, 'SYNC', P5BtnVariant.soft);
  }

  Widget _buildRecordFormCard(Map<String, int> bal) {
    final isEdit = _editingTxId != null;
    final parsedAmt = parseRupiah(_amtCtrl.text);
    final Color cardBorder = isEdit
        ? P5Colors.gold
        : (_txType == 'income'
            ? P5Colors.mintBorder
            : (_txType == 'transfer' ? P5Colors.goldBorder : P5Colors.border));
    final P5BtnVariant ctaVariant = isEdit
        ? P5BtnVariant.gold
        : (_txType == 'income'
            ? P5BtnVariant.mint
            : (_txType == 'transfer' ? P5BtnVariant.gold : P5BtnVariant.primary));
    final ctaBase = isEdit
        ? 'SAVE EDIT'
        : (_txType == 'income'
            ? 'RECORD INCOME'
            : (_txType == 'transfer' ? 'RECORD TRANSFER' : 'RECORD EXPENSE'));
    final ctaLabel = parsedAmt != null ? '$ctaBase · ${formatRupiah(parsedAmt)}' : ctaBase;
    final shortcuts = widget.app.finance.shortcuts();

    return P5Card(
      borderColor: cardBorder,
      borderWidth: isEdit ? 1.6 : 1.2,
      topColor: isEdit ? const Color(0xFF1E1A10) : null,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  isEdit ? 'EDIT TRANSACTION' : 'RECORD TRANSACTION',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: P5Text.display(14, color: isEdit ? P5Colors.gold : P5Colors.red),
                ),
              ),
              if (_amtCtrl.text.trim().isNotEmpty) ...[
                const SizedBox(width: 8),
                Text(
                  parsedAmt != null ? '= ${formatRupiah(parsedAmt)}' : 'INVALID AMOUNT',
                  style: P5Text.display(13, color: parsedAmt != null ? P5Colors.goldFg : P5Colors.redFg),
                ),
              ],
            ],
          ),
          const SizedBox(height: 8),

          // Semantic Mode Pills: EXPENSE (Red) / INCOME (Mint) / TRANSFER (Gold)
          Row(
            children: [
              Expanded(
                child: P5Button(
                  label: 'EXPENSE',
                  active: _txType == 'expense',
                  activeVariant: P5BtnVariant.primary,
                  height: 32,
                  fontSize: 11.5,
                  onTap: () => _selectTxType('expense'),
                ),
              ),
              const SizedBox(width: 6),
              Expanded(
                child: P5Button(
                  label: 'INCOME',
                  active: _txType == 'income',
                  activeVariant: P5BtnVariant.mint,
                  height: 32,
                  fontSize: 11.5,
                  onTap: () => _selectTxType('income'),
                ),
              ),
              const SizedBox(width: 6),
              Expanded(
                child: P5Button(
                  label: 'TRANSFER',
                  active: _txType == 'transfer',
                  activeVariant: P5BtnVariant.gold,
                  height: 32,
                  fontSize: 11.5,
                  onTap: () => _selectTxType('transfer'),
                ),
              ),
            ],
          ),

          // Quick Shortcuts (/pintasan) in Expense mode
          if (_txType == 'expense' && shortcuts.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text('QUICK SHORTCUTS', style: P5Text.display(10.5, color: P5Colors.muted)),
            const SizedBox(height: 4),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final sc in shortcuts.take(8))
                  P5Button(
                    label: sc['amount'] != null
                        ? "${sc['name']} · ${shortRupiah(sc['amount'] as int)}"
                        : sc['name'].toString(),
                    displayFont: false,
                    height: 28,
                    fontSize: 11.5,
                    active: _activeShortcutName == sc['name'],
                    activeVariant: P5BtnVariant.gold,
                    onTap: () => _applyShortcut(sc),
                  ),
              ],
            ),
          ],
          const SizedBox(height: 8),

          // Amount + Date Picker Row
          Row(
            children: [
              Expanded(
                flex: 3,
                child: P5NudgeBox(
                  nudged: _amtNudged,
                  child: TextField(
                    controller: _amtCtrl,
                    keyboardType: TextInputType.text,
                    style: P5Text.body(14.5, color: P5Colors.white, bold: true),
                    decoration: const InputDecoration(hintText: 'Amount (15rb, 1.5jt, 25000)'),
                    onChanged: (_) => setState(() {}),
                    onSubmitted: (_) => _submitTx(),
                  ),
                ),
              ),
              const SizedBox(width: 7),
              Expanded(
                flex: 2,
                child: P5Button(
                  label:
                      '${_selectedDate.day.toString().padLeft(2, '0')}/${_selectedDate.month.toString().padLeft(2, '0')}/${_selectedDate.year}',
                  icon: Icons.calendar_today,
                  height: 40,
                  fontSize: 11.5,
                  onTap: _pickDate,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),

          // 1-Tap Quick-Amount Accumulator Chips (+5rb..+100rb + CLR)
          Row(
            children: [
              for (final pair in const [
                ('+5rb', 5000),
                ('+10rb', 10000),
                ('+20rb', 20000),
                ('+50rb', 50000),
                ('+100rb', 100000),
              ]) ...[
                Expanded(
                  child: P5Button(
                    label: pair.$1,
                    height: 27,
                    fontSize: 10.5,
                    padding: const EdgeInsets.symmetric(horizontal: 2),
                    onTap: () => _bumpAmount(pair.$2),
                  ),
                ),
                const SizedBox(width: 4),
              ],
              P5Button(
                label: 'CLR',
                variant: P5BtnVariant.ghost,
                height: 27,
                fontSize: 10.5,
                padding: const EdgeInsets.symmetric(horizontal: 6),
                onTap: () => setState(() => _amtCtrl.clear()),
              ),
            ],
          ),
          const SizedBox(height: 8),

          // Note / Title input
          P5NudgeBox(
            nudged: _titleNudged,
            child: TextField(
              controller: _titleCtrl,
              style: P5Text.body(14, color: P5Colors.white),
              decoration: InputDecoration(
                hintText: _txType == 'transfer'
                    ? 'Note (optional, e.g. Tarik ATM)'
                    : (_txType == 'income' ? 'Source (e.g. Uang saku, Gaji)' : 'Item or note (e.g. Kopi susu, Galon)'),
              ),
              onSubmitted: (_) => _submitTx(),
            ),
          ),

          // Category chips (Expense only)
          if (_txType == 'expense') ...[
            const SizedBox(height: 8),
            Text('CATEGORY', style: P5Text.display(10.5, color: P5Colors.muted)),
            const SizedBox(height: 4),
            Row(
              children: [
                for (var i = 0; i < kFinanceCategories.length; i++) ...[
                  if (i > 0) const SizedBox(width: 5),
                  Expanded(
                    child: P5Button(
                      label: kFinanceCategoryShort[kFinanceCategories[i]]!,
                      active: _selectedCat == kFinanceCategories[i],
                      height: 30,
                      fontSize: 10.5,
                      padding: const EdgeInsets.symmetric(horizontal: 4),
                      onTap: () => setState(() => _selectedCat = kFinanceCategories[i]),
                    ),
                  ),
                ],
              ],
            ),
          ],

          // Live-Balance Wallet Chips
          const SizedBox(height: 8),
          Text(
            _txType == 'transfer'
                ? 'FROM WALLET'
                : (_txType == 'income' ? 'TO WALLET' : 'WALLET (QRIS = BNI / MANDIRI)'),
            style: P5Text.display(10.5, color: P5Colors.muted),
          ),
          const SizedBox(height: 4),
          Row(
            children: [
              for (var i = 0; i < kFinanceWallets.length; i++) ...[
                if (i > 0) const SizedBox(width: 5),
                Expanded(
                  child: P5Button(
                    label: '${kFinanceWallets[i].toUpperCase()} · ${shortRupiah(bal[kFinanceWallets[i]] ?? 0)}',
                    active: _selectedWallet == kFinanceWallets[i],
                    height: 31,
                    fontSize: 10,
                    padding: const EdgeInsets.symmetric(horizontal: 4),
                    onTap: () {
                      setState(() {
                        _selectedWallet = kFinanceWallets[i];
                        if (_txType == 'transfer' && _selectedToWallet == _selectedWallet) {
                          _selectedToWallet = kFinanceWallets.firstWhere((w) => w != _selectedWallet);
                        }
                      });
                    },
                  ),
                ),
              ],
            ],
          ),

          if (_txType == 'transfer') ...[
            const SizedBox(height: 8),
            Text('TO WALLET', style: P5Text.display(10.5, color: P5Colors.muted)),
            const SizedBox(height: 4),
            Row(
              children: [
                for (var i = 0; i < kFinanceWallets.length; i++) ...[
                  if (i > 0) const SizedBox(width: 5),
                  Expanded(
                    child: P5Button(
                      label: '${kFinanceWallets[i].toUpperCase()} · ${shortRupiah(bal[kFinanceWallets[i]] ?? 0)}',
                      active: _selectedToWallet == kFinanceWallets[i],
                      activeVariant: P5BtnVariant.gold,
                      height: 31,
                      fontSize: 10,
                      padding: const EdgeInsets.symmetric(horizontal: 4),
                      onTap: () => setState(() => _selectedToWallet = kFinanceWallets[i]),
                    ),
                  ),
                ],
              ],
            ),
          ],

          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: P5Button(
                  label: ctaLabel,
                  icon: isEdit ? Icons.check : Icons.add,
                  variant: ctaVariant,
                  height: 40,
                  fontSize: 13,
                  expand: true,
                  onTap: widget.app.sheetBusy ? null : _submitTx,
                ),
              ),
              if (isEdit) ...[
                const SizedBox(width: 7),
                P5Button(
                  label: 'CANCEL',
                  variant: P5BtnVariant.soft,
                  height: 40,
                  fontSize: 11.5,
                  onTap: () {
                    _cancelEdit();
                    widget.app.notify('Edit discarded', tone: P5ToastTone.info, ms: 1500);
                  },
                ),
              ],
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildHistoryCard(List<Map<String, dynamic>> items) {
    final q = _searchCtrl.text.trim().toLowerCase();
    final tod = todayIso();
    final filtered = items.where((t) {
      if (_txFilter == 'today' && t['date'] != tod) return false;
      if ((_txFilter == 'expense' || _txFilter == 'income' || _txFilter == 'transfer') && t['type'] != _txFilter) {
        return false;
      }
      if (_txFilter.startsWith('cat:') && (t['type'] != 'expense' || t['category'] != _txFilter.substring(4))) {
        return false;
      }
      if (q.isNotEmpty) {
        final hay = "${t['title']} ${t['category']} ${t['wallet']} ${t['to_wallet']} ${t['date']}".toLowerCase();
        if (!hay.contains(q)) return false;
      }
      return true;
    }).toList();

    // Live filtered subtotal
    var countText = '${filtered.length} ${filtered.length == 1 ? "entry" : "entries"}';
    if (filtered.isNotEmpty) {
      final types = filtered.map((t) => t['type']).toSet();
      if (types.length == 1 && types.first == 'expense') {
        final s = filtered.fold<int>(0, (acc, t) => acc + ((t['amount'] as int?) ?? 0));
        countText += ' · -${formatRupiah(s)}';
      } else if (types.length == 1 && types.first == 'income') {
        final s = filtered.fold<int>(0, (acc, t) => acc + ((t['amount'] as int?) ?? 0));
        countText += ' · +${formatRupiah(s)}';
      } else if (types.length == 1 && types.first == 'transfer') {
        final s = filtered.fold<int>(0, (acc, t) => acc + ((t['amount'] as int?) ?? 0));
        countText += ' · ${formatRupiah(s)}';
      } else {
        final inc = filtered.where((t) => t['type'] == 'income').fold<int>(0, (a, t) => a + ((t['amount'] as int?) ?? 0));
        final exp = filtered.where((t) => t['type'] == 'expense').fold<int>(0, (a, t) => a + ((t['amount'] as int?) ?? 0));
        final net = inc - exp;
        countText += ' · Net ${net >= 0 ? "+" : ""}${formatRupiah(net)}';
      }
    }

    const filters = [
      ('all', 'ALL'),
      ('expense', 'OUT'),
      ('income', 'IN'),
      ('transfer', 'TF'),
      ('cat:Jajan', 'JAJAN'),
      ('cat:Alat', 'ALAT'),
      ('cat:Mingguan&Bulanan', 'MINGGUAN'),
      ('cat:Hiburan', 'HIBURAN'),
    ];

    return P5Card(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('TRANSACTIONS', style: P5Text.display(14, color: P5Colors.red)),
              const Spacer(),
              Flexible(
                child: Text(
                  countText,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: P5Text.display(12, color: const Color(0xFFC2C6D8)),
                ),
              ),
              if (_txFilter != 'all' || q.isNotEmpty) ...[
                const SizedBox(width: 6),
                P5Button(
                  label: '× RESET',
                  variant: P5BtnVariant.gold,
                  height: 24,
                  fontSize: 9.5,
                  padding: const EdgeInsets.symmetric(horizontal: 7),
                  onTap: () {
                    setState(() {
                      _txFilter = 'all';
                      _searchCtrl.clear();
                    });
                    widget.app.notify('Filters cleared', tone: P5ToastTone.info, ms: 1400);
                  },
                ),
              ],
            ],
          ),
          const SizedBox(height: 8),
          TextField(
            controller: _searchCtrl,
            style: P5Text.body(13.5, color: P5Colors.white),
            decoration: InputDecoration(
              hintText: 'Search transactions, category, wallet...',
              prefixIcon: const Icon(Icons.search, size: 17, color: P5Colors.muted),
              suffixIcon: q.isNotEmpty
                  ? IconButton(
                      icon: const Icon(Icons.close, size: 16, color: P5Colors.muted),
                      onPressed: () => setState(() => _searchCtrl.clear()),
                    )
                  : null,
            ),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 7),
          Wrap(
            spacing: 5,
            runSpacing: 5,
            children: [
              for (final f in filters)
                P5Button(
                  label: f.$2,
                  active: _txFilter == f.$1,
                  height: 26,
                  fontSize: 10,
                  padding: const EdgeInsets.symmetric(horizontal: 9),
                  onTap: () => setState(() => _txFilter = f.$1),
                ),
            ],
          ),
          const SizedBox(height: 10),
          if (filtered.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 24),
              child: Center(
                child: Text(
                  widget.app.isSheetMode && widget.app.sheetBusy && widget.app.sheetData == null
                      ? 'Loading from Google Sheets...'
                      : (_txFilter != 'all' || q.isNotEmpty
                          ? 'No transactions match this filter.\nTap × RESET to show all.'
                          : 'No transactions this month yet.\nRecord an expense, income, or transfer above!'),
                  textAlign: TextAlign.center,
                  style: P5Text.body(13, color: P5Colors.muted),
                ),
              ),
            )
          else
            Column(
              children: [
                for (final t in filtered) _buildTxRow(t),
              ],
            ),
        ],
      ),
    );
  }

  Widget _buildTxRow(Map<String, dynamic> t) {
    final type = t['type'] as String? ?? 'expense';
    final isPool = t['sheet_kind'] == 'qris_pool';
    final (tag, tagBg, accent, amtTxt, meta) = switch (type) {
      'income' => (
          'IN',
          P5Colors.mintBg,
          P5Colors.mint,
          '+${formatRupiah(t['amount'] as num)}',
          '${t['wallet']} · ${t['date']}'
        ),
      'transfer' => (
          'TF',
          P5Colors.goldBg,
          P5Colors.goldFg,
          formatRupiah(t['amount'] as num),
          '${t['wallet']} → ${t['to_wallet']} · ${t['date']}'
        ),
      _ => (
          'OUT',
          P5Colors.redBg,
          P5Colors.redFg,
          '-${formatRupiah(t['amount'] as num)}',
          '${t['category']}${(t['wallet'] ?? '').toString().isNotEmpty ? " · ${t['wallet']}" : ""} · ${t['date']}'
        ),
    };

    return Container(
      margin: const EdgeInsets.only(bottom: 7),
      decoration: BoxDecoration(
        color: P5Colors.surface,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: _editingTxId == t['id'] ? P5Colors.gold : P5Colors.border),
      ),
      clipBehavior: Clip.antiAlias,
      child: IntrinsicHeight(
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Container(width: 3.5, color: accent),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(9, 8, 8, 8),
                child: Row(
                  children: [
                    Container(
                      width: 34,
                      height: 22,
                      alignment: Alignment.center,
                      decoration: BoxDecoration(color: tagBg, borderRadius: BorderRadius.circular(4)),
                      child: Text(tag, style: P5Text.display(10.5, color: accent)),
                    ),
                    const SizedBox(width: 9),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text(
                            t['title'].toString(),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: P5Text.body(13.5, color: P5Colors.white, bold: true),
                          ),
                          Text(
                            meta,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: P5Text.body(11, color: P5Colors.muted),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 6),
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.end,
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Text(amtTxt, style: P5Text.display(13.5, color: accent)),
                        if (!isPool)
                          Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              InkWell(
                                onTap: () => _startEdit(t),
                                child: const Padding(
                                  padding: EdgeInsets.all(3),
                                  child: Icon(Icons.edit, size: 15, color: P5Colors.goldFg),
                                ),
                              ),
                              InkWell(
                                onTap: () => _duplicateTx(t),
                                child: const Padding(
                                  padding: EdgeInsets.all(3),
                                  child: Icon(Icons.copy, size: 14, color: P5Colors.muted),
                                ),
                              ),
                              InkWell(
                                onTap: () => _deleteTx(t),
                                child: const Padding(
                                  padding: EdgeInsets.all(3),
                                  child: Icon(Icons.delete_outline, size: 15, color: P5Colors.redFg),
                                ),
                              ),
                            ],
                          ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildPlanPage(Map<String, int> bal, Map<String, dynamic> summ) {
    final app = widget.app;
    final budgets = app.finance.budgets();
    final byCat = (summ['by_category'] as Map?) ?? const {};
    final bills = app.finance.bills();
    final shortcuts = app.finance.shortcuts();
    final maxPos = [1, ...kFinanceWallets.map((w) => (bal[w] ?? 0) > 0 ? bal[w]! : 0)].reduce((a, b) => a > b ? a : b);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Monthly Category Budgets
        P5Card(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('MONTHLY CATEGORY BUDGETS', style: P5Text.display(14, color: P5Colors.red)),
              const SizedBox(height: 10),
              for (final cat in kFinanceCategories) ...[
                Builder(builder: (_) {
                  final spent = (byCat[cat] as num?)?.toInt() ?? 0;
                  final limit = budgets[cat];
                  final ratio = (limit != null && limit > 0) ? spent / limit : 0.0;
                  final pct = (ratio * 100).round();
                  final Color col = limit == null
                      ? P5Colors.muted
                      : (ratio >= 1.0 ? P5Colors.red : (ratio >= kFinanceWarnRatio ? P5Colors.gold : P5Colors.mint));
                  final pctLabel = limit == null
                      ? 'No limit'
                      : (ratio >= 1.0 ? '$pct% OVER!' : (ratio >= kFinanceWarnRatio ? '$pct% WARN' : '$pct%'));
                  return Padding(
                    padding: const EdgeInsets.only(bottom: 11),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Text(cat, style: P5Text.body(13.5, color: P5Colors.white, bold: true)),
                            const Spacer(),
                            Text(pctLabel, style: P5Text.display(12, color: col)),
                          ],
                        ),
                        const SizedBox(height: 4),
                        ClipRRect(
                          borderRadius: BorderRadius.circular(3),
                          child: LinearProgressIndicator(
                            value: limit != null ? ratio.clamp(0.0, 1.0) : 0.0,
                            minHeight: 7,
                            backgroundColor: const Color(0xFF1B1E28),
                            valueColor: AlwaysStoppedAnimation(col),
                          ),
                        ),
                        const SizedBox(height: 6),
                        Row(
                          children: [
                            Expanded(
                              child: Text(
                                limit != null ? '${formatRupiah(spent)} / ${formatRupiah(limit)}' : 'Spent: ${formatRupiah(spent)}',
                                style: P5Text.body(11.5, color: P5Colors.muted),
                              ),
                            ),
                            SizedBox(
                              width: 104,
                              child: TextField(
                                controller: _budgetCtrls[cat],
                                style: P5Text.body(12.5, color: P5Colors.white),
                                decoration: const InputDecoration(
                                  hintText: '500rb',
                                  contentPadding: EdgeInsets.symmetric(horizontal: 8, vertical: 6),
                                ),
                              ),
                            ),
                            const SizedBox(width: 5),
                            P5Button(
                              label: 'SET',
                              variant: P5BtnVariant.gold,
                              height: 30,
                              fontSize: 10.5,
                              onTap: () async {
                                final ok = await app.finance.setBudget(cat, _budgetCtrls[cat]!.text);
                                if (!ok) {
                                  app.notify('Invalid budget amount (try e.g. 500rb)', tone: P5ToastTone.warn);
                                  return;
                                }
                                final lim = app.finance.budgets()[cat];
                                app.notify(
                                  lim != null ? '$cat budget set to ${formatRupiah(lim)}' : 'Cleared $cat budget',
                                  tone: lim != null ? P5ToastTone.ok : P5ToastTone.info,
                                );
                                setState(() {});
                              },
                            ),
                          ],
                        ),
                      ],
                    ),
                  );
                }),
              ],
            ],
          ),
        ),
        const SizedBox(height: 10),

        // Wallet Balances Breakdown
        P5Card(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('WALLET BALANCES', style: P5Text.display(14, color: P5Colors.red)),
              const SizedBox(height: 8),
              for (final w in kFinanceWallets) ...[
                Row(
                  children: [
                    Text(w, style: P5Text.body(13.5, color: P5Colors.white, bold: true)),
                    const Spacer(),
                    Text(
                      formatRupiah(bal[w] ?? 0),
                      style: P5Text.display(15, color: (bal[w] ?? 0) < 0 ? P5Colors.redFg : P5Colors.goldFg),
                    ),
                  ],
                ),
                const SizedBox(height: 4),
                ClipRRect(
                  borderRadius: BorderRadius.circular(3),
                  child: LinearProgressIndicator(
                    value: ((bal[w] ?? 0) > 0 ? (bal[w]! / maxPos) : 0.0).clamp(0.0, 1.0),
                    minHeight: 6,
                    backgroundColor: const Color(0xFF1B1E28),
                    valueColor: const AlwaysStoppedAnimation(P5Colors.goldWarm),
                  ),
                ),
                const SizedBox(height: 8),
              ],
            ],
          ),
        ),
        const SizedBox(height: 10),

        // Recurring Monthly Bills
        P5Card(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('RECURRING MONTHLY BILLS', style: P5Text.display(14, color: P5Colors.red)),
              const SizedBox(height: 8),
              if (bills.isEmpty)
                Text('No recurring bills yet. Add rent, Wi-Fi, or subscriptions below!',
                    style: P5Text.body(12.5, color: P5Colors.muted))
              else
                for (final b in bills)
                  Container(
                    margin: const EdgeInsets.only(bottom: 6),
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
                    decoration: BoxDecoration(
                      color: P5Colors.surface,
                      borderRadius: BorderRadius.circular(7),
                      border: Border.all(color: P5Colors.border),
                    ),
                    child: Row(
                      children: [
                        Expanded(
                          child: Text(
                            "Day ${(b['day'] as int).toString().padLeft(2, '0')} · ${b['name']} (${formatRupiah(b['amount'] as num)} · ${b['wallet']})",
                            style: P5Text.body(
                              12.5,
                              color: FinanceStore.isBillPaid(b, app.financeYear, app.financeMonth)
                                  ? P5Colors.muted
                                  : P5Colors.white,
                            ),
                          ),
                        ),
                        if (FinanceStore.isBillPaid(b, app.financeYear, app.financeMonth))
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                            decoration: BoxDecoration(color: P5Colors.mintBg, borderRadius: BorderRadius.circular(4)),
                            child: Text('PAID', style: P5Text.display(10.5, color: P5Colors.mint)),
                          )
                        else
                          P5Button(
                            label: 'PAY',
                            variant: P5BtnVariant.gold,
                            height: 26,
                            fontSize: 10,
                            onTap: () async {
                              if (app.isSheetMode) {
                                final res = await app.submitSheetTransaction(
                                  type: 'expense',
                                  amount: b['amount'] as int,
                                  title: b['name'].toString(),
                                  category: b['category'].toString(),
                                  wallet: b['wallet'].toString(),
                                  toWallet: '',
                                  dateStr: todayIso(),
                                );
                                if (res.ok) {
                                  await app.finance.markBillPaid(b['id'].toString(), app.financeYear, app.financeMonth);
                                  setState(() {});
                                }
                              } else {
                                final (paidB, tx) =
                                    await app.finance.payBill(b['id'].toString(), app.financeYear, app.financeMonth);
                                if (paidB != null && tx != null) {
                                  app.notify("Paid '${paidB['name']}' · ${formatRupiah(tx['amount'] as num)}",
                                      tone: P5ToastTone.ok);
                                  setState(() {});
                                }
                              }
                            },
                          ),
                        const SizedBox(width: 5),
                        P5Button(
                          label: '×',
                          height: 26,
                          fontSize: 12,
                          padding: const EdgeInsets.symmetric(horizontal: 7),
                          onTap: () async {
                            await app.finance.deleteBill(b['id'].toString());
                            app.notify('Removed recurring bill', tone: P5ToastTone.info);
                            setState(() {});
                          },
                        ),
                      ],
                    ),
                  ),
              const SizedBox(height: 8),
              Row(
                children: [
                  Expanded(
                    flex: 2,
                    child: P5NudgeBox(
                      nudged: _billNameNudged,
                      child: TextField(
                        controller: _billNameCtrl,
                        style: P5Text.body(13, color: P5Colors.white),
                        decoration: const InputDecoration(hintText: 'Bill name (Kos, Wi-Fi)'),
                      ),
                    ),
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    child: P5NudgeBox(
                      nudged: _billAmtNudged,
                      child: TextField(
                        controller: _billAmtCtrl,
                        style: P5Text.body(13, color: P5Colors.white),
                        decoration: const InputDecoration(hintText: '500rb'),
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 6),
              Row(
                children: [
                  DropdownButton<int>(
                    value: _billDay,
                    dropdownColor: P5Colors.card,
                    style: P5Text.body(12.5, color: P5Colors.white),
                    items: [for (var d = 1; d <= 31; d++) DropdownMenuItem(value: d, child: Text('Day $d'))],
                    onChanged: (v) => setState(() => _billDay = v ?? 1),
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    child: DropdownButton<String>(
                      isExpanded: true,
                      value: _billCat,
                      dropdownColor: P5Colors.card,
                      style: P5Text.body(12.5, color: P5Colors.white),
                      items: [for (final c in kFinanceCategories) DropdownMenuItem(value: c, child: Text(c))],
                      onChanged: (v) => setState(() => _billCat = v ?? 'Mingguan&Bulanan'),
                    ),
                  ),
                  const SizedBox(width: 6),
                  DropdownButton<String>(
                    value: _billWal,
                    dropdownColor: P5Colors.card,
                    style: P5Text.body(12.5, color: P5Colors.white),
                    items: [for (final w in kFinanceWallets) DropdownMenuItem(value: w, child: Text(w))],
                    onChanged: (v) => setState(() => _billWal = v ?? 'BNI'),
                  ),
                  const SizedBox(width: 6),
                  P5Button(
                    label: 'ADD',
                    icon: Icons.add,
                    variant: P5BtnVariant.primary,
                    height: 32,
                    fontSize: 11,
                    onTap: () async {
                      if (_billNameCtrl.text.trim().isEmpty) {
                        _triggerNudge((v) => _billNameNudged = v, 'Enter a bill name first');
                        return;
                      }
                      if (parseRupiah(_billAmtCtrl.text) == null) {
                        _triggerNudge((v) => _billAmtNudged = v, 'Enter a valid bill amount (e.g. 500rb)');
                        return;
                      }
                      final added = await app.finance.addBill(
                        name: _billNameCtrl.text,
                        day: _billDay,
                        amount: _billAmtCtrl.text,
                        category: _billCat,
                        wallet: _billWal,
                      );
                      if (added != null) {
                        _billNameCtrl.clear();
                        _billAmtCtrl.clear();
                        app.notify("Added bill '${added['name']}'", tone: P5ToastTone.ok);
                        setState(() {});
                      }
                    },
                  ),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: 10),

        // Quick Shortcuts Manager
        P5Card(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('QUICK SHORTCUTS (PINTASAN)', style: P5Text.display(14, color: P5Colors.red)),
              const SizedBox(height: 8),
              for (final sc in shortcuts)
                Container(
                  margin: const EdgeInsets.only(bottom: 5),
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                  decoration: BoxDecoration(
                    color: P5Colors.surface,
                    borderRadius: BorderRadius.circular(7),
                    border: Border.all(color: P5Colors.border),
                  ),
                  child: Row(
                    children: [
                      Expanded(
                        child: Text(
                          "${sc['name']} (${sc['category']}${sc['amount'] != null ? ' · ${formatRupiah(sc['amount'] as num)}' : ' · manual'})",
                          style: P5Text.body(12.5, color: P5Colors.white),
                        ),
                      ),
                      P5Button(
                        label: '×',
                        height: 24,
                        fontSize: 12,
                        padding: const EdgeInsets.symmetric(horizontal: 7),
                        onTap: () async {
                          await app.finance.deleteShortcut(sc['name'].toString());
                          app.notify("Removed shortcut '${sc['name']}'", tone: P5ToastTone.info);
                          setState(() {});
                        },
                      ),
                    ],
                  ),
                ),
              const SizedBox(height: 6),
              Row(
                children: [
                  Expanded(
                    flex: 2,
                    child: P5NudgeBox(
                      nudged: _scNameNudged,
                      child: TextField(
                        controller: _scNameCtrl,
                        style: P5Text.body(13, color: P5Colors.white),
                        decoration: const InputDecoration(hintText: 'Name (e.g. Parkir)'),
                      ),
                    ),
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    child: TextField(
                      controller: _scAmtCtrl,
                      style: P5Text.body(13, color: P5Colors.white),
                      decoration: const InputDecoration(hintText: 'Price opt.'),
                    ),
                  ),
                  const SizedBox(width: 6),
                  P5Button(
                    label: 'ADD',
                    variant: P5BtnVariant.gold,
                    height: 36,
                    fontSize: 11,
                    onTap: () async {
                      if (_scNameCtrl.text.trim().isEmpty) {
                        _triggerNudge((v) => _scNameNudged = v, 'Enter a shortcut name first');
                        return;
                      }
                      final res = await app.finance.addShortcut(_scNameCtrl.text, _scCat, _scAmtCtrl.text);
                      if (res == null) {
                        app.notify('Invalid shortcut price (e.g. 7rb)', tone: P5ToastTone.warn);
                        return;
                      }
                      _scNameCtrl.clear();
                      _scAmtCtrl.clear();
                      app.notify("Saved shortcut '${res['name']}'", tone: P5ToastTone.ok);
                      setState(() {});
                    },
                  ),
                ],
              ),
              const SizedBox(height: 4),
              DropdownButton<String>(
                value: _scCat,
                dropdownColor: P5Colors.card,
                style: P5Text.body(12.5, color: P5Colors.white),
                items: [for (final c in kFinanceCategories) DropdownMenuItem(value: c, child: Text('Category: $c'))],
                onChanged: (v) => setState(() => _scCat = v ?? 'Jajan'),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
