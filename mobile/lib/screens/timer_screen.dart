// ignore: deprecated_member_use
import 'package:flutter/material.dart';

import '../core/app_state.dart';
import '../core/storage.dart';
import '../theme/p5_theme.dart';
import '../widgets/p5_widgets.dart';

/// Persona 5 Focus Sprint Pomodoro & Manual Countdown Timers screen.
class TimerScreen extends StatefulWidget {
  final MorganaAppState app;

  const TimerScreen({super.key, required this.app});

  @override
  State<TimerScreen> createState() => _TimerScreenState();
}

class _TimerScreenState extends State<TimerScreen> {
  final _labelCtrl = TextEditingController();
  final _hCtrl = TextEditingController(text: '0');
  final _mCtrl = TextEditingController(text: '10');
  final _sCtrl = TextEditingController(text: '0');

  late int _focusMin;
  late int _restMin;

  @override
  void initState() {
    super.initState();
    _focusMin = (widget.app.settings['focus_min'] as num?)?.toInt() ?? 25;
    _restMin = (widget.app.settings['rest_min'] as num?)?.toInt() ?? 5;
  }

  @override
  void dispose() {
    _labelCtrl.dispose();
    _hCtrl.dispose();
    _mCtrl.dispose();
    _sCtrl.dispose();
    super.dispose();
  }

  void _startCustomTimer() {
    final h = int.tryParse(_hCtrl.text.trim()) ?? 0;
    final m = int.tryParse(_mCtrl.text.trim()) ?? 0;
    final s = int.tryParse(_sCtrl.text.trim()) ?? 0;
    final total = (h.clamp(0, 23) * 3600) + (m.clamp(0, 59) * 60) + s.clamp(0, 59);
    if (total <= 0) {
      widget.app.audio.playSfx('cancel');
      widget.app.notify('Set a timer duration greater than 0s!', tone: P5ToastTone.warn);
      return;
    }
    widget.app.addManualTimer(total, _labelCtrl.text.trim());
    _labelCtrl.clear();
  }

  void _startCountdownToMidnight() {
    final now = DateTime.now();
    final target = DateTime(now.year, now.month, now.day, 23, 59, 0);
    final diff = target.difference(now).inSeconds;
    if (diff <= 0) {
      widget.app.notify("It's already past 23:59!", tone: P5ToastTone.warn);
      return;
    }
    widget.app.addManualTimer(diff, 'Until 23:59');
  }

  @override
  Widget build(BuildContext context) {
    final app = widget.app;
    final isRest = app.focusPhase == 'rest';
    final progress = app.focusTotalSec > 0
        ? (1.0 - (app.focusLeftSec / app.focusTotalSec)).clamp(0.0, 1.0)
        : 0.0;
    final focusStat = (app.settings['focus_stat'] ?? 'knowledge').toString();

    return Column(
      children: [
        P5Header(
          title: 'Timer',
          onAvatarTap: () => app.navigateTo('home'),
          actions: [
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
              decoration: BoxDecoration(
                color: P5Colors.surface,
                borderRadius: BorderRadius.circular(6),
                border: Border.all(color: P5Colors.border),
              ),
              child: Text('DONE: ${app.focusCompleted}', style: P5Text.display(11.5, color: P5Colors.gold)),
            ),
          ],
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 84),
            children: [
              // Hero Focus Clock card
              P5Card(
                topColor: app.focusRunning
                    ? (isRest ? const Color(0xFF1F1B0C) : const Color(0xFF2E0A12))
                    : P5Colors.cardTop,
                borderColor: app.focusRunning
                    ? (isRest ? P5Colors.gold : P5Colors.red)
                    : P5Colors.border,
                borderWidth: app.focusRunning ? 1.8 : 1.1,
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 16),
                child: Column(
                  children: [
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
                      decoration: BoxDecoration(
                        color: app.focusRunning
                            ? (isRest ? P5Colors.gold : P5Colors.red)
                            : P5Colors.surface,
                        borderRadius: BorderRadius.circular(4),
                      ),
                      child: Text(
                        app.focusRunning
                            ? (isRest ? 'BREAK TIME' : 'FOCUS IN PROGRESS')
                            : 'FOCUS SPRINT READY',
                        style: P5Text.display(
                          11.5,
                          color: (app.focusRunning && isRest) ? P5Colors.black : P5Colors.white,
                        ),
                      ),
                    ),
                    const SizedBox(height: 10),
                    Text(
                      MorganaAppState.formatHms(app.focusRunning ? app.focusLeftSec : _focusMin * 60),
                      style: P5Text.heading(56, color: isRest ? P5Colors.gold : P5Colors.white),
                    ),
                    const SizedBox(height: 10),
                    ClipRRect(
                      borderRadius: BorderRadius.circular(4),
                      child: LinearProgressIndicator(
                        value: app.focusRunning ? progress : 0.0,
                        minHeight: 7,
                        backgroundColor: P5Colors.black,
                        valueColor: AlwaysStoppedAnimation<Color>(isRest ? P5Colors.gold : P5Colors.red),
                      ),
                    ),
                    const SizedBox(height: 14),
                    Row(
                      children: [
                        Expanded(
                          child: P5Button(
                            label: app.focusRunning ? 'STOP FOCUS' : 'START FOCUS',
                            icon: app.focusRunning ? Icons.stop : Icons.play_arrow,
                            variant: app.focusRunning ? P5BtnVariant.danger : P5BtnVariant.primary,
                            height: 42,
                            fontSize: 14,
                            expand: true,
                            onTap: () {
                              if (app.focusRunning) {
                                app.stopFocus();
                              } else {
                                app.startFocus(_focusMin, _restMin);
                              }
                            },
                          ),
                        ),
                        const SizedBox(width: 8),
                        P5Button(
                          label: '5M BREAK',
                          variant: P5BtnVariant.gold,
                          height: 42,
                          fontSize: 12.5,
                          onTap: () => app.addManualTimer(5 * 60, '5m Break'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 10),

              // Focus & Break duration + Stat reward card
              P5Card(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('SPRINT CONFIGURATION', style: P5Text.display(12, color: P5Colors.gold)),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        Expanded(
                          child: _stepperBox(
                            label: 'FOCUS (MIN)',
                            value: _focusMin,
                            onMinus: () => setState(() => _focusMin = (_focusMin - 5).clamp(5, 120)),
                            onPlus: () => setState(() => _focusMin = (_focusMin + 5).clamp(5, 120)),
                          ),
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: _stepperBox(
                            label: 'BREAK (MIN)',
                            value: _restMin,
                            onMinus: () => setState(() => _restMin = (_restMin - 1).clamp(1, 30)),
                            onPlus: () => setState(() => _restMin = (_restMin + 1).clamp(1, 30)),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 10),
                    Text('SOCIAL STAT REWARD (+2 PTS)', style: P5Text.display(11, color: P5Colors.muted)),
                    const SizedBox(height: 6),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        for (final k in kSocialStatKeys)
                          P5Button(
                            label: kSocialStats[k]!.short,
                            active: focusStat == k,
                            activeVariant: P5BtnVariant.gold,
                            height: 28,
                            fontSize: 11,
                            onTap: () async {
                              await app.settings.updateAll({'focus_stat': k});
                              app.notify('Focus rewards ${kSocialStats[k]!.label}', tone: P5ToastTone.info, ms: 1600);
                              setState(() {});
                            },
                          ),
                        P5Button(
                          label: 'OFF',
                          active: focusStat == 'none',
                          activeVariant: P5BtnVariant.danger,
                          height: 28,
                          fontSize: 11,
                          onTap: () async {
                            await app.settings.updateAll({'focus_stat': 'none'});
                            app.notify('Focus stat reward disabled', tone: P5ToastTone.info, ms: 1600);
                            setState(() {});
                          },
                        ),
                      ],
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 10),

              // Manual Timers section
              P5Card(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('MANUAL TIMERS & PRESETS', style: P5Text.display(12, color: P5Colors.gold)),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        for (final p in const [
                          (5 * 60, '5M'),
                          (10 * 60, '10M'),
                          (15 * 60, '15M'),
                          (30 * 60, '30M'),
                          (60 * 60, '1H'),
                        ])
                          P5Button(
                            label: p.$2,
                            variant: P5BtnVariant.soft,
                            height: 32,
                            fontSize: 12,
                            onTap: () => app.addManualTimer(p.$1, '${p.$2} Timer'),
                          ),
                        P5Button(
                          label: 'UNTIL 23:59',
                          variant: P5BtnVariant.gold,
                          height: 32,
                          fontSize: 11.5,
                          onTap: _startCountdownToMidnight,
                        ),
                      ],
                    ),
                    const SizedBox(height: 10),
                    TextField(
                      controller: _labelCtrl,
                      decoration: const InputDecoration(hintText: 'Timer label (e.g. Laundry, Ramen, Stretch)...'),
                    ),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        Expanded(
                          child: TextField(
                            controller: _hCtrl,
                            keyboardType: TextInputType.number,
                            decoration: const InputDecoration(labelText: 'H'),
                          ),
                        ),
                        const SizedBox(width: 6),
                        Expanded(
                          child: TextField(
                            controller: _mCtrl,
                            keyboardType: TextInputType.number,
                            decoration: const InputDecoration(labelText: 'M'),
                          ),
                        ),
                        const SizedBox(width: 6),
                        Expanded(
                          child: TextField(
                            controller: _sCtrl,
                            keyboardType: TextInputType.number,
                            decoration: const InputDecoration(labelText: 'S'),
                          ),
                        ),
                        const SizedBox(width: 8),
                        P5Button(
                          label: 'START',
                          variant: P5BtnVariant.primary,
                          height: 44,
                          onTap: _startCustomTimer,
                        ),
                      ],
                    ),
                  ],
                ),
              ),

              if (app.manualTimers.isNotEmpty) ...[
                const SizedBox(height: 10),
                Text('ACTIVE TIMERS (${app.manualTimers.length})', style: P5Text.display(12, color: P5Colors.gold)),
                const SizedBox(height: 6),
                for (final tm in app.manualTimers) _buildManualTimerTile(tm),
              ],
            ],
          ),
        ),
      ],
    );
  }

  Widget _stepperBox({
    required String label,
    required int value,
    required VoidCallback onMinus,
    required VoidCallback onPlus,
  }) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 7),
      decoration: BoxDecoration(
        color: P5Colors.surface,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: P5Colors.border),
      ),
      child: Column(
        children: [
          Text(label, style: P5Text.display(10, color: P5Colors.muted)),
          const SizedBox(height: 4),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              P5Button(label: '-', variant: P5BtnVariant.soft, height: 28, onTap: onMinus),
              Text('$value', style: P5Text.display(17, color: P5Colors.white)),
              P5Button(label: '+', variant: P5BtnVariant.soft, height: 28, onTap: onPlus),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildManualTimerTile(ManualTimerItem tm) {
    final pct = tm.totalSeconds > 0
        ? (1.0 - (tm.remainingSeconds / tm.totalSeconds)).clamp(0.0, 1.0)
        : 0.0;
    return Padding(
      padding: const EdgeInsets.only(bottom: 7),
      child: P5Card(
        padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 9),
        borderColor: P5Colors.goldBorder,
        child: Column(
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(tm.label, style: P5Text.body(13.5, color: P5Colors.white, bold: true)),
                ),
                Text(
                  MorganaAppState.formatHms(tm.remainingSeconds),
                  style: P5Text.display(18, color: P5Colors.gold),
                ),
                const SizedBox(width: 8),
                P5Button(
                  label: 'CANCEL',
                  variant: P5BtnVariant.danger,
                  height: 28,
                  fontSize: 10.5,
                  onTap: () => widget.app.cancelManualTimer(tm.id),
                ),
              ],
            ),
            const SizedBox(height: 6),
            ClipRRect(
              borderRadius: BorderRadius.circular(3),
              child: LinearProgressIndicator(
                value: pct,
                minHeight: 4,
                backgroundColor: P5Colors.black,
                valueColor: const AlwaysStoppedAnimation<Color>(P5Colors.gold),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
