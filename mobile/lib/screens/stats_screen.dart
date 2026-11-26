import 'dart:async';

// ignore: deprecated_member_use
import 'package:flutter/material.dart';

import '../core/app_state.dart';
import '../core/storage.dart';
import '../theme/p5_theme.dart';
import '../widgets/p5_widgets.dart';
import '../widgets/stat_pentagon.dart';

/// Persona 5 Royal Social Stats Pentagon, Confidant Rank progress, Daily Habits & Activity Log.
class StatsScreen extends StatefulWidget {
  final MorganaAppState app;

  const StatsScreen({super.key, required this.app});

  @override
  State<StatsScreen> createState() => _StatsScreenState();
}

class _StatsScreenState extends State<StatsScreen> {
  String _selectedStat = 'knowledge';
  int _subTab = 0; // 0 = DAILY HABITS, 1 = LOG ACTIVITY

  // Habit form
  final _habitTitleCtrl = TextEditingController();
  int _habitPoints = 2;
  bool _habitNudged = false;

  // Activity form
  final _actCtrl = TextEditingController();
  int _actPoints = 2;
  String _actBlock = defaultTimeBlock();
  String? _editingActId;
  bool _actNudged = false;
  String _logFilter = 'all';

  // Undo & nudge timers
  Map<String, dynamic>? _undoPayload;
  String _undoKind = ''; // 'habit' | 'activity'
  Timer? _undoTimer;
  Timer? _nudgeTimer;

  @override
  void dispose() {
    _undoTimer?.cancel();
    _nudgeTimer?.cancel();
    _habitTitleCtrl.dispose();
    _actCtrl.dispose();
    super.dispose();
  }

  void _setUndo(String kind, Map<String, dynamic> payload) {
    _undoTimer?.cancel();
    setState(() {
      _undoKind = kind;
      _undoPayload = payload;
    });
    _undoTimer = Timer(const Duration(seconds: 5), () {
      if (mounted) setState(() => _undoPayload = null);
    });
  }

  Future<void> _restoreUndo() async {
    final p = _undoPayload;
    if (p == null) return;
    _undoTimer?.cancel();
    if (_undoKind == 'habit') {
      await widget.app.habits.restore(p);
      widget.app.notify("Restored habit '${p['title']}'", tone: P5ToastTone.ok);
    } else if (_undoKind == 'activity') {
      await widget.app.stats.restore(p);
      widget.app.notify("Restored '${p['activity']}'", tone: P5ToastTone.ok);
    }
    widget.app.audio.playSfx('select');
    setState(() => _undoPayload = null);
  }

  Future<void> _addHabit() async {
    final t = _habitTitleCtrl.text.trim();
    if (t.isEmpty) {
      widget.app.audio.playSfx('cancel');
      widget.app.notify('Enter a habit name first!', tone: P5ToastTone.warn);
      setState(() => _habitNudged = true);
      _nudgeTimer?.cancel();
      _nudgeTimer = Timer(const Duration(milliseconds: 450), () {
        if (mounted) setState(() => _habitNudged = false);
      });
      return;
    }
    await widget.app.habits.add(t, stat: _selectedStat, points: _habitPoints);
    widget.app.audio.playSfx('select');
    final sShort = kSocialStats[_selectedStat]!.short;
    widget.app.notify("Added habit '$t' (+$_habitPoints $sShort)", tone: P5ToastTone.ok);
    _habitTitleCtrl.clear();
    setState(() {});
  }

  Future<void> _toggleHabit(Map<String, dynamic> habit) async {
    final id = habit['id'].toString();
    final (checked, _, oldLv, newLv) = await widget.app.habits.toggleToday(id, widget.app.stats);
    final st = (habit['stat'] ?? 'knowledge').toString();
    final meta = kSocialStats[st] ?? kSocialStats['knowledge']!;
    if (checked) {
      if (newLv > oldLv) {
        widget.app.audio.playSfx('rank_up');
        widget.app.morganaSay('Rank up! ${meta.label} reached Lv $newLv!', emotion: 'excited', voice: 'oh_excited');
        widget.app.notify('RANK UP! ${meta.label} Lv $newLv ★', tone: P5ToastTone.gold, ms: 3000);
      } else {
        widget.app.audio.playSfx('select');
        widget.app.notify("+${habit['points']} ${meta.short} · '${habit['title']}'", tone: P5ToastTone.ok);
      }
    } else {
      widget.app.audio.playSfx('cancel');
      widget.app.notify("Unchecked '${habit['title']}'", tone: P5ToastTone.info, ms: 1600);
    }
    setState(() {});
  }

  Future<void> _deleteHabit(Map<String, dynamic> habit) async {
    final removed = await widget.app.habits.delete(habit['id'].toString());
    if (removed == null) return;
    widget.app.audio.playSfx('close');
    _setUndo('habit', removed);
    widget.app.notify("Removed habit '${removed['title']}'", tone: P5ToastTone.info);
  }

  Future<void> _saveActivity() async {
    final text = _actCtrl.text.trim();
    if (text.isEmpty) {
      widget.app.audio.playSfx('cancel');
      widget.app.notify('Describe the activity first!', tone: P5ToastTone.warn);
      setState(() => _actNudged = true);
      _nudgeTimer?.cancel();
      _nudgeTimer = Timer(const Duration(milliseconds: 450), () {
        if (mounted) setState(() => _actNudged = false);
      });
      return;
    }
    final meta = kSocialStats[_selectedStat]!;
    if (_editingActId != null) {
      await widget.app.stats.update(
        _editingActId!,
        stat: _selectedStat,
        activity: text,
        points: _actPoints,
        block: _actBlock,
      );
      widget.app.audio.playSfx('select');
      widget.app.notify("Updated '$text' (+$_actPoints ${meta.short})", tone: P5ToastTone.gold);
      _editingActId = null;
    } else {
      final (_, oldLv, newLv) = await widget.app.stats.add(
        _selectedStat,
        text,
        points: _actPoints,
        block: _actBlock,
      );
      if (newLv > oldLv) {
        widget.app.audio.playSfx('rank_up');
        widget.app.morganaSay('Rank up! ${meta.label} reached Lv $newLv!', emotion: 'excited', voice: 'oh_excited');
        widget.app.notify('RANK UP! ${meta.label} Lv $newLv ★', tone: P5ToastTone.gold, ms: 3000);
      } else {
        widget.app.audio.playSfx('select');
        widget.app.notify("+$_actPoints ${meta.short} logged!", tone: P5ToastTone.ok);
      }
    }
    _actCtrl.clear();
    setState(() {});
  }

  Future<void> _deleteActivity(Map<String, dynamic> entry) async {
    final removed = await widget.app.stats.delete(entry['id'].toString());
    if (removed == null) return;
    widget.app.audio.playSfx('close');
    _setUndo('activity', removed);
    widget.app.notify("Removed '${removed['activity']}'", tone: P5ToastTone.info);
  }

  @override
  Widget build(BuildContext context) {
    final app = widget.app;
    final totals = app.stats.totals();
    final conf = overallConfidantLevel(totals);
    final streak = app.stats.streakDays();
    final todayPts = app.stats.todayPoints();
    final hSum = app.habits.todaySummary();

    return Column(
      children: [
        P5Header(
          title: 'Social Stats',
          onAvatarTap: () => app.navigateTo('home'),
          actions: [
            if (app.isDataSyncActive) ...[
              P5Button(
                label: '',
                icon: Icons.sync,
                variant: app.companionSyncBusy ? P5BtnVariant.gold : P5BtnVariant.mint,
                height: 28,
                fontSize: 13,
                onTap: app.companionSyncBusy ? null : () => app.syncCompanionData(silent: false),
              ),
              const SizedBox(width: 6),
            ],
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
              decoration: BoxDecoration(
                color: P5Colors.goldBg,
                borderRadius: BorderRadius.circular(6),
                border: Border.all(color: P5Colors.goldBorder),
              ),
              child: Text('LV ${conf.total}/${conf.maxBase}', style: P5Text.display(12, color: P5Colors.gold)),
            ),
          ],
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 84),
            children: [
              // KPI strip
              Row(
                children: [
                  Expanded(
                    child: P5KpiCard(
                      title: 'TODAY',
                      value: '+$todayPts PTS',
                      subtitle: '${app.stats.todayEntries().length} logs',
                      accent: P5Colors.gold,
                      tint: const Color(0xFF211C0E),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: P5KpiCard(
                      title: 'STREAK',
                      value: '${streak}D',
                      subtitle: 'Habits ${hSum.done}/${hSum.total}',
                      accent: P5Colors.mint,
                      tint: const Color(0xFF0E211A),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 10),

              // Golden Star Pentagon + Selected Stat spotlight
              P5Card(
                padding: const EdgeInsets.fromLTRB(10, 10, 10, 12),
                child: Column(
                  children: [
                    StatPentagonWidget(
                      totals: totals,
                      selectedStat: _selectedStat,
                      onSelectStat: (k) {
                        app.audio.playSfx('hover');
                        setState(() => _selectedStat = k);
                      },
                    ),
                    const SizedBox(height: 8),
                    for (final key in kSocialStatKeys) _buildStatBar(key, totals[key] ?? 0),
                  ],
                ),
              ),

              if (_undoPayload != null)
                P5UndoBar(
                  message: _undoKind == 'habit'
                      ? "Removed habit '${_undoPayload!['title']}'"
                      : "Removed '${_undoPayload!['activity']}'",
                  onUndo: _restoreUndo,
                ),

              const SizedBox(height: 12),

              // Sub-tabs: DAILY HABITS | LOG ACTIVITY
              Row(
                children: [
                  Expanded(
                    child: P5Button(
                      label: 'DAILY HABITS (${hSum.done}/${hSum.total})',
                      active: _subTab == 0,
                      activeVariant: P5BtnVariant.primary,
                      height: 36,
                      expand: true,
                      onTap: () => setState(() => _subTab = 0),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: P5Button(
                      label: 'LOG ACTIVITY',
                      active: _subTab == 1,
                      activeVariant: P5BtnVariant.gold,
                      height: 36,
                      expand: true,
                      onTap: () => setState(() => _subTab = 1),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 10),

              if (_subTab == 0) _buildHabitsSection(app) else _buildActivitySection(app),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildStatBar(String key, int pts) {
    final meta = kSocialStats[key]!;
    final prog = statLevelProgress(pts);
    final rankName = statRankTitle(key, prog.level);
    final isSel = key == _selectedStat;
    final pct = (prog.into / prog.span).clamp(0.0, 1.0);

    return GestureDetector(
      onTap: () => setState(() => _selectedStat = key),
      child: Container(
        margin: const EdgeInsets.only(bottom: 6),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
        decoration: BoxDecoration(
          color: isSel ? P5Colors.goldBg : P5Colors.surface,
          borderRadius: BorderRadius.circular(7),
          border: Border.all(color: isSel ? P5Colors.gold : P5Colors.border),
        ),
        child: Column(
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    '${meta.label.toUpperCase()} · Lv ${prog.level} $rankName',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: P5Text.display(12, color: isSel ? P5Colors.gold : P5Colors.white),
                  ),
                ),
                const SizedBox(width: 6),
                Text('$pts pts (${prog.toNext} next)', style: P5Text.body(11, color: P5Colors.goldFg, bold: true)),
              ],
            ),
            const SizedBox(height: 5),
            ClipRRect(
              borderRadius: BorderRadius.circular(3),
              child: LinearProgressIndicator(
                value: pct,
                minHeight: 5,
                backgroundColor: P5Colors.black,
                valueColor: AlwaysStoppedAnimation<Color>(isSel ? P5Colors.gold : P5Colors.red),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildHabitsSection(MorganaAppState app) {
    final habits = app.habits.all();
    final selMeta = kSocialStats[_selectedStat]!;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        P5Card(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('ADD DAILY HABIT (${selMeta.short})', style: P5Text.display(12, color: P5Colors.gold)),
              const SizedBox(height: 8),
              P5NudgeBox(
                nudged: _habitNudged,
                child: TextField(
                  controller: _habitTitleCtrl,
                  decoration: InputDecoration(hintText: selMeta.placeholder),
                  onSubmitted: (_) => _addHabit(),
                ),
              ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 6,
                runSpacing: 6,
                children: [
                  for (final k in kSocialStatKeys)
                    P5Button(
                      label: kSocialStats[k]!.short,
                      active: _selectedStat == k,
                      activeVariant: P5BtnVariant.gold,
                      height: 28,
                      fontSize: 11,
                      onTap: () => setState(() => _selectedStat = k),
                    ),
                ],
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  for (final p in kQuickStatPoints) ...[
                    P5Button(
                      label: '+$p',
                      active: _habitPoints == p,
                      activeVariant: P5BtnVariant.mint,
                      height: 32,
                      fontSize: 11.5,
                      onTap: () => setState(() => _habitPoints = p),
                    ),
                    const SizedBox(width: 6),
                  ],
                  Expanded(
                    child: P5Button(
                      label: '+ ADD HABIT',
                      variant: P5BtnVariant.primary,
                      height: 34,
                      expand: true,
                      onTap: _addHabit,
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: 10),
        if (habits.isEmpty)
          P5Card(
            child: Text(
              'No daily habits yet! Add a recurring habit above to earn Persona 5 Social Stat points every day.',
              style: P5Text.body(12.5, color: P5Colors.muted),
            ),
          )
        else
          for (final h in habits) _buildHabitTile(h),
      ],
    );
  }

  Widget _buildHabitTile(Map<String, dynamic> h) {
    final done = HabitStore.isDoneOn(h);
    final streak = HabitStore.streak(h);
    final st = (h['stat'] ?? 'knowledge').toString();
    final meta = kSocialStats[st] ?? kSocialStats['knowledge']!;

    return Padding(
      padding: const EdgeInsets.only(bottom: 7),
      child: P5Card(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        borderColor: done ? P5Colors.mintBorder : P5Colors.border,
        child: Row(
          children: [
            GestureDetector(
              onTap: () => _toggleHabit(h),
              child: Container(
                width: 26,
                height: 26,
                decoration: BoxDecoration(
                  color: done ? P5Colors.mint : P5Colors.black,
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(color: done ? P5Colors.mint : P5Colors.border, width: 1.5),
                ),
                child: done ? const Icon(Icons.check, size: 17, color: P5Colors.black) : null,
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: GestureDetector(
                onTap: () => _toggleHabit(h),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      (h['title'] ?? '').toString(),
                      style: P5Text.body(13.5, color: done ? P5Colors.mint : P5Colors.white, bold: true),
                    ),
                    Text(
                      '+${h['points']} ${meta.label} · Streak ${streak}d',
                      style: P5Text.body(11.5, color: P5Colors.muted),
                    ),
                  ],
                ),
              ),
            ),
            P5Button(
              label: '',
              icon: Icons.delete_outline,
              variant: P5BtnVariant.danger,
              height: 30,
              onTap: () => _deleteHabit(h),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildActivitySection(MorganaAppState app) {
    final meta = kSocialStats[_selectedStat]!;
    final recent = app.stats.recent(limit: 40, statFilter: _logFilter);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        P5Card(
          borderColor: _editingActId != null ? P5Colors.gold : P5Colors.border,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                _editingActId != null ? 'EDIT ACTIVITY' : 'LOG ${meta.label.toUpperCase()} ACTIVITY',
                style: P5Text.display(12.5, color: P5Colors.gold),
              ),
              const SizedBox(height: 6),
              Wrap(
                spacing: 6,
                runSpacing: 6,
                children: [
                  for (final k in kSocialStatKeys)
                    P5Button(
                      label: kSocialStats[k]!.short,
                      active: _selectedStat == k,
                      activeVariant: P5BtnVariant.gold,
                      height: 28,
                      fontSize: 11,
                      onTap: () => setState(() => _selectedStat = k),
                    ),
                ],
              ),
              const SizedBox(height: 8),
              P5NudgeBox(
                nudged: _actNudged,
                child: TextField(
                  controller: _actCtrl,
                  decoration: InputDecoration(hintText: meta.placeholder),
                  onSubmitted: (_) => _saveActivity(),
                ),
              ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 6,
                runSpacing: 6,
                children: [
                  for (final b in kTimeBlocks)
                    P5Button(
                      label: b.toUpperCase(),
                      active: _actBlock == b,
                      activeVariant: P5BtnVariant.primary,
                      height: 28,
                      fontSize: 10.5,
                      onTap: () => setState(() => _actBlock = b),
                    ),
                ],
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  for (final p in kQuickStatPoints) ...[
                    P5Button(
                      label: '+$p',
                      active: _actPoints == p,
                      activeVariant: P5BtnVariant.mint,
                      height: 34,
                      fontSize: 12,
                      onTap: () => setState(() => _actPoints = p),
                    ),
                    const SizedBox(width: 6),
                  ],
                  Expanded(
                    child: P5Button(
                      label: _editingActId != null ? 'UPDATE' : 'LOG +$_actPoints ${meta.short}',
                      variant: _editingActId != null ? P5BtnVariant.gold : P5BtnVariant.primary,
                      height: 36,
                      expand: true,
                      onTap: _saveActivity,
                    ),
                  ),
                  if (_editingActId != null) ...[
                    const SizedBox(width: 6),
                    P5Button(
                      label: '×',
                      variant: P5BtnVariant.soft,
                      height: 36,
                      onTap: () => setState(() {
                        _editingActId = null;
                        _actCtrl.clear();
                      }),
                    ),
                  ],
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: 10),
        Wrap(
          spacing: 6,
          runSpacing: 6,
          children: [
            P5Button(
              label: 'ALL',
              active: _logFilter == 'all',
              activeVariant: P5BtnVariant.primary,
              height: 26,
              fontSize: 10.5,
              onTap: () => setState(() => _logFilter = 'all'),
            ),
            for (final k in kSocialStatKeys)
              P5Button(
                label: kSocialStats[k]!.short,
                active: _logFilter == k,
                activeVariant: P5BtnVariant.gold,
                height: 26,
                fontSize: 10.5,
                onTap: () => setState(() => _logFilter = k),
              ),
          ],
        ),
        const SizedBox(height: 8),
        if (recent.isEmpty)
          P5Card(
            child: Text('No logged activities yet.', style: P5Text.body(12.5, color: P5Colors.muted)),
          )
        else
          for (final e in recent)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: P5Card(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                child: Row(
                  children: [
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
                      decoration: BoxDecoration(
                        color: P5Colors.goldBg,
                        borderRadius: BorderRadius.circular(5),
                        border: Border.all(color: P5Colors.goldBorder),
                      ),
                      child: Text(
                        '+${e['points']} ${kSocialStats[e['stat']]?.short ?? 'PRO'}',
                        style: P5Text.display(11, color: P5Colors.gold),
                      ),
                    ),
                    const SizedBox(width: 9),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            (e['activity'] ?? '').toString(),
                            style: P5Text.body(13, color: P5Colors.white, bold: true),
                          ),
                          Text(
                            '${e['date']} · ${e['block'] ?? 'Daytime'}',
                            style: P5Text.body(11, color: P5Colors.muted),
                          ),
                        ],
                      ),
                    ),
                    P5Button(
                      label: '',
                      icon: Icons.edit,
                      variant: P5BtnVariant.soft,
                      height: 28,
                      onTap: () => setState(() {
                        _editingActId = e['id'].toString();
                        _selectedStat = (e['stat'] ?? 'knowledge').toString();
                        _actCtrl.text = (e['activity'] ?? '').toString();
                        _actPoints = ((e['points'] as num?)?.toInt() ?? 2).clamp(1, 3);
                        _actBlock = (e['block'] ?? defaultTimeBlock()).toString();
                      }),
                    ),
                    const SizedBox(width: 5),
                    P5Button(
                      label: '',
                      icon: Icons.delete_outline,
                      variant: P5BtnVariant.danger,
                      height: 28,
                      onTap: () => _deleteActivity(e),
                    ),
                  ],
                ),
              ),
            ),
      ],
    );
  }
}
