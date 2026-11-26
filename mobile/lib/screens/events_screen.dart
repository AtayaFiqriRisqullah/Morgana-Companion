import 'dart:async';

// ignore: deprecated_member_use
import 'package:flutter/material.dart';

import '../core/app_state.dart';
import '../core/storage.dart';
import '../theme/p5_theme.dart';
import '../widgets/p5_widgets.dart';

/// 7-day weekly timeline merging local events and pending Task deadlines.
class EventsScreen extends StatefulWidget {
  final MorganaAppState app;

  const EventsScreen({super.key, required this.app});

  @override
  State<EventsScreen> createState() => _EventsScreenState();
}

class _EventsScreenState extends State<EventsScreen> {
  final _titleCtrl = TextEditingController();
  final _locCtrl = TextEditingController();

  bool _formOpen = false;
  String? _editingId;
  bool _titleNudged = false;
  DateTime _selectedDate = DateTime.now();
  TimeOfDay _selectedTime = const TimeOfDay(hour: 10, minute: 0);
  bool _allDay = false;
  int _dayFilterIdx = -1; // -1 = ALL 7D, 0..6 = specific day offset

  Map<String, dynamic>? _undoEvent;
  Timer? _undoTimer;
  Timer? _nudgeTimer;

  static const _weekdays = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'];
  static const _months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

  @override
  void dispose() {
    _undoTimer?.cancel();
    _nudgeTimer?.cancel();
    _titleCtrl.dispose();
    _locCtrl.dispose();
    super.dispose();
  }

  void _openAddForm({DateTime? presetDate}) {
    final base = presetDate ?? DateTime.now();
    setState(() {
      _editingId = null;
      _titleCtrl.clear();
      _locCtrl.clear();
      _selectedDate = DateTime(base.year, base.month, base.day);
      _selectedTime = TimeOfDay(hour: (DateTime.now().hour + 1) % 24, minute: 0);
      _allDay = false;
      _formOpen = true;
    });
  }

  void _openEditForm(Map<String, dynamic> ev) {
    final dt = parseIsoDate((ev['start'] ?? '').toString()) ?? DateTime.now();
    setState(() {
      _editingId = ev['id']?.toString();
      _titleCtrl.text = (ev['title'] ?? '').toString();
      _locCtrl.text = (ev['location'] ?? '').toString();
      _selectedDate = DateTime(dt.year, dt.month, dt.day);
      _selectedTime = TimeOfDay(hour: dt.hour, minute: dt.minute);
      _allDay = ev['all_day'] == true;
      _formOpen = true;
    });
  }

  Future<void> _saveEvent() async {
    final title = _titleCtrl.text.trim();
    if (title.isEmpty) {
      widget.app.audio.playSfx('cancel');
      widget.app.notify('Enter an event title first!', tone: P5ToastTone.warn);
      setState(() => _titleNudged = true);
      _nudgeTimer?.cancel();
      _nudgeTimer = Timer(const Duration(milliseconds: 450), () {
        if (mounted) setState(() => _titleNudged = false);
      });
      return;
    }

    final start = DateTime(
      _selectedDate.year,
      _selectedDate.month,
      _selectedDate.day,
      _allDay ? 0 : _selectedTime.hour,
      _allDay ? 0 : _selectedTime.minute,
    );
    final loc = _locCtrl.text.trim();

    if (_editingId != null) {
      await widget.app.events.update(_editingId!, title, start, allDay: _allDay, location: loc);
      widget.app.notify("Updated '$title'", tone: P5ToastTone.gold);
    } else {
      await widget.app.events.add(title, start, allDay: _allDay, location: loc);
      widget.app.notify("Scheduled '$title'", tone: P5ToastTone.ok);
    }
    widget.app.audio.playSfx('select');
    setState(() {
      _formOpen = false;
      _editingId = null;
      _titleCtrl.clear();
      _locCtrl.clear();
    });
  }

  Future<void> _deleteEvent(Map<String, dynamic> ev) async {
    final removed = await widget.app.events.delete(ev['id'].toString());
    if (removed == null) return;
    widget.app.audio.playSfx('close');
    _undoTimer?.cancel();
    setState(() => _undoEvent = removed);
    _undoTimer = Timer(const Duration(seconds: 5), () {
      if (mounted) setState(() => _undoEvent = null);
    });
    widget.app.notify("Deleted '${removed['title']}'", tone: P5ToastTone.info);
  }

  Future<void> _restoreUndo() async {
    final ev = _undoEvent;
    if (ev == null) return;
    _undoTimer?.cancel();
    await widget.app.events.restore(ev);
    widget.app.audio.playSfx('select');
    widget.app.notify("Restored '${ev['title']}'", tone: P5ToastTone.ok);
    setState(() => _undoEvent = null);
  }

  Future<void> _pickDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _selectedDate,
      firstDate: DateTime.now().subtract(const Duration(days: 30)),
      lastDate: DateTime.now().add(const Duration(days: 365)),
    );
    if (picked != null && mounted) {
      setState(() => _selectedDate = picked);
    }
  }

  Future<void> _pickTime() async {
    final picked = await showTimePicker(context: context, initialTime: _selectedTime);
    if (picked != null && mounted) {
      setState(() {
        _allDay = false;
        _selectedTime = picked;
      });
    }
  }

  String _dayKey(DateTime dt) =>
      '${dt.year.toString().padLeft(4, '0')}-${dt.month.toString().padLeft(2, '0')}-${dt.day.toString().padLeft(2, '0')}';

  @override
  Widget build(BuildContext context) {
    final app = widget.app;
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final days = List<DateTime>.generate(7, (i) => today.add(Duration(days: i)));
    final dayKeys = days.map(_dayKey).toList();

    // Build unified timeline items
    final itemsByDay = <String, List<Map<String, dynamic>>>{for (final k in dayKeys) k: []};
    final beyondItems = <Map<String, dynamic>>[];

    for (final ev in app.events.all()) {
      final dt = parseIsoDate((ev['start'] ?? '').toString());
      if (dt == null) continue;
      final dk = _dayKey(dt);
      final wrapped = {...ev, '_kind': 'event', '_dt': dt};
      if (itemsByDay.containsKey(dk)) {
        itemsByDay[dk]!.add(wrapped);
      } else if (dt.isAfter(today.add(const Duration(days: 6)))) {
        beyondItems.add(wrapped);
      }
    }

    var deadlineCount = 0;
    for (final t in app.tasks.pending()) {
      final dl = parseIsoDate((t['deadline'] ?? '').toString());
      if (dl == null) continue;
      final dk = _dayKey(dl);
      if (itemsByDay.containsKey(dk)) {
        deadlineCount++;
        itemsByDay[dk]!.add({
          'id': t['id'],
          'title': t['title'],
          'priority': t['priority'],
          'stat': TaskStore.statOf(t),
          '_kind': 'deadline',
          '_dt': dl,
        });
      }
    }

    for (final list in itemsByDay.values) {
      list.sort((a, b) => (a['_dt'] as DateTime).compareTo(b['_dt'] as DateTime));
    }
    beyondItems.sort((a, b) => (a['_dt'] as DateTime).compareTo(b['_dt'] as DateTime));

    final todayCount = itemsByDay[dayKeys.first]?.length ?? 0;
    final weekTotal = itemsByDay.values.fold<int>(0, (s, l) => s + l.length);

    final visibleDays = _dayFilterIdx >= 0 && _dayFilterIdx < 7 ? [days[_dayFilterIdx]] : days;

    return Column(
      children: [
        P5Header(
          title: 'Events',
          onAvatarTap: () => app.navigateTo('home'),
          actions: [
            P5Button(
              label: _formOpen ? 'CLOSE' : '+ EVENT',
              variant: _formOpen ? P5BtnVariant.soft : P5BtnVariant.primary,
              height: 32,
              fontSize: 12,
              onTap: () {
                if (_formOpen) {
                  setState(() {
                    _formOpen = false;
                    _editingId = null;
                  });
                } else {
                  _openAddForm(
                    presetDate: _dayFilterIdx >= 0 ? days[_dayFilterIdx] : today,
                  );
                }
              },
            ),
          ],
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 84),
            children: [
              // Morgana briefing banner
              P5Card(
                topColor: const Color(0xFF280D15),
                borderColor: P5Colors.red,
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                child: Row(
                  children: [
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                      decoration: BoxDecoration(
                        color: P5Colors.red,
                        borderRadius: BorderRadius.circular(4),
                      ),
                      child: Text('7-DAY BRIEF', style: P5Text.display(11, color: P5Colors.white)),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        todayCount == 0 && weekTotal == 0
                            ? 'Your next 7 days are clear! Tap + EVENT to plan ahead.'
                            : 'Today: $todayCount scheduled · 7-day total: $weekTotal ($deadlineCount task deadlines)',
                        style: P5Text.body(12.5, color: P5Colors.white, bold: true),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 10),

              // 7-day interactive filter strip
              SizedBox(
                height: 54,
                child: ListView.separated(
                  scrollDirection: Axis.horizontal,
                  itemCount: 8,
                  separatorBuilder: (_, _) => const SizedBox(width: 6),
                  itemBuilder: (context, idx) {
                    if (idx == 0) {
                      final active = _dayFilterIdx == -1;
                      return GestureDetector(
                        onTap: () => setState(() => _dayFilterIdx = -1),
                        child: Container(
                          width: 64,
                          alignment: Alignment.center,
                          decoration: BoxDecoration(
                            color: active ? P5Colors.red : P5Colors.surface,
                            borderRadius: BorderRadius.circular(8),
                            border: Border.all(color: active ? P5Colors.white : P5Colors.border),
                          ),
                          child: Column(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Text('ALL', style: P5Text.display(12, color: P5Colors.white)),
                              Text('$weekTotal', style: P5Text.display(14, color: active ? P5Colors.gold : P5Colors.muted)),
                            ],
                          ),
                        ),
                      );
                    }
                    final dIdx = idx - 1;
                    final d = days[dIdx];
                    final dk = dayKeys[dIdx];
                    final cnt = itemsByDay[dk]?.length ?? 0;
                    final active = _dayFilterIdx == dIdx;
                    return GestureDetector(
                      onTap: () => setState(() => _dayFilterIdx = active ? -1 : dIdx),
                      child: Container(
                        width: 58,
                        alignment: Alignment.center,
                        decoration: BoxDecoration(
                          color: active
                              ? P5Colors.red
                              : (dIdx == 0 ? P5Colors.goldBg : P5Colors.surface),
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(
                            color: active
                                ? P5Colors.white
                                : (dIdx == 0 ? P5Colors.goldBorder : P5Colors.border),
                          ),
                        ),
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Text(
                              dIdx == 0 ? 'TODAY' : _weekdays[d.weekday - 1],
                              style: P5Text.display(10, color: active ? P5Colors.white : P5Colors.muted),
                            ),
                            Text('${d.day}', style: P5Text.display(15, color: P5Colors.white)),
                            if (cnt > 0)
                              Container(
                                width: 16,
                                height: 3,
                                margin: const EdgeInsets.only(top: 2),
                                decoration: BoxDecoration(
                                  color: active ? P5Colors.white : P5Colors.gold,
                                  borderRadius: BorderRadius.circular(2),
                                ),
                              ),
                          ],
                        ),
                      ),
                    );
                  },
                ),
              ),

              if (_formOpen) ...[
                const SizedBox(height: 10),
                _buildFormCard(),
              ],

              if (_undoEvent != null)
                P5UndoBar(
                  message: "Deleted '${_undoEvent!['title']}'",
                  onUndo: _restoreUndo,
                ),

              const SizedBox(height: 10),

              // Day timeline groups
              for (final d in visibleDays) _buildDaySection(d, itemsByDay[_dayKey(d)] ?? const [], today),

              if (_dayFilterIdx == -1 && beyondItems.isNotEmpty) ...[
                const SizedBox(height: 8),
                Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: Text('LATER UPCOMING (${beyondItems.length})', style: P5Text.display(12, color: P5Colors.gold)),
                ),
                for (final ev in beyondItems) _buildEventCard(ev, showDate: true),
              ],
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildFormCard() {
    final dateLabel =
        '${_weekdays[_selectedDate.weekday - 1]}, ${_selectedDate.day} ${_months[_selectedDate.month - 1]}';
    final timeLabel = _allDay
        ? 'ALL DAY'
        : '${_selectedTime.hour.toString().padLeft(2, '0')}:${_selectedTime.minute.toString().padLeft(2, '0')}';

    return P5Card(
      borderColor: _editingId != null ? P5Colors.gold : P5Colors.red,
      borderWidth: 1.5,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            _editingId != null ? 'EDIT EVENT' : 'SCHEDULE NEW EVENT',
            style: P5Text.display(13, color: _editingId != null ? P5Colors.gold : P5Colors.red),
          ),
          const SizedBox(height: 8),
          P5NudgeBox(
            nudged: _titleNudged,
            child: TextField(
              controller: _titleCtrl,
              decoration: const InputDecoration(hintText: 'Event title (e.g. Lab Demo, Team Sync)...'),
              textInputAction: TextInputAction.next,
            ),
          ),
          const SizedBox(height: 8),
          TextField(
            controller: _locCtrl,
            decoration: const InputDecoration(hintText: 'Location or note (optional)...'),
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              P5Button(
                label: dateLabel,
                icon: Icons.calendar_today,
                variant: P5BtnVariant.soft,
                height: 32,
                fontSize: 11.5,
                onTap: _pickDate,
              ),
              P5Button(
                label: timeLabel,
                icon: Icons.schedule,
                variant: _allDay ? P5BtnVariant.gold : P5BtnVariant.soft,
                height: 32,
                fontSize: 11.5,
                onTap: _pickTime,
              ),
              P5Button(
                label: 'ALL DAY',
                active: _allDay,
                activeVariant: P5BtnVariant.gold,
                height: 32,
                fontSize: 11,
                onTap: () => setState(() => _allDay = !_allDay),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final preset in const [(9, 0, '09:00'), (13, 0, '13:00'), (18, 0, '18:00'), (20, 0, '20:00')])
                P5Button(
                  label: preset.$3,
                  active: !_allDay && _selectedTime.hour == preset.$1 && _selectedTime.minute == preset.$2,
                  activeVariant: P5BtnVariant.primary,
                  height: 28,
                  fontSize: 11,
                  onTap: () => setState(() {
                    _allDay = false;
                    _selectedTime = TimeOfDay(hour: preset.$1, minute: preset.$2);
                  }),
                ),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: P5Button(
                  label: _editingId != null ? 'UPDATE EVENT' : 'SAVE EVENT',
                  variant: _editingId != null ? P5BtnVariant.gold : P5BtnVariant.primary,
                  height: 38,
                  expand: true,
                  onTap: _saveEvent,
                ),
              ),
              const SizedBox(width: 8),
              P5Button(
                label: 'CANCEL',
                variant: P5BtnVariant.soft,
                height: 38,
                onTap: () => setState(() {
                  _formOpen = false;
                  _editingId = null;
                }),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildDaySection(DateTime day, List<Map<String, dynamic>> items, DateTime today) {
    final diff = day.difference(today).inDays;
    final tag = diff == 0
        ? 'TODAY'
        : (diff == 1 ? 'TOMORROW' : _weekdays[day.weekday - 1]);
    final header = '$tag · ${day.day} ${_months[day.month - 1]}';

    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: diff == 0 ? P5Colors.red : P5Colors.surface,
                  borderRadius: BorderRadius.circular(4),
                  border: Border.all(color: diff == 0 ? P5Colors.white : P5Colors.border),
                ),
                child: Text(header, style: P5Text.display(11.5, color: P5Colors.white)),
              ),
              const SizedBox(width: 8),
              Expanded(child: Container(height: 1, color: P5Colors.border)),
              const SizedBox(width: 6),
              GestureDetector(
                onTap: () => _openAddForm(presetDate: day),
                child: Text('+ ADD', style: P5Text.display(10.5, color: P5Colors.gold)),
              ),
            ],
          ),
          const SizedBox(height: 6),
          if (items.isEmpty)
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              decoration: BoxDecoration(
                color: P5Colors.surface.withValues(alpha: 0.45),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: P5Colors.border.withValues(alpha: 0.5)),
              ),
              child: Text('No events or deadlines.', style: P5Text.body(12, color: P5Colors.muted)),
            )
          else
            for (final item in items) _buildEventCard(item),
        ],
      ),
    );
  }

  Widget _buildEventCard(Map<String, dynamic> item, {bool showDate = false}) {
    final isDeadline = item['_kind'] == 'deadline';
    final dt = item['_dt'] as DateTime;
    final allDay = item['all_day'] == true;
    final timeStr = allDay
        ? 'ALL DAY'
        : '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
    final datePrefix = showDate ? '${dt.day} ${_months[dt.month - 1]} · ' : '';
    final loc = (item['location'] ?? '').toString().trim();

    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: P5Card(
        padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 9),
        borderColor: isDeadline ? P5Colors.red : P5Colors.border,
        child: Row(
          children: [
            Container(
              width: 4,
              height: 36,
              decoration: BoxDecoration(
                color: isDeadline ? P5Colors.red : P5Colors.gold,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                        decoration: BoxDecoration(
                          color: isDeadline ? P5Colors.redBg : P5Colors.goldBg,
                          borderRadius: BorderRadius.circular(4),
                          border: Border.all(color: isDeadline ? P5Colors.red : P5Colors.goldBorder),
                        ),
                        child: Text(
                          isDeadline ? 'TASK DEADLINE' : '$datePrefix$timeStr',
                          style: P5Text.display(10, color: isDeadline ? P5Colors.redFg : P5Colors.gold),
                        ),
                      ),
                      if (isDeadline) ...[
                        const SizedBox(width: 6),
                        Text(timeStr, style: P5Text.body(11, color: P5Colors.muted)),
                      ],
                    ],
                  ),
                  const SizedBox(height: 3),
                  Text(
                    (item['title'] ?? '').toString(),
                    style: P5Text.body(13.5, color: P5Colors.white, bold: true),
                  ),
                  if (loc.isNotEmpty)
                    Text(loc, style: P5Text.body(11.5, color: P5Colors.muted)),
                ],
              ),
            ),
            if (isDeadline)
              P5Button(
                label: 'TASKS',
                variant: P5BtnVariant.danger,
                height: 28,
                fontSize: 10.5,
                onTap: () => widget.app.navigateTo('tasks'),
              )
            else
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  P5Button(
                    label: '',
                    icon: Icons.edit,
                    variant: P5BtnVariant.soft,
                    height: 30,
                    onTap: () => _openEditForm(item),
                  ),
                  const SizedBox(width: 5),
                  P5Button(
                    label: '',
                    icon: Icons.delete_outline,
                    variant: P5BtnVariant.danger,
                    height: 30,
                    onTap: () => _deleteEvent(item),
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}
