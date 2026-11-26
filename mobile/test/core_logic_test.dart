import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:morgana_companion/core/app_state.dart';
import 'package:morgana_companion/core/audio_service.dart';
import 'package:morgana_companion/core/command_engine.dart';
import 'package:morgana_companion/core/emotions.dart';
import 'package:morgana_companion/core/gemini_ai.dart';
import 'package:morgana_companion/core/rupiah.dart';
import 'package:morgana_companion/core/sheets_sync.dart';
import 'package:morgana_companion/core/storage.dart';
import 'package:pointycastle/export.dart' as pc;

Uint8List _asn1Len(int len) {
  if (len < 0x80) return Uint8List.fromList([len]);
  final bytes = <int>[];
  var v = len;
  while (v > 0) {
    bytes.insert(0, v & 0xFF);
    v >>= 8;
  }
  return Uint8List.fromList([0x80 | bytes.length, ...bytes]);
}

Uint8List _asn1Tlv(int tag, List<int> content) =>
    Uint8List.fromList([tag, ..._asn1Len(content.length), ...content]);

Uint8List _asn1Int(BigInt value) {
  var hex = value.toRadixString(16);
  if (hex.length.isOdd) hex = '0$hex';
  final bytes = <int>[];
  for (var i = 0; i < hex.length; i += 2) {
    bytes.add(int.parse(hex.substring(i, i + 2), radix: 16));
  }
  if (bytes.isEmpty || (bytes.first & 0x80) != 0) {
    bytes.insert(0, 0x00);
  }
  return _asn1Tlv(0x02, bytes);
}

/// Generates a real RSA-1024 PKCS#8 PEM keypair in memory for RS256 JWT verification tests.
(String pem, pc.RSAPublicKey pub) _generateTestRsaPkcs8Pem() {
  final rng = pc.FortunaRandom();
  rng.seed(pc.KeyParameter(Uint8List.fromList(List<int>.generate(32, (i) => (i * 37 + 13) & 0xFF))));
  final keyGen = pc.RSAKeyGenerator()
    ..init(pc.ParametersWithRandom(
      pc.RSAKeyGeneratorParameters(BigInt.from(65537), 1024, 64),
      rng,
    ));
  final pair = keyGen.generateKeyPair();
  final priv = pair.privateKey as pc.RSAPrivateKey;
  final pub = pair.publicKey as pc.RSAPublicKey;

  final p = priv.p!;
  final q = priv.q!;
  final dp = priv.privateExponent! % (p - BigInt.one);
  final dq = priv.privateExponent! % (q - BigInt.one);
  final qi = q.modInverse(p);

  final pkcs1Body = <int>[
    ..._asn1Int(BigInt.zero),
    ..._asn1Int(priv.modulus!),
    ..._asn1Int(pub.publicExponent!),
    ..._asn1Int(priv.privateExponent!),
    ..._asn1Int(p),
    ..._asn1Int(q),
    ..._asn1Int(dp),
    ..._asn1Int(dq),
    ..._asn1Int(qi),
  ];
  final pkcs1Seq = _asn1Tlv(0x30, pkcs1Body);
  final algId = _asn1Tlv(0x30, [
    // OID 1.2.840.113549.1.1.1 (rsaEncryption) + NULL
    0x06, 0x09, 0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x01, 0x01,
    0x05, 0x00,
  ]);
  final pkcs8Der = _asn1Tlv(0x30, [
    ..._asn1Int(BigInt.zero),
    ...algId,
    ..._asn1Tlv(0x04, pkcs1Seq),
  ]);

  final b64 = base64Encode(pkcs8Der);
  final lines = <String>[];
  for (var i = 0; i < b64.length; i += 64) {
    lines.add(b64.substring(i, i + 64 > b64.length ? b64.length : i + 64));
  }
  final pem = '-----BEGIN PRIVATE KEY-----\n${lines.join('\n')}\n-----END PRIVATE KEY-----\n';
  return (pem, pub);
}

void main() {
  group('Rupiah shorthand parser & formatter', () {
    test('parses Indonesian shorthands and rejects invalid amounts', () {
      expect(parseRupiah('15rb'), 15000);
      expect(parseRupiah('25k'), 25000);
      expect(parseRupiah('1.5jt'), 1500000);
      expect(parseRupiah('2,5 juta'), 2500000);
      expect(parseRupiah('Rp 25.000'), 25000);
      expect(parseRupiah('150000'), 150000);
      expect(parseRupiah(50000), 50000);
      expect(parseRupiah('0'), isNull);
      expect(parseRupiah('-10000'), isNull);
      expect(parseRupiah('abc'), isNull);
      expect(parseRupiah(''), isNull);
    });

    test('formats Rupiah and compact wallet labels cleanly', () {
      expect(formatRupiah(15000), 'Rp 15.000');
      expect(formatRupiah(-1250000), '-Rp 1.250.000');
      expect(shortRupiah(195000), '195rb');
      expect(shortRupiah(1970000), '1.97jt');
      expect(shortRupiah(-75000), '-75rb');
      expect(normalizeFinanceCategory('makan'), 'Jajan');
      expect(normalizeFinanceCategory('essentials'), 'Mingguan&Bulanan');
      expect(normalizeFinanceWallet('cash'), 'Tunai');
      expect(normalizeFinanceWallet('qris'), 'BNI');
    });
  });

  group('All 7 Atomic JSON Stores persistence', () {
    late Directory tmpDir;

    setUp(() {
      tmpDir = Directory.systemTemp.createTempSync('morgana_test_');
    });

    tearDown(() {
      if (tmpDir.existsSync()) tmpDir.deleteSync(recursive: true);
    });

    test('FinanceStore CRUD, balances, budgets, shortcuts, bills & reload', () async {
      final f = File('${tmpDir.path}/finance.json');
      final store = FinanceStore(f);
      await store.load();

      final inc = await store.addTransaction(
        type: 'income',
        amount: '1jt',
        title: 'Uang Saku',
        wallet: 'BNI',
        day: '2026-11-05',
      );
      expect(inc, isNotNull);

      final exp = await store.addTransaction(
        type: 'expense',
        amount: '25rb',
        title: 'Kopi Susu',
        category: 'Jajan',
        wallet: 'BNI',
        day: '2026-11-05',
      );
      expect(exp, isNotNull);

      final tf = await store.addTransaction(
        type: 'transfer',
        amount: '200rb',
        title: 'Tarik Tunai',
        wallet: 'BNI',
        toWallet: 'Tunai',
        day: '2026-11-05',
      );
      expect(tf, isNotNull);

      final bals = store.walletBalances();
      expect(bals['BNI'], 775000);
      expect(bals['Tunai'], 200000);
      expect(bals['Mandiri'], 0);
      expect(bals['Total'], 975000);

      // Same-wallet transfer must be rejected
      final badTf = await store.addTransaction(
        type: 'transfer',
        amount: 10000,
        title: 'Invalid',
        wallet: 'BNI',
        toWallet: 'BNI',
      );
      expect(badTf, isNull);

      // Budgets, shortcuts, recurring bill pay
      expect(await store.setBudget('Jajan', '300rb'), isTrue);
      final sc = await store.addShortcut('Matcha', 'Jajan', '22rb');
      expect(sc?['amount'], 22000);

      final bill = await store.addBill(name: 'Wi-Fi', day: 10, amount: '150rb', wallet: 'BNI');
      expect(bill, isNotNull);
      final (paidBill, billTx) = await store.payBill(bill!['id'].toString(), 2026, 11);
      expect(paidBill, isNotNull);
      expect(billTx?['amount'], 150000);
      expect(FinanceStore.isBillPaid(paidBill!, 2026, 11), isTrue);

      // Delete + Undo Restore
      final deleted = await store.deleteTransaction(exp!['id'].toString());
      expect(deleted, isNotNull);
      await store.restoreTransaction(deleted!);

      // Reload from disk verifies atomic persistence
      final reloaded = FinanceStore(f);
      await reloaded.load();
      expect(reloaded.allTransactions().length, 4);
      expect(reloaded.budgets()['Jajan'], 300000);
      expect(reloaded.bills().length, 1);
    });

    test('TaskStore, NoteStore, LocalEventStore, Stats & Habits persistence', () async {
      final tasks = TaskStore(File('${tmpDir.path}/tasks.json'));
      await tasks.load();
      final t1 = await tasks.add('Finish APK build', priority: 'high', stat: 'proficiency');
      expect(t1, isNotNull);
      await tasks.setDone(t1!['id'].toString(), true);
      final cleared = await tasks.clearDone();
      expect(cleared.length, 1);
      await tasks.restore(cleared);
      expect(tasks.all().length, 1);

      final notes = NoteStore(File('${tmpDir.path}/notes.json'));
      await notes.load();
      final n1 = await notes.add('Release Checklist', 'Verify #android and #finance sync');
      expect(n1['tags'], containsAll(['android', 'finance']));
      await notes.setPinned(n1['id'].toString(), true);
      expect(notes.get(n1['id'].toString())?['pinned'], isTrue);

      final events = LocalEventStore(File('${tmpDir.path}/events.json'));
      await events.load();
      final ev = await events.add('Demo Day', DateTime(2026, 11, 27, 14, 0), location: 'Lab');
      expect(ev, isNotNull);
      final delEv = await events.delete(ev!['id'].toString());
      expect(delEv, isNotNull);
      await events.restore(delEv!);
      expect(events.all().length, 1);

      final stats = SocialStatsStore(File('${tmpDir.path}/stats.json'));
      final habits = HabitStore(File('${tmpDir.path}/habits.json'));
      await stats.load();
      await habits.load();

      expect(statLevel(0), 1);
      expect(statLevel(30), 2);
      expect(statLevel(310), 5);
      expect(statLevel(490), 6);

      final h = await habits.add('Read 20 pages', stat: 'knowledge', points: 3);
      final (checked, _, _, _) = await habits.toggleToday(h!['id'].toString(), stats);
      expect(checked, isTrue);
      expect(stats.totalFor('knowledge'), 3);
      expect(HabitStore.streak(habits.all().first), 1);

      // Uncheck reverts stat points cleanly
      final (unchecked, _, _, _) = await habits.toggleToday(h['id'].toString(), stats);
      expect(unchecked, isFalse);
      expect(stats.totalFor('knowledge'), 0);

      final settings = SettingsStore(File('${tmpDir.path}/settings.json'));
      await settings.load();
      await settings.updateAll({'enabled_tabs': <String>[]});
      final settingsReloaded = SettingsStore(File('${tmpDir.path}/settings.json'));
      await settingsReloaded.load();
      expect(settingsReloaded.enabledTabs.length, kRadialTabKeys.length);
    });
  });

  group('Offline Quick Command Engine', () {
    test('parses tasks, notes, events, focus, break, saldo, status, dnd & quick expense', () {
      expect(parseQuickCommand('task review PR').kind, CommandKind.addTask);
      expect(parseQuickCommand('note buy coffee #work').kind, CommandKind.addNote);
      expect(parseQuickCommand('event standup').kind, CommandKind.addEvent);

      final foc = parseQuickCommand('focus 45');
      expect(foc.kind, CommandKind.startFocus);
      expect(foc.minutes, 45);

      final brk = parseQuickCommand('break 10');
      expect(brk.kind, CommandKind.startBreak);
      expect(brk.minutes, 10);

      expect(parseQuickCommand('/saldo').kind, CommandKind.checkBalance);
      expect(parseQuickCommand('rekap').kind, CommandKind.checkStatus);
      expect(parseQuickCommand('dnd on').boolFlag, isTrue);
      expect(parseQuickCommand('dnd off').boolFlag, isFalse);

      final quickExp = parseQuickCommand('jajan 15rb kopi');
      expect(quickExp.kind, CommandKind.openTab);
      expect(quickExp.tab, 'finance');
      expect(quickExp.amount, 15000);
      expect(quickExp.text, 'kopi');

      expect(parseQuickCommand('hello morgana').kind, CommandKind.chat);
    });
  });

  group('Google Sheets Monthly_Budget Sync & RS256 JWT', () {
    test('URL/ID extraction, formula injection guard, bank-word SUMIF guard, date parsing', () {
      expect(
        extractSpreadsheetId('https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit#gid=0'),
        '1AbCdEfGhIjKlMnOpQrStUvWxYz',
      );
      expect(extractSpreadsheetId('bad!id'), '');
      expect(safeSheetText('=SUM(A1:A5)'), "'=SUM(A1:A5)");
      expect(safeSheetText('Kopi'), 'Kopi');

      expect(forbiddenBankWord('Bayar pakai BNI'), 'BNI');
      expect(forbiddenBankWord('Bayar pakai BNI', allowedBank: 'BNI'), isNull);
      expect(forbiddenBankWord('Transfer Mandiri', allowedBank: 'BNI'), 'Mandiri');

      expect(parseSheetDate('26/11/2026'), '2026-11-26');
      expect(parseSheetDate('2026-11-26'), '2026-11-26');
      expect(parseSheetDate(46352), '2026-11-26');
    });

    test('signs verifiable RS256 JWT and parses Monthly_Budget sheet via mocked REST API', () async {
      final (pem, pubKey) = _generateTestRsaPkcs8Pem();
      final saJson = jsonEncode({
        'type': 'service_account',
        'client_email': 'morgana-test@project.iam.gserviceaccount.com',
        'private_key': pem,
        'token_uri': 'https://oauth2.googleapis.com/token',
      });

      final (info, err) = validateServiceAccountJson(saJson);
      expect(err, isEmpty);
      expect(info?['client_email'], 'morgana-test@project.iam.gserviceaccount.com');

      // Verify RS256 cryptographic signature using public key
      final jwt = signServiceAccountJwt(
        clientEmail: 'morgana-test@project.iam.gserviceaccount.com',
        privateKeyPem: pem,
      );
      final parts = jwt.split('.');
      expect(parts.length, 3);
      final sigBytes = base64Url.decode(base64Url.normalize(parts[2]));
      final verifier = pc.RSASigner(pc.SHA256Digest(), '0609608648016503040201');
      verifier.init(false, pc.PublicKeyParameter<pc.RSAPublicKey>(pubKey));
      expect(
        verifier.verifySignature(
          Uint8List.fromList(utf8.encode('${parts[0]}.${parts[1]}')),
          pc.RSASignature(sigBytes),
        ),
        isTrue,
      );

      // Mock OAuth + Google Sheets v4 metadata + batchGet (Oktober Monthly_Budget layout)
      final mockHttp = MockClient((req) async {
        final url = req.url.toString();
        if (url.contains('oauth2.googleapis.com/token')) {
          return http.Response(jsonEncode({'access_token': 'fake-access-token'}), 200);
        }
        if (url.contains('fields=sheets.properties.title')) {
          return http.Response(
            jsonEncode({
              'sheets': [
                {'properties': {'title': 'Oktober'}},
                {'properties': {'title': 'November'}},
              ],
            }),
            200,
          );
        }
        if (url.contains('values:batchGet')) {
          return http.Response(
            jsonEncode({
              'valueRanges': [
                {'values': [['Total Pemasukan', 1000000], ['Sisa Uang', 200000]]}, // G9:H12
                {'values': [['BNI', 500000], ['Mandiri', 250000]]}, // K9:L12
                {'values': [['Pemasukan Bank', 750000]]}, // P9:Q12
                // Jajan B35:D
                {'values': [
                  ['Total', '', 45000],
                  ['Kopi Susu', '2026-11-12', 25000],
                  ['Nasi Padang', '2026-11-13', 20000],
                ]},
                // Alat G35:I
                {'values': [['Total', '', 0]]},
                // Mingguan K35:M
                {'values': [['Total', '', 7000], ['Galon', '2026-11-10', 7000]]},
                // Hiburan P35:R
                {'values': [['Total', '', 0]]},
                // Income B9:D34
                {'values': [
                  ['Source', 'Date', 'Amount'],
                  ['Uang Bulanan', '2026-11-01', 1000000],
                ]},
                // Bank T8:V31
                {'values': [
                  ['Bank', 'Date', 'Amount'],
                  ['BNI (QRIS)', '2026-11-12', -25000],
                ]},
                // Legacy Bank S8:U31
                {'values': []},
              ],
            }),
            200,
          );
        }
        return http.Response('Not Found', 404);
      });

      final client = GoogleSheetsFinanceClient(client: mockHttp);
      final res = await client.fetchMonth(
        rawSheetId: '1AbCdEfGhIjKlMnOpQrStUvWxYz',
        keyJson: saJson,
        year: 2026,
        month: 11,
      );
      expect(res.ok, isTrue, reason: res.error);
      expect(res.tab, 'November');
      expect(res.balances['Tunai'], 200000);
      expect(res.balances['BNI'], 500000);
      expect(res.balances['Mandiri'], 250000);
      expect(res.balances['Total'], 950000);
      expect(res.summary['expense'], 52000);
      expect(res.summary['income'], 1000000);
      expect(res.transactions.length, greaterThanOrEqualTo(4));
    });
  });

  group('Gemini AI Client', () {
    test('parses strict JSON Morgana response and falls back on 404 model', () async {
      var calls = 0;
      final mockHttp = MockClient((req) async {
        calls++;
        if (calls == 1 && req.url.toString().contains('gemini-3.5-flash-lite')) {
          return http.Response('Model not found', 404);
        }
        return http.Response(
          jsonEncode({
            'candidates': [
              {
                'content': {
                  'parts': [
                    {
                      'text': '{"reply":"Looking cool, Joker! Let us check Finance!","emotion":"smug","action":"open_finance"}',
                    }
                  ]
                }
              }
            ]
          }),
          200,
        );
      });

      final ai = MorganaAiClient(client: mockHttp);
      final res = await ai.ask(
        provider: 'gemini',
        apiKey: 'test-key',
        model: 'gemini-3.5-flash-lite',
        endpoint: '',
        callUser: 'Joker',
        userText: 'check money',
      );
      expect(res.ok, isTrue);
      expect(calls, 2); // Confirmed automatic 404 fallback to gemini-2.5-flash!
      expect(res.emotion, 'smug');
      expect(res.action, 'open_finance');
      expect(res.text, contains('Joker'));
    });
  });

  group('Curated Morgana Audio, Reaction Coherence & Sleep Mode', () {
    test('all 44 kVoiceAssets exist on disk and kVoicePools resolve validly with anti-repeat', () {
      expect(kVoiceAssets.length, 44);
      for (final entry in kVoiceAssets.entries) {
        final f = File('assets/${entry.value}');
        expect(f.existsSync(), isTrue, reason: 'Missing audio asset for ${entry.key}: ${f.path}');
      }
      final audio = MorganaAudioService();
      for (final poolEntry in kVoicePools.entries) {
        final picked1 = audio.resolveVoiceKey(poolEntry.key);
        final picked2 = audio.resolveVoiceKey(poolEntry.key);
        expect(kVoiceAssets.containsKey(picked1), isTrue);
        expect(kVoiceAssets.containsKey(picked2), isTrue);
        if (poolEntry.value.length > 1) {
          expect(picked2, isNot(equals(picked1)), reason: 'Pool ${poolEntry.key} repeated consecutive clip');
        }
      }
    });

    test('resolveReaction keeps sprite, text, and audio coherent (never sad on excited/upbeat lines)', () {
      // Alias normalization
      expect(normalizeExpression('happy'), 'wink');
      expect(normalizeExpression('sleepy'), 'neutral');

      // Upbeat text mentioning sleep/rest never turns sad
      final r1 = resolveReaction('Moved 3 tasks to tomorrow—sleep guilt-free tonight!');
      expect(r1.emotion, isNot('sad'));
      expect(r1.emotion, 'wink');

      // Contradictory sad emotion paired with excited voice upgrades to excited
      final r2 = resolveReaction('Awesome work!', emotion: 'sad', voice: 'oh_excited');
      expect(r2.emotion, 'excited');
      expect(r2.voice, 'oh_excited');

      // Multi-line startup greeting ignores scary/sleepy task titles below first line
      final r3 = resolveReaction(
        'Good morning, Joker!\n\nYou have 2 pending tasks:\n1. Fix sleep error\n2. Overdue failed build',
        context: 'startup',
        hour: 8,
      );
      expect(r3.emotion, 'wink');
      expect(r3.voice, 'ohayou');

      // Not-a-cat tease picks angry + chigau
      final r4 = resolveReaction("Hey! I am NOT a cat! I'm Morgana!");
      expect(r4.emotion, 'angry');
      expect(r4.voice, 'chigau');
    });

    test('Sleep Mode quick commands and state persistence work end-to-end', () async {
      for (final phrase in ['sleep', 'sleep on', 'tidur', 'mau tidur', 'good night']) {
        final c = parseQuickCommand(phrase);
        expect(c.kind, CommandKind.toggleSleep, reason: phrase);
        expect(c.boolFlag, isTrue, reason: phrase);
      }
      for (final phrase in ['sleep off', 'wake', 'wake up', 'bangun', 'aku bangun']) {
        final c = parseQuickCommand(phrase);
        expect(c.kind, CommandKind.toggleSleep, reason: phrase);
        expect(c.boolFlag, isFalse, reason: phrase);
      }

      final tmp = Directory.systemTemp.createTempSync('morgana_sleep_test_');
      try {
        final app = MorganaAppState();
        await app.init(storageDirOverride: tmp, enablePlatformHooks: false);
        app.audio.enabled = false;
        expect(app.sleepMode, isFalse);

        final resOn = await app.runQuickCommand('mau tidur');
        expect(resOn.badge, 'SLEEP ON');
        expect(app.sleepMode, isTrue);
        expect(app.morganaEmotion, 'wink'); // warm goodnight smile, never sad

        final resOff = await app.runQuickCommand('bangun');
        expect(resOff.badge, 'AWAKE');
        expect(app.sleepMode, isFalse);
        expect(app.morganaEmotion, 'wink');

        app.dispose();
      } finally {
        if (tmp.existsSync()) tmp.deleteSync(recursive: true);
      }
    });
  });

  group('2-Way Companion Google Sheets Sync (Tasks, Notes, Stats, Events, Habits)', () {
    test('mergeStorePayload handles concurrent adds, LWW edits, tombstones, undo restore & habit parity', () {
      // 1. Concurrent adds + LWW edit + delete tombstone
      final merged = mergeStorePayload(
        storeKey: 'tasks',
        localItems: [
          {'id': 't1', 'title': 'From Android (older)', 'done': false, 'updated_at': '2026-11-26T09:00:00Z'},
          {'id': 't2', 'title': 'Android Only', 'done': false, 'updated_at': '2026-11-26T09:05:00Z'},
          {'id': 't3', 'title': 'Deleted On Laptop', 'done': false, 'updated_at': '2026-11-26T08:00:00Z'},
        ],
        localTombstones: {'t4': '2026-11-26T10:00:00Z'},
        remoteItems: [
          {'id': 't1', 'title': 'From Laptop (newer)', 'done': true, 'updated_at': '2026-11-26T09:30:00Z'},
          {'id': 't4', 'title': 'Deleted On Android', 'done': false, 'updated_at': '2026-11-26T08:30:00Z'},
          {'id': 't5', 'title': 'Laptop Only', 'done': false, 'updated_at': '2026-11-26T09:10:00Z'},
        ],
        remoteTombstones: {'t3': '2026-11-26T09:40:00Z'},
        nowMs: DateTime.utc(2026, 11, 26, 12, 0).millisecondsSinceEpoch,
      );
      final byId = {for (final it in merged.items) it['id']: it};
      expect(byId.keys, containsAll(['t1', 't2', 't5']));
      expect(byId.containsKey('t3'), isFalse, reason: 't3 deleted remotely must not resurrect');
      expect(byId.containsKey('t4'), isFalse, reason: 't4 deleted locally must not resurrect');
      expect(byId['t1']?['title'], 'From Laptop (newer)');
      expect(byId['t1']?['done'], isTrue);
      expect(merged.tombstones.keys, containsAll(['t3', 't4']));
      expect(merged.localChanged, isTrue);
      expect(merged.remoteChanged, isTrue);

      // 2. Undo restore with newer timestamp beats older tombstone
      final restored = mergeStorePayload(
        storeKey: 'tasks',
        localItems: [
          {'id': 't3', 'title': 'Restored Task', 'done': false, 'updated_at': '2026-11-26T10:15:00Z'},
        ],
        localTombstones: const {},
        remoteItems: const [],
        remoteTombstones: {'t3': '2026-11-26T09:40:00Z'},
        nowMs: DateTime.utc(2026, 11, 26, 12, 0).millisecondsSinceEpoch,
      );
      expect(restored.items.length, 1);
      expect(restored.items.first['title'], 'Restored Task');
      expect(restored.tombstones.containsKey('t3'), isFalse);

      // 3. HabitStore parity: done_dates <-> dates mirrored identically
      final habitMerged = mergeStorePayload(
        storeKey: 'habits',
        localItems: [
          {'id': 'h1', 'name': 'Read', 'dates': ['2026-11-25'], 'updated': '2026-11-26T08:00:00Z'},
        ],
        localTombstones: const {},
        remoteItems: [
          {'id': 'h1', 'name': 'Read 20m', 'done_dates': ['2026-11-25', '2026-11-26'], 'updated': '2026-11-26T09:00:00Z'},
        ],
        remoteTombstones: const {},
        nowMs: DateTime.utc(2026, 11, 26, 12, 0).millisecondsSinceEpoch,
      );
      expect(habitMerged.items.length, 1);
      expect(habitMerged.items.first['done_dates'], ['2026-11-25', '2026-11-26']);
      expect(habitMerged.items.first['dates'], ['2026-11-25', '2026-11-26']);

      // 4. Chunking round-trip across multiple 45k-char cells
      final hugeBody = List.filled(95000, 'A').join();
      final row = encodeStoreRow(
        'notes',
        [{'id': 'n_big', 'title': 'Huge Note', 'body': hugeBody, 'updated': '2026-11-26T10:00:00Z'}],
        {'n_old': '2026-11-25T10:00:00Z'},
        '2026-11-26T10:00:00Z',
      );
      // 3 metadata cols + >=3 payload chunks
      expect(row.length, greaterThanOrEqualTo(6));
      for (var i = 3; i < row.length; i++) {
        expect(row[i].toString().length, lessThanOrEqualTo(kSyncChunkSize));
      }
      final decoded = decodeStoreRows([
        ['store', 'updated_at', 'count', 'payload_0'],
        row,
      ]);
      expect(decoded['notes']?.items.length, 1);
      expect(decoded['notes']?.items.first['body'], hugeBody);
      expect(decoded['notes']?.tombstones['n_old'], '2026-11-25T10:00:00Z');
    });

    test('syncCompanionData auto-creates Morgana_Sync, syncs 2-way with Desktop & skips redundant writes', () async {
      final (pem, _) = _generateTestRsaPkcs8Pem();
      final saJson = jsonEncode({
        'type': 'service_account',
        'client_email': 'morgana-sync@project.iam.gserviceaccount.com',
        'private_key': pem,
        'token_uri': 'https://oauth2.googleapis.com/token',
      });

      final tabs = <String, List<List<dynamic>>>{'Morgana': []};
      var putCount = 0;
      var createdSyncTab = false;
      String? lastValueInputOption;

      final mockHttp = MockClient((req) async {
        final url = req.url.toString();
        if (url.contains('oauth2.googleapis.com/token')) {
          return http.Response(jsonEncode({'access_token': 'sync-token'}), 200);
        }
        if (req.method == 'POST' && url.contains(':batchUpdate')) {
          final body = jsonDecode(req.body) as Map<String, dynamic>;
          final reqs = body['requests'] as List? ?? const [];
          for (final r in reqs) {
            final title = (r as Map)['addSheet']?['properties']?['title']?.toString();
            if (title != null && title.isNotEmpty) {
              tabs.putIfAbsent(title, () => []);
              if (title == kCompanionSyncTab) createdSyncTab = true;
            }
          }
          return http.Response(jsonEncode({'replies': []}), 200);
        }
        if (url.contains('/values/')) {
          final afterValues = url.split('/values/').last.split('?').first;
          final decodedRange = Uri.decodeComponent(afterValues);
          final tabName = decodedRange.split('!').first.replaceAll("'", '');
          if (req.method == 'GET') {
            if (!tabs.containsKey(tabName)) {
              return http.Response('Unable to parse range: $decodedRange', 400);
            }
            return http.Response(jsonEncode({'values': tabs[tabName]}), 200);
          }
          if (req.method == 'POST' && url.endsWith(':clear')) {
            if (!tabs.containsKey(tabName)) {
              return http.Response('Unable to parse range: $decodedRange', 400);
            }
            tabs[tabName] = [];
            return http.Response(jsonEncode({}), 200);
          }
          if (req.method == 'PUT') {
            putCount++;
            lastValueInputOption = req.url.queryParameters['valueInputOption'];
            final body = jsonDecode(req.body) as Map<String, dynamic>;
            final vals = (body['values'] as List? ?? const [])
                .map((r) => List<dynamic>.from(r as List))
                .toList();
            tabs[tabName] = vals;
            return http.Response(jsonEncode({'updatedCells': vals.length}), 200);
          }
        }
        return http.Response('Unhandled ${req.method} $url', 404);
      });

      final tmp = Directory.systemTemp.createTempSync('morgana_sync_app_');
      try {
        final client = GoogleSheetsFinanceClient(client: mockHttp);
        final app = MorganaAppState(sheets: client);
        await app.init(storageDirOverride: tmp, enablePlatformHooks: false);
        app.audio.enabled = false;

        await app.settings.updateAll({
          'sheets_sync_id': '1AbCdEfGhIjKlMnOpQrStUvWxYz',
          'sheets_key_json': saJson,
          'data_sheets_enabled': true,
          'sheets_sync_tab': 'Morgana',
        });

        // Create local Android items across all 5 stores
        final localTask = await app.tasks.add('Android Task', priority: 'high', deadline: DateTime(2026, 12, 1, 10, 0));
        await app.notes.add('Android Note', 'Sync body #sync');
        await app.stats.add('knowledge', 'Study', points: 3);
        await app.events.add('Android Event', DateTime(2026, 12, 2, 14, 0));
        await app.habits.add('Android Habit', stat: 'guts', points: 2);

        // 1st sync: auto-creates Morgana_Sync tab, writes RAW payload + updates Morgana deadlines
        final r1 = await app.syncCompanionData(silent: true);
        expect(r1.ok, isTrue, reason: r1.error);
        expect(createdSyncTab, isTrue);
        expect(lastValueInputOption, 'RAW');
        expect(tabs[kCompanionSyncTab]?.length, 6); // header + 5 stores
        expect(tabs['Morgana']?.length, 2); // header + 1 active task

        // 2nd sync immediately after: converged -> ZERO PUT calls!
        final putsAfterFirst = putCount;
        final r2 = await app.syncCompanionData(silent: true);
        expect(r2.ok, isTrue, reason: r2.error);
        expect(r2.remoteChanged, isEmpty);
        expect(r2.localChanged, isEmpty);
        expect(putCount, putsAfterFirst, reason: 'Converged poll must perform 0 writes');

        // Simulate Desktop Laptop adding items in all 5 stores & deleting Android's task
        final curRemote = decodeStoreRows(tabs[kCompanionSyncTab]!);
        final tsNow = DateTime.now().add(const Duration(minutes: 5)).toUtc().toIso8601String();
        final newSyncRows = <List<dynamic>>[
          ['store', 'updated_at', 'count', 'payload_0'],
          encodeStoreRow(
            'tasks',
            [
              {'id': 'desk_t1', 'title': 'From Laptop', 'done': false, 'priority': 'med', 'updated_at': tsNow},
            ],
            {localTask!['id'].toString(): tsNow},
            tsNow,
          ),
          encodeStoreRow(
            'notes',
            [
              ...curRemote['notes']!.items,
              {'id': 'desk_n1', 'title': 'Laptop Note', 'body': 'Hello from PC', 'updated': tsNow},
            ],
            const {},
            tsNow,
          ),
          encodeStoreRow(
            'social_stats',
            [
              ...curRemote['social_stats']!.items,
              {'id': 'desk_s1', 'stat': 'charm', 'note': 'Laptop Charm', 'points': 4, 'date': '2026-11-26', 'updated': tsNow},
            ],
            const {},
            tsNow,
          ),
          encodeStoreRow(
            'local_events',
            [
              ...curRemote['local_events']!.items,
              {'id': 'desk_e1', 'title': 'Laptop Event', 'start': '2026-12-03T09:00:00', 'end': '2026-12-03T10:00:00', 'updated': tsNow},
            ],
            const {},
            tsNow,
          ),
          encodeStoreRow(
            'habits',
            [
              ...curRemote['habits']!.items,
              {'id': 'desk_h1', 'name': 'Laptop Habit', 'stat': 'proficiency', 'points': 2, 'done_dates': ['2026-11-26'], 'updated': tsNow},
            ],
            const {},
            tsNow,
          ),
        ];
        tabs[kCompanionSyncTab] = newSyncRows;

        // 3rd sync: Android pulls all Laptop changes and removes deleted localTask
        final r3 = await app.syncCompanionData(silent: true);
        expect(r3.ok, isTrue, reason: r3.error);
        expect(r3.localChanged, containsAll(['tasks', 'notes', 'social_stats', 'local_events', 'habits']));
        expect(app.tasks.all().map((t) => t['id']).toList(), ['desk_t1']);
        expect(app.notes.all().length, 2);
        expect(app.stats.totalFor('knowledge'), 3);
        expect(app.stats.totalFor('charm'), 4);
        expect(app.events.all().length, 2);
        expect(app.habits.all().length, 2);
        final deskHabit = app.habits.all().firstWhere((h) => h['id'] == 'desk_h1');
        expect(deskHabit['dates'], ['2026-11-26']);
        expect(deskHabit['done_dates'], ['2026-11-26']);

        // Verify persistence to disk after reload
        final reloadedTasks = TaskStore(File('${tmp.path}/tasks.json'));
        await reloadedTasks.load();
        expect(reloadedTasks.all().map((t) => t['id']).toList(), ['desk_t1']);
        expect(reloadedTasks.tombstones().containsKey(localTask['id'].toString()), isTrue);

        // Verify one-time settings migration for existing Finance Sheets users
        final migFile = File('${tmp.path}/legacy_settings.json');
        await migFile.writeAsString(jsonEncode({
          'finance_sheets_enabled': true,
          'sheets_sync_id': '1AbCdEfGhIjKlMnOpQrStUvWxYz',
          'sheets_key_json': saJson,
        }));
        final migSettings = SettingsStore(migFile);
        await migSettings.load();
        expect(migSettings['data_sheets_enabled'], isTrue);
        expect(migSettings['companion_sheets_migrated'], isTrue);

        app.dispose();
      } finally {
        if (tmp.existsSync()) tmp.deleteSync(recursive: true);
      }
    });
  });
}
