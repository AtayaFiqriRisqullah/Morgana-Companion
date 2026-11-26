import 'dart:io';

// ignore: deprecated_member_use
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:morgana_companion/core/app_state.dart';
import 'package:morgana_companion/main.dart';

Future<MorganaAppState> _createTestAppState(WidgetTester tester, Directory dir) async {
  late MorganaAppState app;
  await tester.runAsync(() async {
    app = MorganaAppState();
    await app.init(storageDirOverride: dir, enablePlatformHooks: false);
    app.audio.applySettings(soundEnabled: false, voiceVol: 0, sfxVol: 0);
  });
  return app;
}

Future<void> _pumpAppAtSize(WidgetTester tester, MorganaAppState app, Size logicalSize) async {
  tester.view.devicePixelRatio = 1.0;
  tester.view.physicalSize = logicalSize;
  await tester.pumpWidget(MorganaCompanionApp(appState: app));
  await tester.pump(const Duration(milliseconds: 120));
}

Future<void> _flushDiskIo(WidgetTester tester) async {
  await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 40)));
  await tester.pump(const Duration(milliseconds: 80));
}

void main() {
  late Directory tmpDir;

  setUp(() {
    tmpDir = Directory.systemTemp.createTempSync('morgana_ui_test_');
  });

  tearDown(() {
    if (tmpDir.existsSync()) tmpDir.deleteSync(recursive: true);
  });

  testWidgets('Home Stage, Morgana tap, Radial ribbons & Bottom Nav flow cleanly', (tester) async {
    final app = await _createTestAppState(tester, tmpDir);
    addTearDown(() {
      tester.view.resetPhysicalSize();
      tester.view.resetDevicePixelRatio();
      app.dispose();
    });

    await _pumpAppAtSize(tester, app, const Size(412, 915));

    expect(find.text('MORGANA'), findsWidgets);
    expect(find.text('FINANCE'), findsWidgets);
    expect(find.text('TASKS'), findsWidgets);
    expect(find.text('NOTES'), findsWidgets);

    // Tap Morgana speech box -> updates speech without errors
    final beforeSpeech = app.morganaSpeech;
    await tester.tap(find.text('TAP TO CHAT'));
    await tester.pump(const Duration(milliseconds: 100));
    expect(app.morganaSpeech, isNot(equals(beforeSpeech)));

    // Open MORE Assist Menu sheet and jump to EVENTS
    await tester.tap(find.text('MORE'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 500));
    expect(find.text('ASSIST MENU'), findsOneWidget);
    await tester.tap(find.text('EVENTS').last);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(app.activeTab, 'events');
    expect(find.text('7-DAY BRIEF'), findsOneWidget);

    // Drain Morgana talk timer cleanly before dispose
    await tester.pump(const Duration(seconds: 2));
  });

  testWidgets('Finance end-to-end UX flow: empty nudge, quick +10rb, record expense, Budgets & Bills', (tester) async {
    final app = await _createTestAppState(tester, tmpDir);
    app.navigateTo('finance');
    addTearDown(() {
      tester.view.resetPhysicalSize();
      tester.view.resetDevicePixelRatio();
      app.dispose();
    });

    await _pumpAppAtSize(tester, app, const Size(412, 915));
    expect(find.text('FINANCE'), findsWidgets);

    // 1. Empty submit triggers warning toast nudge
    final recordBtn = find.text('RECORD EXPENSE');
    await tester.ensureVisible(recordBtn);
    await tester.tap(recordBtn);
    await tester.pump(const Duration(milliseconds: 100));
    expect(app.currentToast, isNotNull);

    // 2. Tap +10rb quick amount pill -> verify dynamic v1.5.3 CTA updates live!
    final plus10 = find.text('+10rb');
    await tester.ensureVisible(plus10);
    await tester.tap(plus10);
    await tester.pump(const Duration(milliseconds: 80));

    final fields = find.byType(TextField);
    await tester.enterText(fields.at(1), 'Kopi Susu');
    await tester.pump(const Duration(milliseconds: 80));

    final submitBtn = find.text('RECORD EXPENSE · Rp 10.000');
    expect(submitBtn, findsOneWidget);
    await tester.ensureVisible(submitBtn);
    await tester.tap(submitBtn);
    await _flushDiskIo(tester);

    expect(app.finance.allTransactions().length, 1);
    expect(app.finance.allTransactions().first['amount'], 10000);
    expect(app.finance.allTransactions().first['title'], 'Kopi Susu');

    // 3. Scroll back up & switch to Budgets & Bills page
    await tester.drag(find.byType(ListView).first, const Offset(0, 400));
    await tester.pump(const Duration(milliseconds: 150));
    await tester.tap(find.text('BUDGETS & BILLS'));
    await tester.pump(const Duration(milliseconds: 150));
    expect(find.textContaining('BUDGET'), findsWidgets);

    await tester.pump(const Duration(seconds: 3));
  });

  testWidgets('Tasks, Notes, Events, Stats, Timer, Ask & Settings render with zero overflow across 3 viewports', (tester) async {
    final app = await _createTestAppState(tester, tmpDir);
    await tester.runAsync(() async {
      await app.settings.updateAll({
        'sheets_sync_id': '1AbCdEfGhIjKlMnOpQrStUvWxYz',
        'sheets_key_json': '{"type":"service_account","client_email":"t@p.iam.gserviceaccount.com","private_key":"x"}',
        'data_sheets_enabled': true,
      });
      await app.tasks.add('Build Android Release APK', priority: 'high', stat: 'proficiency');
      await app.notes.add('Release Notes', '## Android\n- Floating Bubble #android');
      await app.events.add('Launch Party', DateTime.now().add(const Duration(hours: 2)));
      await app.habits.add('Morning Stretch', stat: 'guts', points: 2);
      await app.stats.add('knowledge', 'Flutter Architecture', points: 5);
      await app.finance.addTransaction(type: 'income', amount: 500000, title: 'Allowance', wallet: 'BNI');
      await app.finance.addTransaction(type: 'expense', amount: 25000, title: 'Ramen', category: 'Jajan', wallet: 'Tunai');
    });

    addTearDown(() {
      tester.view.resetPhysicalSize();
      tester.view.resetDevicePixelRatio();
      app.dispose();
    });

    final viewports = <Size>[
      const Size(360, 640), // Compact Android phone
      const Size(412, 915), // Standard modern Android phone
      const Size(800, 1280), // Android tablet
    ];
    final tabs = ['home', 'finance', 'tasks', 'notes', 'events', 'stats', 'timer', 'ask', 'settings'];

    for (final vp in viewports) {
      for (final tab in tabs) {
        app.navigateTo(tab);
        await _pumpAppAtSize(tester, app, vp);
        final err = tester.takeException();
        expect(err, isNull, reason: 'Unexpected layout/render error on tab=$tab at viewport=$vp: $err');
      }
    }

    // Exercise Ask Morgana offline command execution end-to-end
    app.navigateTo('ask');
    await _pumpAppAtSize(tester, app, const Size(412, 915));
    await tester.ensureVisible(find.text('SALDO'));
    await tester.pump();
    await tester.tap(find.text('SALDO'));
    await _flushDiskIo(tester);
    expect(find.textContaining('Rp 475.000'), findsWidgets);

    // Exercise Timer Focus Start & Stop
    app.navigateTo('timer');
    await _pumpAppAtSize(tester, app, const Size(412, 915));
    await tester.tap(find.text('START FOCUS'));
    await _flushDiskIo(tester);
    expect(app.focusRunning, isTrue);
    await tester.tap(find.text('STOP FOCUS'));
    await _flushDiskIo(tester);
    expect(app.focusRunning, isFalse);

    // Drain any toast/talk timers cleanly
    await tester.pump(const Duration(seconds: 3));
  });
}
