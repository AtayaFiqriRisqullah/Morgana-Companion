import 'dart:async';

// ignore: deprecated_member_use
import 'package:flutter/material.dart';

import '../core/app_state.dart';
import '../core/storage.dart';
import '../theme/p5_theme.dart';
import '../widgets/p5_widgets.dart';

class TasksScreen extends StatefulWidget {
  final MorganaAppState app;

  const TasksScreen({super.key, required this.app});

  @override
  State<TasksScreen> createState() => _TasksScreenState();
}

class _TasksScreenState extends State<TasksScreen> {
  final TextEditingController _inputCtrl = TextEditingController();
  final TextEditingController _searchCtrl = TextEditingController();

  String _filter = 'all';
  bool _newHigh = false;
  DateTime? _newDue;
  String _newStat = 'proficiency';
  bool _showDueBar = false;
  bool _inputNudged = false;

  List<Map<String, dynamic>> _undoItems = [];
  String _undoMsg = '';
  Timer? _undoTimer;
  Timer? _nudgeTimer;

  @override
  void dispose() {
    _undoTimer?.cancel();
    _nudgeTimer?.cancel();
    _inputCtrl.dispose();
    _searchCtrl.dispose();
    super.dispose();
  }

  void _triggerUndo(String msg, List<Map<String, dynamic>> items) {
    _undoTimer?.cancel();
    setState(() {
      _undoMsg = msg;
      _undoItems = items;
    });
    _undoTimer = Timer(const Duration(seconds: 6), () {
      if (mounted) setState(() => _undoItems = []);
    });
  }

  Future<void> _addTask() async {
    final title = _inputCtrl.text.trim();
    if (title.isEmpty) {
      widget.app.audio.playSfx('cancel');
      widget.app.notify('Type a task title first!', tone: P5ToastTone.warn);
      setState(() => _inputNudged = true);
      _nudgeTimer?.cancel();
      _nudgeTimer = Timer(const Duration(milliseconds: 450), () {
        if (mounted) setState(() => _inputNudged = false);
      });
      return;
    }
    final t = await widget.app.tasks.add(
      title,
      priority: _newHigh ? 'high' : 'normal',
      deadline: _newDue,
      stat: _newStat,
    );
    if (t != null) {
      widget.app.audio.playSfx('select');
      setState(() {
        _inputCtrl.clear();
        _newHigh = false;
        _newDue = null;
        _showDueBar = false;
        if (_filter == 'done') _filter = 'all';
      });
      widget.app.notify("Added task: ${t['title']}", tone: P5ToastTone.ok);
    }
  }

  void _applyDuePreset(String kind) {
    final now = DateTime.now();
    DateTime target;
    if (kind == '1h') {
      target = now.add(const Duration(hours: 1));
    } else if (kind == '3h') {
      target = now.add(const Duration(hours: 3));
    } else if (kind == 'eod') {
      target = DateTime(now.year, now.month, now.day, 23, 59);
      if (!target.isAfter(now)) target = target.add(const Duration(days: 1));
    } else {
      final tm = now.add(const Duration(days: 1));
      target = DateTime(tm.year, tm.month, tm.day, 9, 0);
    }
    setState(() => _newDue = target);
    widget.app.notify(
      'Deadline preset: ${target.day}/${target.month} ${target.hour.toString().padLeft(2, '0')}:${target.minute.toString().padLeft(2, '0')}',
      tone: P5ToastTone.gold,
      ms: 1600,
    );
  }

  @override
  Widget build(BuildContext context) {
    final app = widget.app;
    final all = app.tasks.all();
    final doneCnt = all.where((t) => t['done'] == true).length;
    final leftCnt = all.length - doneCnt;
    final q = _searchCtrl.text.trim().toLowerCase();
    final now = DateTime.now();

    final pending = all.where((t) => t['done'] != true).toList()
      ..sort((a, b) {
        final da = parseIsoDate(a['deadline']?.toString());
        final db = parseIsoDate(b['deadline']?.toString());
        final ova = (da != null && !da.isAfter(now)) ? 0 : 1;
        final ovb = (db != null && !db.isAfter(now)) ? 0 : 1;
        if (ova != ovb) return ova.compareTo(ovb);
        final pa = a['priority'] == 'high' ? 0 : 1;
        final pb = b['priority'] == 'high' ? 0 : 1;
        if (pa != pb) return pa.compareTo(pb);
        return (da?.millisecondsSinceEpoch ?? 9999999999999).compareTo(db?.millisecondsSinceEpoch ?? 9999999999999);
      });
    final finished = all.where((t) => t['done'] == true).toList();

    List<Map<String, dynamic>> shown = switch (_filter) {
      'todo' => pending,
      'due' => pending.where((t) => (t['deadline'] ?? '').toString().isNotEmpty).toList(),
      'high' => pending.where((t) => t['priority'] == 'high').toList(),
      'done' => finished,
      _ => [...pending, ...finished],
    };
    if (q.isNotEmpty) {
      shown = shown.where((t) => t['title'].toString().toLowerCase().contains(q)).toList();
    }

    return Column(
      children: [
        P5Header(
          title: 'TASKS',
          onAvatarTap: () => app.navigateTo('home'),
          actions: [
            Text('$leftCnt LEFT · $doneCnt DONE', style: P5Text.display(12, color: P5Colors.goldFg)),
            const SizedBox(width: 8),
            P5Button(
              label: 'NOTES',
              height: 28,
              fontSize: 10.5,
              onTap: () => app.navigateTo('notes'),
            ),
          ],
        ),
        LinearProgressIndicator(
          value: all.isEmpty ? 0.0 : doneCnt / all.length,
          minHeight: 5,
          backgroundColor: P5Colors.surface,
          valueColor: const AlwaysStoppedAnimation(P5Colors.red),
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 84),
            children: [
              // Quick-Add Card
              P5Card(
                padding: const EdgeInsets.all(10),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: P5NudgeBox(
                            nudged: _inputNudged,
                            child: TextField(
                              controller: _inputCtrl,
                              style: P5Text.body(14, color: P5Colors.white),
                              decoration: const InputDecoration(hintText: 'Add a task...'),
                              onSubmitted: (_) => _addTask(),
                            ),
                          ),
                        ),
                        const SizedBox(width: 6),
                        P5Button(
                          label: 'ADD',
                          icon: Icons.add,
                          variant: P5BtnVariant.primary,
                          height: 40,
                          fontSize: 12,
                          onTap: _addTask,
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        P5Button(
                          label: _newDue != null
                              ? 'DUE ${_newDue!.hour.toString().padLeft(2, '0')}:${_newDue!.minute.toString().padLeft(2, '0')}'
                              : 'DUE',
                          icon: Icons.schedule,
                          active: _newDue != null || _showDueBar,
                          activeVariant: P5BtnVariant.gold,
                          height: 28,
                          fontSize: 10.5,
                          onTap: () => setState(() => _showDueBar = !_showDueBar),
                        ),
                        const SizedBox(width: 6),
                        P5Button(
                          label: 'HIGH',
                          icon: Icons.bolt,
                          active: _newHigh,
                          activeVariant: P5BtnVariant.gold,
                          height: 28,
                          fontSize: 10.5,
                          onTap: () => setState(() => _newHigh = !_newHigh),
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: SingleChildScrollView(
                            scrollDirection: Axis.horizontal,
                            child: Row(
                              children: [
                                for (final sk in kSocialStatKeys) ...[
                                  P5Button(
                                    label: kSocialStats[sk]!.short,
                                    active: _newStat == sk,
                                    activeVariant: P5BtnVariant.gold,
                                    height: 26,
                                    fontSize: 10,
                                    padding: const EdgeInsets.symmetric(horizontal: 7),
                                    onTap: () => setState(() => _newStat = sk),
                                  ),
                                  const SizedBox(width: 4),
                                ],
                              ],
                            ),
                          ),
                        ),
                      ],
                    ),
                    if (_showDueBar) ...[
                      const SizedBox(height: 8),
                      Wrap(
                        spacing: 6,
                        runSpacing: 6,
                        children: [
                          P5Button(label: '+1h', height: 26, fontSize: 10.5, onTap: () => _applyDuePreset('1h')),
                          P5Button(label: '+3h', height: 26, fontSize: 10.5, onTap: () => _applyDuePreset('3h')),
                          P5Button(
                              label: '23:59',
                              variant: P5BtnVariant.gold,
                              height: 26,
                              fontSize: 10.5,
                              onTap: () => _applyDuePreset('eod')),
                          P5Button(
                              label: 'Tomorrow 9:00',
                              height: 26,
                              fontSize: 10.5,
                              onTap: () => _applyDuePreset('tmrw')),
                          if (_newDue != null)
                            P5Button(
                              label: 'CLEAR',
                              variant: P5BtnVariant.danger,
                              height: 26,
                              fontSize: 10.5,
                              onTap: () => setState(() => _newDue = null),
                            ),
                        ],
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(height: 10),

              // Filter Chips + Search
              Row(
                children: [
                  Expanded(
                    child: SingleChildScrollView(
                      scrollDirection: Axis.horizontal,
                      child: Row(
                        children: [
                          for (final f in const [
                            ('ALL', 'all'),
                            ('PENDING', 'todo'),
                            ('DUE', 'due'),
                            ('HIGH', 'high'),
                            ('DONE', 'done'),
                          ]) ...[
                            P5Button(
                              label: f.$1,
                              active: _filter == f.$2,
                              height: 28,
                              fontSize: 10.5,
                              padding: const EdgeInsets.symmetric(horizontal: 9),
                              onTap: () => setState(() => _filter = f.$2),
                            ),
                            const SizedBox(width: 5),
                          ],
                        ],
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 7),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _searchCtrl,
                      style: P5Text.body(13.5, color: P5Colors.white),
                      decoration: InputDecoration(
                        hintText: 'Search tasks...',
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
                  ),
                  const SizedBox(width: 7),
                  P5Button(
                    label: doneCnt > 0 ? 'CLEAR DONE ($doneCnt)' : 'CLEAR DONE',
                    variant: P5BtnVariant.soft,
                    height: 38,
                    fontSize: 10.5,
                    onTap: doneCnt == 0
                        ? null
                        : () async {
                            final cleared = await app.tasks.clearDone();
                            if (cleared.isNotEmpty) {
                              _triggerUndo('Cleared ${cleared.length} completed tasks', cleared);
                              app.notify('Cleared ${cleared.length} completed tasks', tone: P5ToastTone.info);
                            }
                          },
                  ),
                ],
              ),
              const SizedBox(height: 10),

              if (shown.isEmpty)
                P5Card(
                  padding: const EdgeInsets.symmetric(vertical: 28, horizontal: 16),
                  child: Center(
                    child: Text(
                      all.isEmpty ? 'No tasks yet.\nAdd one above and hit ADD!' : 'No tasks match this filter.',
                      textAlign: TextAlign.center,
                      style: P5Text.body(13.5, color: P5Colors.muted),
                    ),
                  ),
                )
              else
                for (final t in shown) _buildTaskCard(t, now),

              if (_undoItems.isNotEmpty)
                P5UndoBar(
                  message: _undoMsg,
                  onUndo: () async {
                    final list = List<Map<String, dynamic>>.from(_undoItems);
                    setState(() => _undoItems = []);
                    await app.tasks.restore(list);
                    app.notify('Restored deleted task(s)', tone: P5ToastTone.ok);
                  },
                ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildTaskCard(Map<String, dynamic> t, DateTime now) {
    final done = t['done'] == true;
    final high = t['priority'] == 'high';
    final sk = TaskStore.statOf(t);
    final dl = parseIsoDate(t['deadline']?.toString());
    final overdue = !done && dl != null && !dl.isAfter(now);

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      decoration: BoxDecoration(
        color: done ? P5Colors.card : P5Colors.surface,
        borderRadius: BorderRadius.circular(9),
        border: Border.all(
          color: overdue ? P5Colors.red : (high && !done ? P5Colors.goldBorder : P5Colors.border),
          width: overdue ? 1.5 : 1.0,
        ),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
        child: Row(
          children: [
            GestureDetector(
              onTap: () async {
                await widget.app.toggleTaskDone(t['id'].toString(), !done);
                setState(() {});
              },
              child: Container(
                width: 24,
                height: 24,
                decoration: BoxDecoration(
                  color: done ? P5Colors.red : Colors.transparent,
                  borderRadius: BorderRadius.circular(5),
                  border: Border.all(color: done ? P5Colors.white : P5Colors.muted, width: 1.8),
                ),
                child: done ? const Icon(Icons.check, size: 17, color: P5Colors.white) : null,
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    t['title'].toString(),
                    style: P5Text.body(
                      14.5,
                      color: done ? P5Colors.muted : P5Colors.white,
                      bold: !done,
                    ).copyWith(decoration: done ? TextDecoration.lineThrough : null),
                  ),
                  const SizedBox(height: 4),
                  Wrap(
                    spacing: 5,
                    runSpacing: 4,
                    children: [
                      if (high && !done) _pill('HIGH', P5Colors.redBg, P5Colors.redFg),
                      _pill(kSocialStats[sk]!.short, P5Colors.goldBg, P5Colors.goldFg),
                      _pill(done ? 'DONE' : 'PENDING', done ? P5Colors.mintBg : const Color(0xFF252534),
                          done ? P5Colors.mint : P5Colors.goldFg),
                      if (dl != null && !done)
                        _pill(
                          '${overdue ? "OVERDUE " : "DUE "}${dl.day}/${dl.month} ${dl.hour.toString().padLeft(2, '0')}:${dl.minute.toString().padLeft(2, '0')}',
                          overdue ? P5Colors.red : P5Colors.goldBg,
                          overdue ? P5Colors.white : P5Colors.gold,
                        ),
                    ],
                  ),
                ],
              ),
            ),
            IconButton(
              icon: Icon(Icons.bolt, size: 18, color: high ? P5Colors.gold : P5Colors.dim),
              tooltip: 'Toggle High Priority',
              onPressed: () async {
                await widget.app.tasks.setPriority(t['id'].toString(), high ? 'normal' : 'high');
                widget.app.notify('Updated task priority', tone: P5ToastTone.gold, ms: 1500);
                setState(() {});
              },
            ),
            IconButton(
              icon: const Icon(Icons.delete_outline, size: 18, color: P5Colors.redFg),
              tooltip: 'Delete Task',
              onPressed: () async {
                final rem = await widget.app.tasks.delete(t['id'].toString());
                if (rem != null) {
                  widget.app.audio.playSfx('close');
                  _triggerUndo("Deleted '${rem['title']}'", [rem]);
                  widget.app.notify("Deleted '${rem['title']}' · Undo available", tone: P5ToastTone.info);
                }
              },
            ),
          ],
        ),
      ),
    );
  }

  Widget _pill(String label, Color bg, Color fg) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
        decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(4)),
        child: Text(label, style: P5Text.display(10, color: fg)),
      );
}
