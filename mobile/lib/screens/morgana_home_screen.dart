// ignore: deprecated_member_use
import 'package:flutter/material.dart';

import '../core/app_state.dart';
import '../core/rupiah.dart';
import '../core/storage.dart';
import '../theme/p5_theme.dart';
import '../widgets/morgana_stage.dart';
import '../widgets/p5_widgets.dart';

class MorganaHomeScreen extends StatefulWidget {
  final MorganaAppState app;

  const MorganaHomeScreen({super.key, required this.app});

  @override
  State<MorganaHomeScreen> createState() => _MorganaHomeScreenState();
}

class _MorganaHomeScreenState extends State<MorganaHomeScreen> {
  bool _menuOpen = true;
  final TextEditingController _quickAskCtrl = TextEditingController();

  @override
  void dispose() {
    _quickAskCtrl.dispose();
    super.dispose();
  }

  Future<void> _submitQuickAsk() async {
    final txt = _quickAskCtrl.text.trim();
    if (txt.isEmpty) {
      widget.app.notify('Type a command or ask Morgana something!', tone: P5ToastTone.warn);
      return;
    }
    _quickAskCtrl.clear();
    final res = await widget.app.runQuickCommand(txt);
    widget.app.notify(res.badge, tone: res.accentRed ? P5ToastTone.warn : P5ToastTone.ok, ms: 1600);
  }

  @override
  Widget build(BuildContext context) {
    final app = widget.app;
    final fin = app.activeFinanceData();
    final pendTasks = app.tasks.pending().length;
    final totals = app.stats.totals();
    final conf = overallConfidantLevel(totals);
    final hSum = app.habits.todaySummary();

    return Column(
      children: [
        P5Header(
          title: 'MORGANA',
          onAvatarTap: app.tapMorgana,
          actions: [
            P5Button(
              label: app.sleepMode ? 'AWAKE' : 'SLEEP',
              icon: app.sleepMode ? Icons.wb_sunny_outlined : Icons.bedtime_outlined,
              variant: app.sleepMode ? P5BtnVariant.gold : P5BtnVariant.soft,
              height: 30,
              fontSize: 10.5,
              onTap: () => app.setSleepMode(!app.sleepMode),
            ),
            const SizedBox(width: 6),
            P5Button(
              label: 'BUBBLE',
              icon: Icons.bubble_chart,
              variant: app.overlayRunning ? P5BtnVariant.mint : P5BtnVariant.soft,
              height: 30,
              fontSize: 10.5,
              onTap: () => app.setFloatingBubbleEnabled(!app.overlayRunning),
            ),
            const SizedBox(width: 6),
            P5Button(
              label: '',
              icon: Icons.settings,
              variant: P5BtnVariant.soft,
              height: 30,
              fontSize: 14,
              onTap: () => app.navigateTo('settings'),
            ),
          ],
        ),
        Expanded(
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 82),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                // Interactive Joker Assist Menu Stage + Morgana Sprite
                MorganaStageWidget(
                  emotion: app.morganaEmotion,
                  talking: app.morganaTalking,
                  asleep: app.sleepMode,
                  menuOpen: _menuOpen,
                  enabledTabs: app.settings.enabledTabs,
                  onTapMorgana: app.tapMorgana,
                  onToggleMenu: () {
                    app.audio.playSfx(_menuOpen ? 'close' : 'open_radial');
                    setState(() => _menuOpen = !_menuOpen);
                  },
                  onSelectTab: (tab) {
                    app.audio.playSfx('select');
                    app.navigateTo(tab);
                  },
                ),
                const SizedBox(height: 10),

                // Persona 5 Slanted Dialogue Speech Box
                GestureDetector(
                  onTap: app.tapMorgana,
                  child: Container(
                    padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
                    decoration: BoxDecoration(
                      color: const Color(0xFF0E0E15),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(color: app.sleepMode ? P5Colors.gold : P5Colors.white, width: 1.6),
                      boxShadow: [BoxShadow(color: app.sleepMode ? P5Colors.gold : P5Colors.red, offset: const Offset(4, 4))],
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Container(
                              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                              decoration: BoxDecoration(
                                color: P5Colors.red,
                                borderRadius: BorderRadius.circular(4),
                              ),
                              child: Text('MORGANA', style: P5Text.heading(12)),
                            ),
                            const SizedBox(width: 8),
                            Container(
                              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                              decoration: BoxDecoration(
                                color: P5Colors.goldBg,
                                borderRadius: BorderRadius.circular(4),
                                border: Border.all(color: P5Colors.goldBorder),
                              ),
                              child: Text(
                                app.sleepMode && !app.morganaTalking ? 'SLEEP · ASLEEP' : app.morganaEmotion.toUpperCase(),
                                style: P5Text.display(10, color: P5Colors.gold),
                              ),
                            ),
                            const Spacer(),
                            Text(app.sleepMode ? 'USER ASLEEP' : 'TAP TO CHAT', style: P5Text.display(10, color: P5Colors.dim)),
                          ],
                        ),
                        const SizedBox(height: 7),
                        Text(
                          app.morganaSpeech,
                          style: P5Text.body(14.5, color: P5Colors.white, bold: true),
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 14),

                // Quick Command / Ask Spotlight bar right from Home
                Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _quickAskCtrl,
                        style: P5Text.body(14, color: P5Colors.white),
                        decoration: const InputDecoration(
                          hintText: 'Quick command: saldo · task ... · sleep · bangun',
                          prefixIcon: Icon(Icons.bolt, color: P5Colors.gold, size: 18),
                        ),
                        onSubmitted: (_) => _submitQuickAsk(),
                      ),
                    ),
                    const SizedBox(width: 8),
                    P5Button(
                      label: 'SEND',
                      variant: P5BtnVariant.primary,
                      height: 42,
                      fontSize: 12,
                      onTap: _submitQuickAsk,
                    ),
                  ],
                ),
                const SizedBox(height: 14),

                // Live 2x2 Companion Overview KPI Cards
                Row(
                  children: [
                    Expanded(
                      child: P5KpiCard(
                        title: app.isSheetMode ? 'BALANCE (SHEET)' : 'TOTAL BALANCE',
                        value: formatRupiah(fin.balances['Total'] ?? 0),
                        subtitle: 'Today: -${formatRupiah((fin.summary['today_expense'] as num?) ?? 0)}',
                        accent: P5Colors.goldFg,
                        tint: const Color(0xFF241E0F),
                        actionHint: 'OPEN ▸',
                        onTap: () => app.navigateTo('finance'),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: P5KpiCard(
                        title: 'TASKS LEFT',
                        value: '$pendTasks PENDING',
                        subtitle: '${app.tasks.all().length - pendTasks} completed',
                        accent: P5Colors.redFg,
                        tint: const Color(0xFF281116),
                        actionHint: 'OPEN ▸',
                        onTap: () => app.navigateTo('tasks'),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Row(
                  children: [
                    Expanded(
                      child: P5KpiCard(
                        title: 'CONFIDANT STATS',
                        value: 'LV ${conf.total} / ${conf.maxBase}',
                        subtitle: 'Habits ${hSum.done}/${hSum.total} · +${app.stats.todayPoints()} pts',
                        accent: P5Colors.mint,
                        tint: const Color(0xFF11241C),
                        actionHint: 'OPEN ▸',
                        onTap: () => app.navigateTo('stats'),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: P5KpiCard(
                        title: 'FOCUS & TIMERS',
                        value: app.focusRunning
                            ? MorganaAppState.formatHms(app.focusLeftSec)
                            : '${app.manualTimers.length} ACTIVE',
                        subtitle: app.focusRunning
                            ? (app.focusPhase == 'rest' ? 'Break phase' : 'Focus running')
                            : 'Tap to start sprint',
                        accent: P5Colors.white,
                        tint: const Color(0xFF1F1F2E),
                        actionHint: 'OPEN ▸',
                        onTap: () => app.navigateTo('timer'),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}
