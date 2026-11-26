import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:pointycastle/export.dart' as pc;

import 'rupiah.dart';
import 'storage.dart';

/// Pure-Dart Google Sheets v4 Finance Sync (100% compatible with desktop `sheets_sync.py` & `bot-wa` Monthly_Budget).
/// Uses RS256 Service Account JWT directly via `pointycastle` + REST — zero backend required.

const List<String> kFinanceMonthTabs = [
  'Januari',
  'Februari',
  'Maret',
  'April',
  'Mei',
  'Juni',
  'Juli',
  'Agustus',
  'September',
  'Oktober',
  'November',
  'Desember',
];

const List<String> _kEnglishMonths = [
  'january',
  'february',
  'march',
  'april',
  'may',
  'june',
  'july',
  'august',
  'september',
  'october',
  'november',
  'december',
];

const Map<String, (String, String)> kFinanceSheetCatCols = {
  'Jajan': ('B', 'D'),
  'Alat': ('G', 'I'),
  'Mingguan&Bulanan': ('K', 'M'),
  'Hiburan': ('P', 'R'),
};

const int kExpenseTotalRow = 35;
const int kExpenseRowStart = 36;
const int kExpenseRowEnd = 1000;
const int kIncomeRowStart = 10;
const int kIncomeRowEnd = 31;
const int kBankRowStart = 10;
const int kBankRowEnd = 31;

const Map<String, String> kQrisSourceByBank = {
  'BNI': 'QRIS BNI',
  'Mandiri': 'QRIS Mandiri',
};

const List<String> kBankWords = ['BNI', 'Mandiri'];

final RegExp _sheetUrlRe = RegExp(r'/spreadsheets/d/([A-Za-z0-9_-]{10,128})');
final RegExp _sheetIdRe = RegExp(r'^[A-Za-z0-9_-]{10,128}$');

String extractSpreadsheetId(String? raw) {
  final text = (raw ?? '').trim();
  if (text.isEmpty) return '';
  final m = _sheetUrlRe.firstMatch(text);
  if (m != null) return m.group(1)!;
  if (_sheetIdRe.hasMatch(text)) return text;
  return '';
}

String monthTabName(int month) => kFinanceMonthTabs[(month - 1).clamp(0, 11)];

String _a1(String tab, String cells) => "'${tab.replaceAll("'", "''")}'!$cells";

String safeSheetText(String text) {
  final t = text.trim();
  if (t.isNotEmpty && ('=+-@'.contains(t[0]))) {
    return "'$t";
  }
  return t;
}

/// Protect Monthly_Budget SUMIF("*BNI*") / SUMIF("*Mandiri*") formulas.
String? forbiddenBankWord(String text, {String? allowedBank}) {
  final low = text.toLowerCase();
  for (final bank in kBankWords) {
    if (allowedBank != null && bank.toLowerCase() == allowedBank.toLowerCase()) continue;
    if (RegExp('\\b${RegExp.escape(bank.toLowerCase())}\\b').hasMatch(low)) {
      return bank;
    }
  }
  return null;
}

bool _isNum(Object? v) => v is num && !v.isNaN && !v.isInfinite;

/// Parse ISO date, DD/MM/YYYY, or Google Sheets / Excel serial date number -> YYYY-MM-DD.
String? parseSheetDate(Object? raw) {
  if (raw == null) return null;
  if (raw is num && raw > 25000 && raw < 80000) {
    final dt = DateTime.utc(1899, 12, 30).add(Duration(days: raw.floor()));
    return '${dt.year.toString().padLeft(4, '0')}-${dt.month.toString().padLeft(2, '0')}-${dt.day.toString().padLeft(2, '0')}';
  }
  final s = raw.toString().trim();
  if (s.isEmpty) return null;
  if (RegExp(r'^\d{4}-\d{2}-\d{2}').hasMatch(s)) return s.substring(0, 10);
  final dm = RegExp(r'^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$').firstMatch(s);
  if (dm != null) {
    final d = int.tryParse(dm.group(1)!);
    final mo = int.tryParse(dm.group(2)!);
    final y = int.tryParse(dm.group(3)!);
    if (d != null && mo != null && y != null && mo >= 1 && mo <= 12 && d >= 1 && d <= 31) {
      return '${y.toString().padLeft(4, '0')}-${mo.toString().padLeft(2, '0')}-${d.toString().padLeft(2, '0')}';
    }
  }
  return null;
}

String _extractBankFromLabel(String name) => name.toLowerCase().contains('mandiri') ? 'Mandiri' : 'BNI';

String _stripTransferNote(String name) {
  final m = RegExp(r'\s-\s(.+?)\)?$').firstMatch(name);
  return m?.group(1)?.trim() ?? '';
}

String _formatBankIncomeLabel(String bank, String note) {
  final clean = note.trim();
  if (clean.isEmpty) return bank;
  if (clean.toLowerCase().contains(bank.toLowerCase())) return clean;
  return '$bank - $clean';
}

List<int> _findEmptyRows(List<List<dynamic>> colRows, int startRow, int endRow, int count) {
  final out = <int>[];
  final maxIdx = endRow - startRow;
  for (var i = 0; i <= maxIdx; i++) {
    final cell = (i < colRows.length && colRows[i].isNotEmpty) ? colRows[i][0] : '';
    if (cell == null || cell.toString().trim().isEmpty) {
      out.add(startRow + i);
      if (out.length >= count) break;
    }
  }
  return out;
}

class FinanceSheetMonthData {
  final bool ok;
  final String tab;
  final int year;
  final int month;
  final Map<String, int> balances;
  final Map<String, dynamic> summary;
  final List<Map<String, dynamic>> transactions;
  final String syncedAt;
  final String error;

  const FinanceSheetMonthData({
    required this.ok,
    required this.tab,
    required this.year,
    required this.month,
    this.balances = const {'Tunai': 0, 'BNI': 0, 'Mandiri': 0, 'Total': 0},
    this.summary = const {
      'income': 0,
      'expense': 0,
      'net': 0,
      'today_expense': 0,
      'count': 0,
      'by_category': {'Jajan': 0, 'Alat': 0, 'Mingguan&Bulanan': 0, 'Hiburan': 0},
    },
    this.transactions = const [],
    this.syncedAt = '',
    this.error = '',
  });
}

class FinanceSheetWriteResult {
  final bool ok;
  final String tab;
  final String message;
  final String error;
  final List<Map<String, dynamic>> cells;

  const FinanceSheetWriteResult({
    required this.ok,
    required this.tab,
    this.message = '',
    this.error = '',
    this.cells = const [],
  });
}

// ---------------------------------------------------------------------------
// Minimal PKCS#8 ASN.1 DER RSA Private Key Parser + RS256 JWT Signer
// ---------------------------------------------------------------------------
class _Asn1Reader {
  final Uint8List bytes;
  int offset = 0;

  _Asn1Reader(this.bytes);

  int _readLength() {
    var b = bytes[offset++];
    if ((b & 0x80) == 0) return b;
    final n = b & 0x7F;
    var len = 0;
    for (var i = 0; i < n; i++) {
      len = (len << 8) | bytes[offset++];
    }
    return len;
  }

  (int tag, Uint8List content) readNext() {
    final tag = bytes[offset++];
    final len = _readLength();
    final slice = Uint8List.sublistView(bytes, offset, offset + len);
    offset += len;
    return (tag, slice);
  }

  BigInt readInteger() {
    final (tag, content) = readNext();
    if (tag != 0x02) throw const FormatException('Expected ASN.1 INTEGER');
    var result = BigInt.zero;
    for (final b in content) {
      result = (result << 8) | BigInt.from(b);
    }
    return result;
  }
}

pc.RSAPrivateKey parseRsaPrivateKeyFromPem(String pem) {
  final cleaned = pem
      .replaceAll(RegExp(r'-----BEGIN [A-Z ]+-----'), '')
      .replaceAll(RegExp(r'-----END [A-Z ]+-----'), '')
      .replaceAll(RegExp(r'\s+'), '');
  final der = base64Decode(cleaned);
  final outer = _Asn1Reader(der);
  final (outerTag, outerSeq) = outer.readNext();
  if (outerTag != 0x30) throw const FormatException('Invalid RSA key ASN.1 sequence');

  // Detect PKCS#8 (version, AlgorithmIdentifier SEQUENCE, privateKey OCTET STRING)
  // vs PKCS#1 (version, n, e, d, p, q, ...)
  final r1 = _Asn1Reader(outerSeq);
  r1.readInteger(); // version
  final (secondTag, secondContent) = r1.readNext();
  late final _Asn1Reader rsaSeq;
  if (secondTag == 0x30) {
    // PKCS#8: next item is OCTET STRING wrapping PKCS#1 SEQUENCE
    final (octTag, octBytes) = r1.readNext();
    if (octTag != 0x04) throw const FormatException('Expected PKCS#8 OCTET STRING');
    final inner = _Asn1Reader(octBytes);
    final (seqTag, seqBytes) = inner.readNext();
    if (seqTag != 0x30) throw const FormatException('Expected PKCS#1 SEQUENCE');
    rsaSeq = _Asn1Reader(seqBytes);
    rsaSeq.readInteger(); // pkcs1 version
  } else if (secondTag == 0x02) {
    // Already PKCS#1: rewind to modulus
    rsaSeq = _Asn1Reader(outerSeq);
    rsaSeq.readInteger(); // version
    final _ = secondContent;
  } else {
    throw const FormatException('Unsupported RSA private key format');
  }

  final n = rsaSeq.readInteger();
  rsaSeq.readInteger(); // publicExponent e
  final d = rsaSeq.readInteger();
  final p = rsaSeq.readInteger();
  final q = rsaSeq.readInteger();
  return pc.RSAPrivateKey(n, d, p, q);
}

String _b64Url(List<int> data) => base64UrlEncode(data).replaceAll('=', '');

String signServiceAccountJwt({
  required String clientEmail,
  required String privateKeyPem,
  String scope = 'https://www.googleapis.com/auth/spreadsheets',
  String tokenUri = 'https://oauth2.googleapis.com/token',
  DateTime? now,
}) {
  final iat = ((now ?? DateTime.now().toUtc()).millisecondsSinceEpoch ~/ 1000);
  final header = _b64Url(utf8.encode(jsonEncode({'alg': 'RS256', 'typ': 'JWT'})));
  final claim = _b64Url(utf8.encode(jsonEncode({
    'iss': clientEmail,
    'scope': scope,
    'aud': tokenUri,
    'exp': iat + 3600,
    'iat': iat,
  })));
  final signingInput = '$header.$claim';
  final key = parseRsaPrivateKeyFromPem(privateKeyPem);
  final signer = pc.RSASigner(pc.SHA256Digest(), '0609608648016503040201');
  signer.init(true, pc.PrivateKeyParameter<pc.RSAPrivateKey>(key));
  final sig = signer.generateSignature(Uint8List.fromList(utf8.encode(signingInput)));
  return '$signingInput.${_b64Url(sig.bytes)}';
}

/// Validates Service Account JSON string and returns `(info, error)`.
(Map<String, dynamic>?, String) validateServiceAccountJson(String rawJson) {
  try {
    final decoded = jsonDecode(rawJson);
    if (decoded is! Map<String, dynamic> || decoded['type'] != 'service_account') {
      return (null, 'JSON is not a Google Service Account key (expected type: service_account).');
    }
    if ((decoded['client_email'] ?? '').toString().isEmpty ||
        (decoded['private_key'] ?? '').toString().isEmpty) {
      return (null, 'Service Account JSON is missing client_email or private_key.');
    }
    // Verify key parses cleanly up front
    parseRsaPrivateKeyFromPem(decoded['private_key'].toString());
    return (decoded, '');
  } catch (e) {
    return (null, 'Invalid Service Account JSON: $e');
  }
}

class GoogleSheetsFinanceClient {
  final http.Client _http;
  String _cachedToken = '';
  String _cachedEmail = '';
  DateTime _tokenExpiry = DateTime.fromMillisecondsSinceEpoch(0);

  GoogleSheetsFinanceClient({http.Client? client}) : _http = client ?? http.Client();

  void clearTokenCache() {
    _cachedToken = '';
    _cachedEmail = '';
    _tokenExpiry = DateTime.fromMillisecondsSinceEpoch(0);
  }

  Future<(String?, String, String)> _getAccessToken(String keyJson) async {
    final (info, err) = validateServiceAccountJson(keyJson);
    if (info == null || err.isNotEmpty) return (null, '', err);
    final email = info['client_email'].toString();
    final tokenUri = (info['token_uri'] ?? 'https://oauth2.googleapis.com/token').toString();
    final now = DateTime.now().toUtc();
    if (_cachedToken.isNotEmpty && _cachedEmail == email && now.isBefore(_tokenExpiry)) {
      return (_cachedToken, email, '');
    }
    try {
      final jwt = signServiceAccountJwt(
        clientEmail: email,
        privateKeyPem: info['private_key'].toString(),
        tokenUri: tokenUri,
        now: now,
      );
      final resp = await _http
          .post(
            Uri.parse(tokenUri),
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: {
              'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
              'assertion': jwt,
            },
          )
          .timeout(const Duration(seconds: 12));
      if (resp.statusCode != 200) {
        return (null, email, 'Google OAuth failed (HTTP ${resp.statusCode}). Check key file or device clock.');
      }
      final body = jsonDecode(resp.body) as Map<String, dynamic>;
      final token = (body['access_token'] ?? '').toString();
      if (token.isEmpty) return (null, email, 'Google OAuth returned empty access token.');
      _cachedToken = token;
      _cachedEmail = email;
      _tokenExpiry = now.add(const Duration(minutes: 50));
      return (token, email, '');
    } catch (e) {
      return (null, email, 'Cannot reach Google OAuth (${e.runtimeType}). Check internet connection.');
    }
  }

  static List<String> _batchRanges(String tab) {
    final ranges = <String>[
      _a1(tab, 'G9:H12'),
      _a1(tab, 'K9:L12'),
      _a1(tab, 'P9:Q12'),
    ];
    for (final cat in kFinanceSheetCatCols.keys) {
      final (colA, colC) = kFinanceSheetCatCols[cat]!;
      ranges.add(_a1(tab, '$colA$kExpenseTotalRow:$colC'));
    }
    ranges.add(_a1(tab, 'B9:D34'));
    ranges.add(_a1(tab, 'T8:V31'));
    ranges.add(_a1(tab, 'S8:U31'));
    return ranges;
  }

  Future<http.Response> _batchGet(String token, String sheetId, List<String> ranges) {
    final qs = [
      'valueRenderOption=UNFORMATTED_VALUE',
      for (final r in ranges) 'ranges=${Uri.encodeQueryComponent(r)}',
    ].join('&');
    final uri = Uri.parse('https://sheets.googleapis.com/v4/spreadsheets/$sheetId/values:batchGet?$qs');
    return _http.get(uri, headers: {'Authorization': 'Bearer $token'}).timeout(const Duration(seconds: 14));
  }

  Future<http.Response> _valuesGet(String token, String sheetId, String a1Range) {
    final uri = Uri.parse(
      'https://sheets.googleapis.com/v4/spreadsheets/$sheetId/values/${Uri.encodeComponent(a1Range)}?valueRenderOption=UNFORMATTED_VALUE',
    );
    return _http.get(uri, headers: {'Authorization': 'Bearer $token'}).timeout(const Duration(seconds: 12));
  }

  Future<http.Response> _valuesUpdate(String token, String sheetId, String a1Range, List<List<dynamic>> values) {
    final uri = Uri.parse(
      'https://sheets.googleapis.com/v4/spreadsheets/$sheetId/values/${Uri.encodeComponent(a1Range)}?valueInputOption=USER_ENTERED',
    );
    return _http
        .put(
          uri,
          headers: {'Authorization': 'Bearer $token', 'Content-Type': 'application/json'},
          body: jsonEncode({'values': values}),
        )
        .timeout(const Duration(seconds: 12));
  }

  Future<http.Response> _valuesBatchUpdate(String token, String sheetId, List<Map<String, dynamic>> data) {
    final uri = Uri.parse('https://sheets.googleapis.com/v4/spreadsheets/$sheetId/values:batchUpdate');
    return _http
        .post(
          uri,
          headers: {'Authorization': 'Bearer $token', 'Content-Type': 'application/json'},
          body: jsonEncode({'valueInputOption': 'USER_ENTERED', 'data': data}),
        )
        .timeout(const Duration(seconds: 12));
  }

  Future<http.Response> _valuesClear(String token, String sheetId, String a1Range) {
    final uri = Uri.parse(
      'https://sheets.googleapis.com/v4/spreadsheets/$sheetId/values/${Uri.encodeComponent(a1Range)}:clear',
    );
    return _http.post(uri, headers: {'Authorization': 'Bearer $token'}).timeout(const Duration(seconds: 12));
  }

  Future<(String?, List<String>)> _resolveMonthTab(String token, String sheetId, int month) async {
    final uri = Uri.parse('https://sheets.googleapis.com/v4/spreadsheets/$sheetId?fields=sheets.properties.title');
    final resp = await _http.get(uri, headers: {'Authorization': 'Bearer $token'}).timeout(const Duration(seconds: 10));
    if (resp.statusCode != 200) return (null, const <String>[]);
    final body = jsonDecode(resp.body) as Map<String, dynamic>;
    final sheets = (body['sheets'] as List? ?? const []);
    final titles = sheets
        .map((s) => ((s as Map)['properties'] as Map?)?['title']?.toString() ?? '')
        .where((t) => t.isNotEmpty)
        .toList();
    final idx = (month - 1).clamp(0, 11);
    final targets = {
      kFinanceMonthTabs[idx].toLowerCase(),
      kFinanceMonthTabs[idx].substring(0, 3).toLowerCase(),
      _kEnglishMonths[idx],
      _kEnglishMonths[idx].substring(0, 3),
    };
    for (final t in titles) {
      if (targets.contains(t.trim().toLowerCase())) return (t, titles);
    }
    return (null, titles);
  }

  String _friendlyError(int status, String email, String tab, [List<String> avail = const []]) {
    if (status == 403) {
      return email.isNotEmpty
          ? 'Permission denied (403). Share sheet as Editor with $email.'
          : 'Permission denied (403). Share sheet with Service Account email.';
    }
    if (status == 404) return 'Spreadsheet not found (404). Check Spreadsheet ID / URL.';
    if (status == 400) {
      final hint = avail.isNotEmpty ? ' Available tabs: ${avail.take(6).join(", ")}' : '';
      return "Tab '$tab' not found in spreadsheet.$hint";
    }
    if (status == 429) return 'Google Sheets rate limit (429). Try again in a moment.';
    return 'Google Sheets API error (HTTP $status).';
  }

  /// Pure parser for a fetched Monthly_Budget `batchGet` valueRanges response (unit-testable!).
  static FinanceSheetMonthData parseBatchResponse({
    required String tab,
    required int year,
    required int month,
    required List<List<List<dynamic>>> blocks,
    String? nowTodayIso,
    String? syncedAt,
  }) {
    final hasil = <String, double>{};
    for (final row in blocks[0]) {
      if (row.length >= 2 && _isNum(row[1])) {
        hasil[row[0].toString().trim().toLowerCase()] = (row[1] as num).toDouble();
      }
    }
    final bankHasil = <String, double>{};
    for (final row in blocks[1]) {
      if (row.length >= 2 && _isNum(row[1])) {
        bankHasil[row[0].toString().trim().toLowerCase()] = (row[1] as num).toDouble();
      }
    }
    final bankAct = <String, double>{};
    for (final row in blocks[2]) {
      if (row.length >= 2 && _isNum(row[1])) {
        bankAct[row[0].toString().trim().toLowerCase()] = (row[1] as num).toDouble();
      }
    }

    final b9_34 = blocks[7];
    final t8_31 = blocks[8];
    final s8_31 = blocks[9];

    final b16 = (b9_34.length > 7 && b9_34[7].isNotEmpty) ? b9_34[7][0].toString().trim().toLowerCase() : '';
    final b17 = (b9_34.length > 8 && b9_34[8].isNotEmpty) ? b9_34[8][0].toString().trim().toLowerCase() : '';
    final legacySingle = b16 == 'pengeluaran' && b17 == 'source';

    final t8Hdr = (t8_31.isNotEmpty && t8_31[0].isNotEmpty) ? t8_31[0][0].toString().trim().toLowerCase() : '';
    final s8Hdr = (s8_31.isNotEmpty && s8_31[0].isNotEmpty) ? s8_31[0][0].toString().trim().toLowerCase() : '';
    final legacySepBank = t8Hdr != 'source' && s8Hdr == 'source';

    final tunaiBal = (hasil['sisa uang'] ?? 0).round();
    var bniBal = (bankHasil['bni'] ?? 0).round();
    final mandiriBal = (bankHasil['mandiri'] ?? 0).round();
    if (bankHasil.isEmpty && bankAct.containsKey('sisa uang')) {
      bniBal = bankAct['sisa uang']!.round();
    }
    final balances = <String, int>{
      'Tunai': tunaiBal,
      'BNI': bniBal,
      'Mandiri': mandiriBal,
      'Total': tunaiBal + bniBal + mandiriBal,
    };

    final catNames = kFinanceSheetCatCols.keys.toList();
    final byCat = {for (final c in catNames) c: 0};
    final transactions = <Map<String, dynamic>>[];
    final defaultDate = '${year.toString().padLeft(4, '0')}-${month.toString().padLeft(2, '0')}-01';
    final todayS = nowTodayIso ?? todayIso();
    var todayExpense = 0;

    if (legacySingle) {
      final combined = <(int, List<dynamic>)>[];
      for (var i = 9; i < b9_34.length; i++) {
        combined.add((9 + i, b9_34[i]));
      }
      for (var i = 0; i < blocks[3].length; i++) {
        final rnum = kExpenseTotalRow + i;
        if (rnum > kExpenseRowEnd) break;
        combined.add((rnum, blocks[3][i]));
      }
      for (final (rnum, r) in combined) {
        final nama = r.isNotEmpty ? r[0].toString().trim() : '';
        final tgl = r.length > 1 ? r[1] : null;
        final nilai = r.length > 2 ? r[2] : null;
        if (!_isNum(nilai)) continue;
        final val = (nilai as num).round();
        byCat['Jajan'] = byCat['Jajan']! + val;
        final dStr = parseSheetDate(tgl) ?? defaultDate;
        if (dStr == todayS) todayExpense += val.abs();
        final rng = 'B$rnum:D$rnum';
        transactions.add({
          'id': 'sh:$tab:$rng',
          'type': 'expense',
          'amount': val.abs(),
          'title': nama.isEmpty ? 'Expense' : nama,
          'category': 'Jajan',
          'wallet': '',
          'to_wallet': '',
          'date': dStr,
          'source': 'sheets',
          'sheet_row': rnum,
          'sheet_cells': [
            {'tab': tab, 'range': rng, 'name': nama, 'amount': val}
          ],
        });
      }
    } else {
      for (var ci = 0; ci < catNames.length; ci++) {
        final cat = catNames[ci];
        final (colA, colC) = kFinanceSheetCatCols[cat]!;
        final rows = blocks[3 + ci];
        final totalRow = rows.isNotEmpty ? rows[0] : const [];
        var catSum = 0;
        for (var i = 1; i < rows.length; i++) {
          final rnum = kExpenseRowStart + (i - 1);
          if (rnum > kExpenseRowEnd) break;
          final r = rows[i];
          final nama = r.isNotEmpty ? r[0].toString().trim() : '';
          final tgl = r.length > 1 ? r[1] : null;
          final nilai = r.length > 2 ? r[2] : null;
          if (!_isNum(nilai)) continue;
          final val = (nilai as num).round();
          catSum += val;
          final dStr = parseSheetDate(tgl) ?? defaultDate;
          if (dStr == todayS) todayExpense += val.abs();
          final rng = '$colA$rnum:$colC$rnum';
          transactions.add({
            'id': 'sh:$tab:$rng',
            'type': 'expense',
            'amount': val.abs(),
            'title': nama.isEmpty ? cat : nama,
            'category': cat,
            'wallet': '',
            'to_wallet': '',
            'date': dStr,
            'source': 'sheets',
            'sheet_row': rnum,
            'sheet_cells': [
              {'tab': tab, 'range': rng, 'name': nama, 'amount': val}
            ],
          });
        }
        byCat[cat] = (totalRow.length >= 3 && _isNum(totalRow[2])) ? (totalRow[2] as num).round() : catSum;
      }
    }

    // Pemasukan (B10:D31) & Pemasukan Bank (T10:V31 or S9:U31)
    final cashRaw = <Map<String, dynamic>>[];
    final incStart = legacySingle ? 0 : 1;
    final incEnd = legacySingle ? (b9_34.length < 7 ? b9_34.length : 7) : (b9_34.length < 23 ? b9_34.length : 23);
    for (var i = incStart; i < incEnd; i++) {
      final rnum = 9 + i;
      final r = b9_34[i];
      final nama = r.isNotEmpty ? r[0].toString().trim() : '';
      final tgl = r.length > 1 ? r[1] : null;
      final nilai = r.length > 2 ? r[2] : null;
      if (!_isNum(nilai)) continue;
      final val = (nilai as num).round();
      final rng = 'B$rnum:D$rnum';
      cashRaw.add({
        'row': rnum,
        'name': nama,
        'low': nama.toLowerCase(),
        'date': parseSheetDate(tgl) ?? defaultDate,
        'amount': val,
        'cell': {'tab': tab, 'range': rng, 'name': nama, 'amount': val},
      });
    }

    final bankRaw = <Map<String, dynamic>>[];
    final bSrc = legacySepBank ? s8_31 : t8_31;
    final bStart = legacySepBank ? 1 : 2;
    final bColA = legacySepBank ? 'S' : 'T';
    final bColC = legacySepBank ? 'U' : 'V';
    for (var i = bStart; i < bSrc.length; i++) {
      final rnum = 8 + i;
      final r = bSrc[i];
      final nama = r.isNotEmpty ? r[0].toString().trim() : '';
      final tgl = r.length > 1 ? r[1] : null;
      final nilai = r.length > 2 ? r[2] : null;
      if (!_isNum(nilai)) continue;
      final val = (nilai as num).round();
      final rng = '$bColA$rnum:$bColC$rnum';
      bankRaw.add({
        'row': rnum,
        'name': nama,
        'low': nama.toLowerCase(),
        'date': parseSheetDate(tgl) ?? defaultDate,
        'amount': val,
        'cell': {'tab': tab, 'range': rng, 'name': nama, 'amount': val},
      });
    }

    final usedBankRows = <int>{};
    var fallbackInc = 0;

    for (final item in cashRaw) {
      final low = item['low'] as String;
      final amt = item['amount'] as int;
      final name = item['name'] as String;
      final cell = item['cell'] as Map<String, dynamic>;
      if (low == 'qris bni' || low == 'qris mandiri' || low == 'qris') {
        final bank = low.contains('mandiri') ? 'Mandiri' : 'BNI';
        transactions.add({
          'id': 'sh:$tab:${cell['range']}',
          'type': 'transfer',
          'amount': amt.abs(),
          'title': low == 'qris' ? 'QRIS' : '$name (Monthly Pool)',
          'category': '',
          'wallet': bank,
          'to_wallet': 'QRIS',
          'date': item['date'],
          'source': 'sheets',
          'sheet_kind': 'qris_pool',
          'sheet_row': item['row'],
          'sheet_cells': [cell],
        });
      } else if (low.startsWith('tarik uang')) {
        final bank = low.contains('mandiri') ? 'Mandiri' : 'BNI';
        final note = _stripTransferNote(name);
        transactions.add({
          'id': 'sh:$tab:${cell['range']}',
          'type': 'transfer',
          'amount': amt.abs(),
          'title': name,
          'edit_note': note,
          'category': '',
          'wallet': bank,
          'to_wallet': 'Tunai',
          'date': item['date'],
          'source': 'sheets',
          'sheet_row': item['row'],
          'sheet_cells': [cell],
        });
      } else if (low.startsWith('setor tunai') && amt < 0) {
        Map<String, dynamic>? partner;
        for (final b in bankRaw) {
          if (!usedBankRows.contains(b['row']) &&
              (b['low'] as String).contains('(setor tunai') &&
              b['amount'] == -amt &&
              b['date'] == item['date']) {
            partner = b;
            break;
          }
        }
        final note = _stripTransferNote(name);
        if (partner != null) {
          usedBankRows.add(partner['row'] as int);
          final toBank = _extractBankFromLabel(partner['name'] as String);
          final pCell = partner['cell'] as Map<String, dynamic>;
          transactions.add({
            'id': 'sh:$tab:${cell['range']}+${pCell['range']}',
            'type': 'transfer',
            'amount': amt.abs(),
            'title': note.isEmpty ? 'Setor Tunai → $toBank' : note,
            'edit_note': note,
            'category': '',
            'wallet': 'Tunai',
            'to_wallet': toBank,
            'date': item['date'],
            'source': 'sheets',
            'sheet_row': item['row'],
            'sheet_cells': [cell, pCell],
          });
        } else {
          transactions.add({
            'id': 'sh:$tab:${cell['range']}',
            'type': 'transfer',
            'amount': amt.abs(),
            'title': name,
            'edit_note': note,
            'category': '',
            'wallet': 'Tunai',
            'to_wallet': 'BNI',
            'date': item['date'],
            'source': 'sheets',
            'sheet_row': item['row'],
            'sheet_cells': [cell],
          });
        }
      } else {
        if (amt > 0) fallbackInc += amt;
        transactions.add({
          'id': 'sh:$tab:${cell['range']}',
          'type': 'income',
          'amount': amt.abs(),
          'title': name.isEmpty ? 'Tunai' : name,
          'category': '',
          'wallet': 'Tunai',
          'to_wallet': '',
          'date': item['date'],
          'source': 'sheets',
          'sheet_row': item['row'],
          'sheet_cells': [cell],
        });
      }
    }

    for (final b in bankRaw) {
      if (usedBankRows.contains(b['row'])) continue;
      final low = b['low'] as String;
      final amt = b['amount'] as int;
      final name = b['name'] as String;
      final cell = b['cell'] as Map<String, dynamic>;
      if (low.contains('(tf keluar') && amt < 0) {
        Map<String, dynamic>? partner;
        for (final p in bankRaw) {
          if (!usedBankRows.contains(p['row']) &&
              p['row'] != b['row'] &&
              (p['low'] as String).contains('(tf masuk') &&
              p['amount'] == -amt &&
              p['date'] == b['date']) {
            partner = p;
            break;
          }
        }
        if (partner != null) {
          usedBankRows.add(b['row'] as int);
          usedBankRows.add(partner['row'] as int);
          final fromBank = _extractBankFromLabel(name);
          final toBank = _extractBankFromLabel(partner['name'] as String);
          final note = _stripTransferNote(name);
          final pCell = partner['cell'] as Map<String, dynamic>;
          transactions.add({
            'id': 'sh:$tab:${cell['range']}+${pCell['range']}',
            'type': 'transfer',
            'amount': amt.abs(),
            'title': note.isEmpty ? '$fromBank → $toBank' : note,
            'edit_note': note,
            'category': '',
            'wallet': fromBank,
            'to_wallet': toBank,
            'date': b['date'],
            'source': 'sheets',
            'sheet_row': b['row'],
            'sheet_cells': [cell, pCell],
          });
          continue;
        }
      }
      usedBankRows.add(b['row'] as int);
      final bank = _extractBankFromLabel(name);
      if (amt > 0) fallbackInc += amt;
      transactions.add({
        'id': 'sh:$tab:${cell['range']}',
        'type': 'income',
        'amount': amt.abs(),
        'title': name.isEmpty ? bank : name,
        'category': '',
        'wallet': bank,
        'to_wallet': '',
        'date': b['date'],
        'source': 'sheets',
        'sheet_row': b['row'],
        'sheet_cells': [cell],
      });
    }

    transactions.sort((a, b) {
      final dc = (b['date'] as String).compareTo(a['date'] as String);
      if (dc != 0) return dc;
      return ((b['sheet_row'] as int?) ?? 0).compareTo((a['sheet_row'] as int?) ?? 0);
    });

    final monthInc = hasil.containsKey('pemasukan') ? hasil['pemasukan']!.round() : fallbackInc;
    final monthExp = hasil.containsKey('pengeluaran')
        ? hasil['pengeluaran']!.round()
        : byCat.values.fold<int>(0, (a, b) => a + b);
    final now = DateTime.now();
    final hhmm = syncedAt ?? '${now.hour.toString().padLeft(2, '0')}:${now.minute.toString().padLeft(2, '0')}';

    return FinanceSheetMonthData(
      ok: true,
      tab: tab,
      year: year,
      month: month,
      balances: balances,
      summary: {
        'income': monthInc,
        'expense': monthExp,
        'net': monthInc - monthExp,
        'today_expense': todayExpense,
        'count': transactions.length,
        'by_category': byCat,
      },
      transactions: transactions,
      syncedAt: hhmm,
    );
  }

  Future<FinanceSheetMonthData> fetchMonth({
    required String rawSheetId,
    required String keyJson,
    required int year,
    required int month,
  }) async {
    var tab = monthTabName(month);
    final sheetId = extractSpreadsheetId(rawSheetId);
    if (sheetId.isEmpty) {
      return FinanceSheetMonthData(ok: false, tab: tab, year: year, month: month, error: 'Spreadsheet ID not set.');
    }
    final (token, email, tokErr) = await _getAccessToken(keyJson);
    if (token == null) {
      return FinanceSheetMonthData(ok: false, tab: tab, year: year, month: month, error: tokErr);
    }
    try {
      var ranges = _batchRanges(tab);
      var resp = await _batchGet(token, sheetId, ranges);
      var avail = <String>[];
      if (resp.statusCode == 400) {
        final (matched, list) = await _resolveMonthTab(token, sheetId, month);
        avail = list;
        if (matched != null && matched != tab) {
          tab = matched;
          ranges = _batchRanges(tab);
          resp = await _batchGet(token, sheetId, ranges);
        }
      }
      if (resp.statusCode != 200) {
        return FinanceSheetMonthData(
          ok: false,
          tab: tab,
          year: year,
          month: month,
          error: _friendlyError(resp.statusCode, email, tab, avail),
        );
      }
      final body = jsonDecode(resp.body) as Map<String, dynamic>;
      final vrs = (body['valueRanges'] as List? ?? const []);
      final blocks = List<List<List<dynamic>>>.generate(ranges.length, (i) {
        if (i >= vrs.length) return [];
        final vals = (vrs[i] as Map)['values'] as List? ?? const [];
        return vals.map((r) => List<dynamic>.from(r as List)).toList();
      });
      return parseBatchResponse(tab: tab, year: year, month: month, blocks: blocks);
    } catch (e) {
      return FinanceSheetMonthData(
        ok: false,
        tab: tab,
        year: year,
        month: month,
        error: 'Failed to read Google Sheets (${e.runtimeType}).',
      );
    }
  }

  Future<FinanceSheetWriteResult> writeTransaction({
    required String rawSheetId,
    required String keyJson,
    required String type,
    required int amount,
    required String title,
    String category = 'Jajan',
    String wallet = 'Tunai',
    String toWallet = '',
    required String dateStr,
  }) async {
    final parsedDt = DateTime.tryParse(dateStr) ?? DateTime.now();
    var tab = monthTabName(parsedDt.month);
    final sheetId = extractSpreadsheetId(rawSheetId);
    if (sheetId.isEmpty) return FinanceSheetWriteResult(ok: false, tab: tab, error: 'Spreadsheet ID not set.');
    if (amount <= 0) return FinanceSheetWriteResult(ok: false, tab: tab, error: 'Amount must be positive.');

    final cleanTitle = title.trim().length > 100 ? title.trim().substring(0, 100) : title.trim();
    final cat = kFinanceSheetCatCols.containsKey(category) ? category : 'Jajan';

    // Formula guard
    if (type == 'income') {
      final allowed = kBankWords.contains(wallet) ? wallet : null;
      final bad = forbiddenBankWord(cleanTitle, allowedBank: allowed);
      if (bad != null) {
        return FinanceSheetWriteResult(
          ok: false,
          tab: tab,
          error: allowed != null
              ? "Note cannot include '$bad' in a $allowed deposit (protects Sheet formulas)."
              : "Cash income cannot include '$bad' (Sheets would count it as bank income).",
        );
      }
    } else if (type == 'transfer') {
      final bad = forbiddenBankWord(cleanTitle);
      if (bad != null) {
        return FinanceSheetWriteResult(
          ok: false,
          tab: tab,
          error: "Transfer note cannot include '$bad' (protects Sheet bank formulas).",
        );
      }
    }

    final (token, email, tokErr) = await _getAccessToken(keyJson);
    if (token == null) return FinanceSheetWriteResult(ok: false, tab: tab, error: tokErr);

    try {
      // Resolve exact month tab if case/language differs
      final probe = await _valuesGet(token, sheetId, _a1(tab, 'A1:A1'));
      if (probe.statusCode == 400) {
        final (matched, avail) = await _resolveMonthTab(token, sheetId, parsedDt.month);
        if (matched == null) {
          return FinanceSheetWriteResult(ok: false, tab: tab, error: _friendlyError(400, email, tab, avail));
        }
        tab = matched;
      } else if (probe.statusCode != 200) {
        return FinanceSheetWriteResult(ok: false, tab: tab, error: _friendlyError(probe.statusCode, email, tab));
      }

      if (type == 'expense') {
        final (colA, colC) = kFinanceSheetCatCols[cat]!;
        final name = cleanTitle.isEmpty ? cat : cleanTitle;
        final expColRng = _a1(tab, '$colA$kExpenseRowStart:$colA');

        if (kQrisSourceByBank.containsKey(wallet)) {
          final qrisSrc = kQrisSourceByBank[wallet]!;
          final incRng = _a1(tab, 'B$kIncomeRowStart:D$kIncomeRowEnd');
          final br = await _batchGet(token, sheetId, [incRng, expColRng]);
          if (br.statusCode != 200) {
            return FinanceSheetWriteResult(ok: false, tab: tab, error: _friendlyError(br.statusCode, email, tab));
          }
          final vrs = ((jsonDecode(br.body) as Map)['valueRanges'] as List? ?? const []);
          final incRows = ((vrs[0] as Map)['values'] as List? ?? const []).map((r) => List<dynamic>.from(r as List)).toList();
          final expCol = ((vrs[1] as Map)['values'] as List? ?? const []).map((r) => List<dynamic>.from(r as List)).toList();

          final erows = _findEmptyRows(expCol, kExpenseRowStart, kExpenseRowEnd, 1);
          if (erows.isEmpty) return FinanceSheetWriteResult(ok: false, tab: tab, error: "Table '$cat' in '$tab' is full.");
          final erow = erows.first;

          int? qRow;
          int? oldQVal;
          final incColB = <List<dynamic>>[];
          for (var i = 0; i < incRows.length; i++) {
            final r = incRows[i];
            incColB.add([r.isNotEmpty ? r[0] : '']);
            if (qRow == null &&
                r.length >= 3 &&
                r[0].toString().trim().toLowerCase() == qrisSrc.toLowerCase() &&
                _isNum(r[2])) {
              qRow = kIncomeRowStart + i;
              oldQVal = (r[2] as num).round();
            }
          }

          final cells = <Map<String, dynamic>>[];
          final erange = '$colA$erow:$colC$erow';
          if (qRow == null) {
            final qrows = _findEmptyRows(incColB, kIncomeRowStart, kIncomeRowEnd, 1);
            if (qrows.isEmpty) {
              return FinanceSheetWriteResult(ok: false, tab: tab, error: 'Pemasukan table (B10:D31) is full.');
            }
            final qr = qrows.first;
            final qrng = 'B$qr:D$qr';
            await _valuesBatchUpdate(token, sheetId, [
              {
                'range': _a1(tab, qrng),
                'values': [
                  [safeSheetText(qrisSrc), dateStr, amount]
                ],
              },
              {
                'range': _a1(tab, erange),
                'values': [
                  [safeSheetText(name), dateStr, amount]
                ],
              },
            ]);
            cells.add({'tab': tab, 'range': qrng, 'name': qrisSrc, 'amount': amount});
          } else {
            final newVal = oldQVal! + amount;
            await _valuesBatchUpdate(token, sheetId, [
              {
                'range': _a1(tab, 'D$qRow'),
                'values': [
                  [newVal]
                ],
              },
              {
                'range': _a1(tab, erange),
                'values': [
                  [safeSheetText(name), dateStr, amount]
                ],
              },
            ]);
            cells.add({
              'tab': tab,
              'range': 'B$qRow:D$qRow',
              'name': qrisSrc,
              'amount': newVal,
              'prev_amount': oldQVal,
              'kind': 'increment',
            });
          }
          cells.add({'tab': tab, 'range': erange, 'name': name, 'amount': amount});
          return FinanceSheetWriteResult(
            ok: true,
            tab: tab,
            cells: cells,
            message: "Saved QRIS ($wallet) '$name' to $tab",
          );
        }

        // Standard Tunai expense
        final r = await _valuesGet(token, sheetId, expColRng);
        final vals = ((jsonDecode(r.body) as Map)['values'] as List? ?? const [])
            .map((e) => List<dynamic>.from(e as List))
            .toList();
        final erows = _findEmptyRows(vals, kExpenseRowStart, kExpenseRowEnd, 1);
        if (erows.isEmpty) return FinanceSheetWriteResult(ok: false, tab: tab, error: "Table '$cat' is full.");
        final erow = erows.first;
        final erange = '$colA$erow:$colC$erow';
        final upd = await _valuesUpdate(token, sheetId, _a1(tab, erange), [
          [safeSheetText(name), dateStr, amount]
        ]);
        if (upd.statusCode != 200) {
          return FinanceSheetWriteResult(ok: false, tab: tab, error: _friendlyError(upd.statusCode, email, tab));
        }
        return FinanceSheetWriteResult(
          ok: true,
          tab: tab,
          cells: [
            {'tab': tab, 'range': erange, 'name': name, 'amount': amount}
          ],
          message: "Saved '$name' to $tab · $cat",
        );
      }

      if (type == 'income') {
        if (kBankWords.contains(wallet)) {
          final label = _formatBankIncomeLabel(wallet, cleanTitle);
          final r = await _valuesGet(token, sheetId, _a1(tab, 'T$kBankRowStart:T$kBankRowEnd'));
          final vals = ((jsonDecode(r.body) as Map)['values'] as List? ?? const [])
              .map((e) => List<dynamic>.from(e as List))
              .toList();
          final rows = _findEmptyRows(vals, kBankRowStart, kBankRowEnd, 1);
          if (rows.isEmpty) return FinanceSheetWriteResult(ok: false, tab: tab, error: 'Pemasukan Bank table is full.');
          final rng = 'T${rows.first}:V${rows.first}';
          await _valuesUpdate(token, sheetId, _a1(tab, rng), [
            [safeSheetText(label), dateStr, amount]
          ]);
          return FinanceSheetWriteResult(
            ok: true,
            tab: tab,
            cells: [
              {'tab': tab, 'range': rng, 'name': label, 'amount': amount}
            ],
            message: "Saved bank income '$label' to $tab",
          );
        }
        final label = cleanTitle.isEmpty ? 'Pemasukan' : cleanTitle;
        final r = await _valuesGet(token, sheetId, _a1(tab, 'B$kIncomeRowStart:B$kIncomeRowEnd'));
        final vals = ((jsonDecode(r.body) as Map)['values'] as List? ?? const [])
            .map((e) => List<dynamic>.from(e as List))
            .toList();
        final rows = _findEmptyRows(vals, kIncomeRowStart, kIncomeRowEnd, 1);
        if (rows.isEmpty) return FinanceSheetWriteResult(ok: false, tab: tab, error: 'Pemasukan table is full.');
        final rng = 'B${rows.first}:D${rows.first}';
        await _valuesUpdate(token, sheetId, _a1(tab, rng), [
          [safeSheetText(label), dateStr, amount]
        ]);
        return FinanceSheetWriteResult(
          ok: true,
          tab: tab,
          cells: [
            {'tab': tab, 'range': rng, 'name': label, 'amount': amount}
          ],
          message: "Saved cash income '$label' to $tab",
        );
      }

      if (type == 'transfer') {
        if (wallet == toWallet) {
          return FinanceSheetWriteResult(ok: false, tab: tab, error: 'Choose two different wallets.');
        }
        final extra = cleanTitle.isNotEmpty ? ' - $cleanTitle' : '';
        if (kBankWords.contains(wallet) && toWallet == 'Tunai') {
          final label = 'Tarik Uang $wallet$extra';
          final r = await _valuesGet(token, sheetId, _a1(tab, 'B$kIncomeRowStart:B$kIncomeRowEnd'));
          final vals = ((jsonDecode(r.body) as Map)['values'] as List? ?? const [])
              .map((e) => List<dynamic>.from(e as List))
              .toList();
          final rows = _findEmptyRows(vals, kIncomeRowStart, kIncomeRowEnd, 1);
          if (rows.isEmpty) return FinanceSheetWriteResult(ok: false, tab: tab, error: 'Pemasukan table is full.');
          final rng = 'B${rows.first}:D${rows.first}';
          await _valuesUpdate(token, sheetId, _a1(tab, rng), [
            [safeSheetText(label), dateStr, amount]
          ]);
          return FinanceSheetWriteResult(
            ok: true,
            tab: tab,
            cells: [
              {'tab': tab, 'range': rng, 'name': label, 'amount': amount}
            ],
            message: 'Saved transfer $wallet → Tunai to $tab',
          );
        }
        if (wallet == 'Tunai' && kBankWords.contains(toWallet)) {
          final br = await _batchGet(token, sheetId, [
            _a1(tab, 'B$kIncomeRowStart:B$kIncomeRowEnd'),
            _a1(tab, 'T$kBankRowStart:T$kBankRowEnd'),
          ]);
          final vrs = ((jsonDecode(br.body) as Map)['valueRanges'] as List? ?? const []);
          final cRows = _findEmptyRows(
            ((vrs[0] as Map)['values'] as List? ?? const []).map((e) => List<dynamic>.from(e as List)).toList(),
            kIncomeRowStart,
            kIncomeRowEnd,
            1,
          );
          final bRows = _findEmptyRows(
            ((vrs[1] as Map)['values'] as List? ?? const []).map((e) => List<dynamic>.from(e as List)).toList(),
            kBankRowStart,
            kBankRowEnd,
            1,
          );
          if (cRows.isEmpty || bRows.isEmpty) {
            return FinanceSheetWriteResult(ok: false, tab: tab, error: 'Pemasukan / Bank table is full.');
          }
          final lblC = 'Setor Tunai$extra';
          final lblB = '$toWallet (setor tunai$extra)';
          final rngC = 'B${cRows.first}:D${cRows.first}';
          final rngB = 'T${bRows.first}:V${bRows.first}';
          await _valuesBatchUpdate(token, sheetId, [
            {
              'range': _a1(tab, rngC),
              'values': [
                [safeSheetText(lblC), dateStr, -amount]
              ],
            },
            {
              'range': _a1(tab, rngB),
              'values': [
                [safeSheetText(lblB), dateStr, amount]
              ],
            },
          ]);
          return FinanceSheetWriteResult(
            ok: true,
            tab: tab,
            cells: [
              {'tab': tab, 'range': rngC, 'name': lblC, 'amount': -amount},
              {'tab': tab, 'range': rngB, 'name': lblB, 'amount': amount},
            ],
            message: 'Saved transfer Tunai → $toWallet to $tab',
          );
        }
        // Bank <-> Bank
        final r = await _valuesGet(token, sheetId, _a1(tab, 'T$kBankRowStart:T$kBankRowEnd'));
        final vals = ((jsonDecode(r.body) as Map)['values'] as List? ?? const [])
            .map((e) => List<dynamic>.from(e as List))
            .toList();
        final rows = _findEmptyRows(vals, kBankRowStart, kBankRowEnd, 2);
        if (rows.length < 2) {
          return FinanceSheetWriteResult(ok: false, tab: tab, error: 'Bank table needs 2 free rows.');
        }
        final lblOut = '$wallet (tf keluar$extra)';
        final lblIn = '$toWallet (tf masuk$extra)';
        final rngOut = 'T${rows[0]}:V${rows[0]}';
        final rngIn = 'T${rows[1]}:V${rows[1]}';
        await _valuesBatchUpdate(token, sheetId, [
          {
            'range': _a1(tab, rngOut),
            'values': [
              [safeSheetText(lblOut), dateStr, -amount]
            ],
          },
          {
            'range': _a1(tab, rngIn),
            'values': [
              [safeSheetText(lblIn), dateStr, amount]
            ],
          },
        ]);
        return FinanceSheetWriteResult(
          ok: true,
          tab: tab,
          cells: [
            {'tab': tab, 'range': rngOut, 'name': lblOut, 'amount': -amount},
            {'tab': tab, 'range': rngIn, 'name': lblIn, 'amount': amount},
          ],
          message: 'Saved transfer $wallet → $toWallet to $tab',
        );
      }
      return FinanceSheetWriteResult(ok: false, tab: tab, error: 'Unsupported type: $type');
    } catch (e) {
      return FinanceSheetWriteResult(ok: false, tab: tab, error: 'Sheets write failed (${e.runtimeType}).');
    }
  }

  Future<FinanceSheetWriteResult> clearCells({
    required String rawSheetId,
    required String keyJson,
    required List<Map<String, dynamic>> cells,
  }) async {
    final sheetId = extractSpreadsheetId(rawSheetId);
    final tab = cells.isNotEmpty ? (cells.first['tab'] ?? '').toString() : '';
    final (token, _, err) = await _getAccessToken(keyJson);
    if (token == null) return FinanceSheetWriteResult(ok: false, tab: tab, error: err);
    try {
      for (final c in cells) {
        final t = (c['tab'] ?? tab).toString();
        final rng = (c['range'] ?? '').toString();
        if (c['kind'] == 'increment' && c['prev_amount'] != null) {
          final m = RegExp(r'D(\d+)$').firstMatch(rng);
          if (m != null) {
            await _valuesUpdate(token, sheetId, _a1(t, 'D${m.group(1)}'), [
              [c['prev_amount']]
            ]);
          }
        } else if (rng.isNotEmpty) {
          await _valuesClear(token, sheetId, _a1(t, rng));
        }
      }
      return FinanceSheetWriteResult(ok: true, tab: tab, message: 'Cleared row in Google Sheets.');
    } catch (e) {
      return FinanceSheetWriteResult(ok: false, tab: tab, error: 'Sheets delete failed (${e.runtimeType}).');
    }
  }

  Future<(bool, String)> testConnection({required String rawSheetId, required String keyJson}) async {
    final now = DateTime.now();
    final res = await fetchMonth(rawSheetId: rawSheetId, keyJson: keyJson, year: now.year, month: now.month);
    if (!res.ok) return (false, res.error);
    return (true, "Connected! Tab '${res.tab}' ready (${formatRupiah(res.balances['Total'] ?? 0)})");
  }
}
