/// Indonesian Rupiah shorthand parser & formatter (100% parity with desktop `storage_manager.py`).
const List<String> kFinanceCategories = [
  'Jajan',
  'Alat',
  'Mingguan&Bulanan',
  'Hiburan',
];

const Map<String, String> kFinanceCategoryShort = {
  'Jajan': 'JAJAN',
  'Alat': 'ALAT',
  'Mingguan&Bulanan': 'MINGGUAN',
  'Hiburan': 'HIBURAN',
};

const Map<String, String> _kCategoryAliases = {
  'jajan': 'Jajan',
  'snack': 'Jajan',
  'makan': 'Jajan',
  'food': 'Jajan',
  'kopi': 'Jajan',
  'alat': 'Alat',
  'gear': 'Alat',
  'tools': 'Alat',
  'mingguan': 'Mingguan&Bulanan',
  'bulanan': 'Mingguan&Bulanan',
  'mingguan&bulanan': 'Mingguan&Bulanan',
  'essentials': 'Mingguan&Bulanan',
  'hiburan': 'Hiburan',
  'fun': 'Hiburan',
  'entertainment': 'Hiburan',
};

const List<String> kFinanceWallets = ['Tunai', 'BNI', 'Mandiri'];

const Map<String, String> _kWalletAliases = {
  'tunai': 'Tunai',
  'cash': 'Tunai',
  'dompet': 'Tunai',
  'bni': 'BNI',
  'qris': 'BNI',
  'mandiri': 'Mandiri',
};

const double kFinanceWarnRatio = 0.80;
const int kFinanceMaxAmount = 999999999999;

final RegExp _rpPrefixRe = RegExp(r'^rp\.?\s*', caseSensitive: false);
final RegExp _suffixRe = RegExp(r'^(\d+(?:[.,]\d+)?)\s*(jt|juta|m|rb|ribu|k)$', caseSensitive: false);
final RegExp _thousRe = RegExp(r'^\d{1,3}(?:[.,]\d{3})+$');
final RegExp _plainRe = RegExp(r'^\d+$');

/// Parse Indonesian Rupiah shorthand ('15rb', '1.5jt', '25k', 'Rp 25.000', '15000') -> int or null.
int? parseRupiah(Object? input) {
  if (input == null || input is bool) return null;
  if (input is int) {
    return (input > 0 && input <= kFinanceMaxAmount) ? input : null;
  }
  if (input is double) {
    if (input.isNaN || input.isInfinite || input <= 0 || input > kFinanceMaxAmount) return null;
    final val = input.round();
    return (val > 0 && val <= kFinanceMaxAmount) ? val : null;
  }
  var raw = input.toString().trim();
  if (raw.isEmpty) return null;
  raw = raw.replaceFirst(_rpPrefixRe, '').trim();
  if (raw.isEmpty || raw.startsWith('-')) return null;

  final sm = _suffixRe.firstMatch(raw);
  if (sm != null) {
    final numS = sm.group(1)!.replaceAll(',', '.');
    final suf = sm.group(2)!.toLowerCase();
    final mult = (suf == 'jt' || suf == 'juta' || suf == 'm') ? 1000000 : 1000;
    final parsed = double.tryParse(numS);
    if (parsed == null) return null;
    final val = (parsed * mult).round();
    return (val > 0 && val <= kFinanceMaxAmount) ? val : null;
  }
  if (_thousRe.hasMatch(raw)) {
    final val = int.tryParse(raw.replaceAll('.', '').replaceAll(',', ''));
    return (val != null && val > 0 && val <= kFinanceMaxAmount) ? val : null;
  }
  if (_plainRe.hasMatch(raw)) {
    final val = int.tryParse(raw);
    return (val != null && val > 0 && val <= kFinanceMaxAmount) ? val : null;
  }
  return null;
}

/// Format integer Rupiah cleanly with Indonesian dot thousand separators (e.g. Rp 15.000).
String formatRupiah(num amount) {
  final val = amount.round();
  final sign = val < 0 ? '-' : '';
  final digits = val.abs().toString();
  final buf = StringBuffer();
  for (var i = 0; i < digits.length; i++) {
    if (i > 0 && (digits.length - i) % 3 == 0) buf.write('.');
    buf.write(digits[i]);
  }
  return '${sign}Rp $buf';
}

/// Compact wallet-chip Rupiah shorthand (e.g. '195rb', '1.97jt', '-75rb').
String shortRupiah(int amount) {
  final sign = amount < 0 ? '-' : '';
  final a = amount.abs();
  if (a >= 1000000) {
    final raw = a >= 10000000 ? (a / 1000000).toStringAsFixed(1) : (a / 1000000).toStringAsFixed(2);
    final trimmed = raw.replaceFirst(RegExp(r'\.?0+$'), '');
    return '$sign${trimmed}jt';
  }
  if (a >= 1000) {
    return '$sign${(a / 1000).round()}rb';
  }
  return '$sign$a';
}

String normalizeFinanceCategory(String? value) {
  if (value == null || value.trim().isEmpty) return 'Jajan';
  final low = value.trim().toLowerCase();
  if (_kCategoryAliases.containsKey(low)) return _kCategoryAliases[low]!;
  for (final cat in kFinanceCategories) {
    if (cat.toLowerCase() == low) return cat;
  }
  return 'Jajan';
}

String normalizeFinanceWallet(String? value) {
  if (value == null || value.trim().isEmpty) return 'Tunai';
  final low = value.trim().toLowerCase();
  if (_kWalletAliases.containsKey(low)) return _kWalletAliases[low]!;
  for (final w in kFinanceWallets) {
    if (w.toLowerCase() == low) return w;
  }
  return 'Tunai';
}
