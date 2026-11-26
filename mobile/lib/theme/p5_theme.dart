// ignore: deprecated_member_use
import 'package:flutter/material.dart';

/// Persona 5 Royal cohesive dark palette matching desktop Morgana Companion v1.5.3.
class P5Colors {
  static const Color bg = Color(0xFF0C0C10);
  static const Color bgDeep = Color(0xFF09090E);
  static const Color panel = Color(0xFF101017);
  static const Color card = Color(0xFF14141C);
  static const Color cardTop = Color(0xFF171722);
  static const Color subcard = Color(0xFF1B1B26);
  static const Color surface = Color(0xFF1D1D28);
  static const Color hover = Color(0xFF282838);
  static const Color selected = Color(0xFF2D1218);

  static const Color border = Color(0xFF2D2D3D);
  static const Color borderHi = Color(0xFF45455C);

  static const Color red = Color(0xFFE60012);
  static const Color redHover = Color(0xFFFF1E2F);
  static const Color deepRed = Color(0xFF9C000D);
  static const Color redBg = Color(0xFF360A10);
  static const Color redFg = Color(0xFFFF5E6B);

  static const Color gold = Color(0xFFFAD02C);
  static const Color goldWarm = Color(0xFFF5C400);
  static const Color goldBg = Color(0xFF2B2208);
  static const Color goldBorder = Color(0xFF6C5514);
  static const Color goldFg = Color(0xFFFFE46B);

  static const Color mint = Color(0xFF5CE09A);
  static const Color mintBg = Color(0xFF10281E);
  static const Color mintBorder = Color(0xFF245943);

  static const Color white = Color(0xFFFFFFFF);
  static const Color black = Color(0xFF0A0A0D);
  static const Color text = Color(0xFFF2F2F8);
  static const Color muted = Color(0xFF9898AC);
  static const Color dim = Color(0xFF68687C);
}

class P5Text {
  static TextStyle heading(double size, {Color color = P5Colors.white}) => TextStyle(
        fontFamily: 'Anton',
        fontSize: size,
        color: color,
        letterSpacing: 0.6,
        height: 1.08,
      );

  static TextStyle display(double size, {Color color = P5Colors.white, bool bold = true}) => TextStyle(
        fontFamily: 'BarlowCondensed',
        fontSize: size,
        fontWeight: bold ? FontWeight.w800 : FontWeight.w700,
        color: color,
        letterSpacing: 0.5,
        height: 1.12,
      );

  static TextStyle body(double size, {Color color = P5Colors.text, bool bold = false, bool italic = false}) => TextStyle(
        fontFamily: 'Arsenal',
        fontSize: size,
        fontWeight: bold ? FontWeight.w700 : FontWeight.w400,
        fontStyle: italic ? FontStyle.italic : FontStyle.normal,
        color: color,
        height: 1.32,
      );
}

ThemeData buildP5Theme() {
  return ThemeData(
    useMaterial3: true,
    brightness: Brightness.dark,
    scaffoldBackgroundColor: P5Colors.bg,
    fontFamily: 'Arsenal',
    colorScheme: const ColorScheme.dark(
      primary: P5Colors.red,
      secondary: P5Colors.gold,
      tertiary: P5Colors.mint,
      surface: P5Colors.card,
      onPrimary: P5Colors.white,
      onSecondary: P5Colors.black,
      onSurface: P5Colors.text,
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: P5Colors.surface,
      isDense: true,
      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 11),
      hintStyle: P5Text.body(14, color: P5Colors.dim),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(8),
        borderSide: const BorderSide(color: P5Colors.border),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(8),
        borderSide: const BorderSide(color: P5Colors.border),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(8),
        borderSide: const BorderSide(color: P5Colors.red, width: 1.6),
      ),
    ),
    dividerColor: P5Colors.border,
  );
}
