import 'dart:math';

// ignore: deprecated_member_use
import 'package:flutter/material.dart';

import '../core/storage.dart';
import '../theme/p5_theme.dart';

/// Persona 5 Royal animated Golden Star Social Stats Pentagon chart.
class StatPentagonWidget extends StatelessWidget {
  final Map<String, int> totals;
  final String selectedStat;
  final void Function(String statKey) onSelectStat;

  const StatPentagonWidget({
    super.key,
    required this.totals,
    required this.selectedStat,
    required this.onSelectStat,
  });

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 220,
      width: double.infinity,
      child: LayoutBuilder(
        builder: (context, constraints) {
          final size = Size(constraints.maxWidth, constraints.maxHeight);
          final cx = size.width / 2;
          final cy = size.height / 2 + 6;
          final radius = min(size.width * 0.31, size.height * 0.35);

          return Stack(
            children: [
              Positioned.fill(
                child: CustomPaint(
                  painter: _PentagonPainter(
                    totals: totals,
                    selectedStat: selectedStat,
                    center: Offset(cx, cy),
                    radius: radius,
                  ),
                ),
              ),
              for (var i = 0; i < kSocialStatKeys.length; i++)
                _buildVertexButton(i, cx, cy, radius),
            ],
          );
        },
      ),
    );
  }

  Widget _buildVertexButton(int idx, double cx, double cy, double radius) {
    final key = kSocialStatKeys[idx];
    final meta = kSocialStats[key]!;
    final angle = -pi / 2 + idx * (2 * pi / 5);
    final dist = radius + 28;
    final x = cx + cos(angle) * dist;
    final y = cy + sin(angle) * dist;
    final isSel = key == selectedStat;
    final lv = statLevel(totals[key] ?? 0);

    return Positioned(
      left: x - 34,
      top: y - 15,
      width: 68,
      height: 30,
      child: GestureDetector(
        onTap: () => onSelectStat(key),
        child: Container(
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: isSel ? P5Colors.goldBg : P5Colors.black,
            borderRadius: BorderRadius.circular(6),
            border: Border.all(color: isSel ? P5Colors.gold : P5Colors.border, width: isSel ? 1.5 : 1.0),
          ),
          child: Text(
            '${meta.short} L$lv',
            style: P5Text.display(11, color: isSel ? P5Colors.gold : P5Colors.white),
          ),
        ),
      ),
    );
  }
}

class _PentagonPainter extends CustomPainter {
  final Map<String, int> totals;
  final String selectedStat;
  final Offset center;
  final double radius;

  _PentagonPainter({
    required this.totals,
    required this.selectedStat,
    required this.center,
    required this.radius,
  });

  Offset _pt(int idx, double r) {
    final a = -pi / 2 + idx * (2 * pi / 5);
    return Offset(center.dx + cos(a) * r, center.dy + sin(a) * r);
  }

  @override
  void paint(Canvas canvas, Size size) {
    // 5 concentric Persona 5 crimson/obsidian pentagon rings
    for (var ring = 5; ring >= 1; ring--) {
      final r = radius * (ring / 5.0);
      final path = Path();
      for (var i = 0; i < 5; i++) {
        final p = _pt(i, r);
        if (i == 0) {
          path.moveTo(p.dx, p.dy);
        } else {
          path.lineTo(p.dx, p.dy);
        }
      }
      path.close();
      if (ring == 5) {
        canvas.drawPath(path, Paint()..color = const Color(0xFF1A0B10));
      }
      canvas.drawPath(
        path,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = ring == 5 ? 1.8 : 0.9
          ..color = ring == 5 ? P5Colors.red : P5Colors.border,
      );
    }

    // Spokes from center
    final spokePaint = Paint()
      ..color = P5Colors.border.withValues(alpha: 0.7)
      ..strokeWidth = 0.9;
    for (var i = 0; i < 5; i++) {
      canvas.drawLine(center, _pt(i, radius), spokePaint);
    }

    // Golden Persona 5 stat star polygon
    final starPath = Path();
    for (var i = 0; i < 5; i++) {
      final frac = statPentagonFraction(totals[kSocialStatKeys[i]] ?? 0);
      final p = _pt(i, radius * frac);
      if (i == 0) {
        starPath.moveTo(p.dx, p.dy);
      } else {
        starPath.lineTo(p.dx, p.dy);
      }
    }
    starPath.close();

    canvas.drawPath(
      starPath,
      Paint()..color = P5Colors.gold.withValues(alpha: 0.32),
    );
    canvas.drawPath(
      starPath,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.2
        ..color = P5Colors.gold,
    );

    // Highlight vertices
    for (var i = 0; i < 5; i++) {
      final key = kSocialStatKeys[i];
      final frac = statPentagonFraction(totals[key] ?? 0);
      final p = _pt(i, radius * frac);
      final isSel = key == selectedStat;
      canvas.drawCircle(p, isSel ? 5.0 : 3.2, Paint()..color = isSel ? P5Colors.white : P5Colors.gold);
      if (isSel) {
        canvas.drawCircle(
          p,
          8.5,
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1.4
            ..color = P5Colors.gold,
        );
      }
    }
  }

  @override
  bool shouldRepaint(_PentagonPainter old) =>
      old.selectedStat != selectedStat || old.totals != totals || old.radius != radius;
}
