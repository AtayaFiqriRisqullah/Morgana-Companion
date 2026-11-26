// ignore: deprecated_member_use
import 'package:flutter/material.dart';

import '../core/app_state.dart';
import '../theme/p5_theme.dart';
import '../widgets/p5_widgets.dart';
import 'ask_screen.dart';
import 'events_screen.dart';
import 'finance_screen.dart';
import 'morgana_home_screen.dart';
import 'notes_screen.dart';
import 'settings_screen.dart';
import 'stats_screen.dart';
import 'tasks_screen.dart';
import 'timer_screen.dart';

/// Root Persona 5 Royal Android navigation shell with floating toast overlay
/// and compact 5-slot bottom bar (MORGANA | FINANCE | TASKS | NOTES | MORE).
class HomeShell extends StatefulWidget {
  final MorganaAppState app;

  const HomeShell({super.key, required this.app});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      widget.app.refreshOverlayStatus();
    }
  }

  Widget _buildActiveScreen(MorganaAppState app) {
    switch (app.activeTab) {
      case 'finance':
        return FinanceScreen(app: app);
      case 'tasks':
        return TasksScreen(app: app);
      case 'notes':
        return NotesScreen(app: app);
      case 'events':
        return EventsScreen(app: app);
      case 'stats':
        return StatsScreen(app: app);
      case 'timer':
        return TimerScreen(app: app);
      case 'ask':
        return AskScreen(app: app);
      case 'settings':
        return SettingsScreen(app: app);
      case 'home':
      default:
        return MorganaHomeScreen(app: app);
    }
  }

  void _openMoreSheet(BuildContext context, MorganaAppState app) {
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: P5Colors.panel,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
        side: BorderSide(color: P5Colors.red, width: 1.8),
      ),
      builder: (ctx) {
        return SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(14, 14, 14, 16),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
                      decoration: BoxDecoration(color: P5Colors.red, borderRadius: BorderRadius.circular(4)),
                      child: Text('ASSIST MENU', style: P5Text.display(12, color: P5Colors.white)),
                    ),
                    const Spacer(),
                    Text(
                      app.overlayRunning ? 'BUBBLE LIVE' : 'BUBBLE OFF',
                      style: P5Text.display(11, color: app.overlayRunning ? P5Colors.mint : P5Colors.muted),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                GridView.count(
                  crossAxisCount: 3,
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  mainAxisSpacing: 8,
                  crossAxisSpacing: 8,
                  childAspectRatio: 2.0,
                  children: [
                    for (final item in const [
                      ('events', 'EVENTS', Icons.event_note),
                      ('stats', 'STATS', Icons.star),
                      ('timer', 'TIMER', Icons.timer),
                      ('ask', 'ASK AI', Icons.chat_bubble_outline),
                      ('settings', 'SETTINGS', Icons.settings),
                    ])
                      P5Button(
                        label: item.$2,
                        icon: item.$3,
                        active: app.activeTab == item.$1,
                        activeVariant: P5BtnVariant.primary,
                        height: 44,
                        fontSize: 12,
                        onTap: () {
                          Navigator.pop(ctx);
                          app.audio.playSfx('select');
                          app.navigateTo(item.$1);
                        },
                      ),
                    P5Button(
                      label: app.overlayRunning ? 'HIDE BUBBLE' : 'BUBBLE',
                      icon: Icons.bubble_chart,
                      variant: app.overlayRunning ? P5BtnVariant.mint : P5BtnVariant.gold,
                      height: 44,
                      fontSize: 11.5,
                      onTap: () {
                        Navigator.pop(ctx);
                        app.setFloatingBubbleEnabled(!app.overlayRunning);
                      },
                    ),
                  ],
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: widget.app,
      builder: (context, _) {
        final app = widget.app;
        if (!app.ready) {
          return const Scaffold(
            backgroundColor: P5Colors.bg,
            body: Center(
              child: CircularProgressIndicator(color: P5Colors.red),
            ),
          );
        }

        final isMoreActive = const {'events', 'stats', 'timer', 'ask', 'settings'}.contains(app.activeTab);

        return Scaffold(
          backgroundColor: P5Colors.bg,
          body: SafeArea(
            bottom: false,
            child: Stack(
              children: [
                Positioned.fill(child: _buildActiveScreen(app)),

                // Floating Persona 5 toast overlay above bottom bar
                Positioned(
                  left: 0,
                  right: 0,
                  bottom: 70,
                  child: P5ToastOverlay(
                    message: app.currentToast,
                    onDismiss: app.dismissToast,
                  ),
                ),

                // Compact Persona 5 bottom navigation bar
                Positioned(
                  left: 0,
                  right: 0,
                  bottom: 0,
                  child: Container(
                    decoration: const BoxDecoration(
                      color: Color(0xFF09090F),
                      border: Border(top: BorderSide(color: P5Colors.red, width: 1.8)),
                    ),
                    child: SafeArea(
                      top: false,
                      child: SizedBox(
                        height: 58,
                        child: Row(
                          children: [
                            _navItem(app, 'home', 'MORGANA', Icons.pets, app.activeTab == 'home'),
                            _navItem(app, 'finance', 'FINANCE', Icons.account_balance_wallet, app.activeTab == 'finance'),
                            _navItem(app, 'tasks', 'TASKS', Icons.check_box, app.activeTab == 'tasks'),
                            _navItem(app, 'notes', 'NOTES', Icons.sticky_note_2, app.activeTab == 'notes'),
                            Expanded(
                              child: InkWell(
                                onTap: () => _openMoreSheet(context, app),
                                child: Container(
                                  decoration: BoxDecoration(
                                    color: isMoreActive ? P5Colors.red.withValues(alpha: 0.18) : Colors.transparent,
                                  ),
                                  child: Column(
                                    mainAxisAlignment: MainAxisAlignment.center,
                                    children: [
                                      Icon(
                                        Icons.grid_view_rounded,
                                        size: 20,
                                        color: isMoreActive ? P5Colors.gold : P5Colors.muted,
                                      ),
                                      const SizedBox(height: 2),
                                      Text(
                                        'MORE',
                                        style: P5Text.display(10.5, color: isMoreActive ? P5Colors.gold : P5Colors.muted),
                                      ),
                                    ],
                                  ),
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  Widget _navItem(MorganaAppState app, String tab, String label, IconData icon, bool active) {
    return Expanded(
      child: InkWell(
        onTap: () {
          app.audio.playSfx('hover');
          app.navigateTo(tab);
        },
        child: Container(
          decoration: BoxDecoration(
            color: active ? P5Colors.red.withValues(alpha: 0.22) : Colors.transparent,
            border: active
                ? const Border(top: BorderSide(color: P5Colors.gold, width: 2.2))
                : null,
          ),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(icon, size: 20, color: active ? P5Colors.white : P5Colors.muted),
              const SizedBox(height: 2),
              Text(
                label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: P5Text.display(10.5, color: active ? P5Colors.white : P5Colors.muted),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
