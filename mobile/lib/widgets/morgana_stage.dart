import 'dart:async';
import 'dart:math';

// ignore: deprecated_member_use
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/emotions.dart';
import '../theme/p5_theme.dart';

/// Interactive Persona 5 Joker Assist Menu stage:
/// stepped crimson lightning-slash + black counter-spears + offset red Morgana silhouette
/// + animated blinking/talking Morgana sprite + adaptive Persona 5 radial menu ribbons!
class MorganaStageWidget extends StatefulWidget {
  final String emotion;
  final bool talking;
  final bool menuOpen;
  final List<String> enabledTabs;
  final VoidCallback onTapMorgana;
  final VoidCallback onToggleMenu;
  final void Function(String tabKey) onSelectTab;

  const MorganaStageWidget({
    super.key,
    required this.emotion,
    required this.talking,
    required this.menuOpen,
    required this.enabledTabs,
    required this.onTapMorgana,
    required this.onToggleMenu,
    required this.onSelectTab,
  });

  @override
  State<MorganaStageWidget> createState() => _MorganaStageWidgetState();
}

class _MorganaStageWidgetState extends State<MorganaStageWidget> with TickerProviderStateMixin {
  late final AnimationController _bobCtrl;
  late final AnimationController _burstCtrl;
  Timer? _blinkTimer;
  Timer? _mouthTimer;
  int _eyeState = 0; // 0 open, 1 half, 2 closed
  int _mouthState = 0; // 0 base, 1..3 flap

  static const Map<String, (String label, String sub, IconData icon)> _tabMeta = {
    'tasks': ('TASKS', 'TO-DO', Icons.check_box_outlined),
    'notes': ('NOTES', 'MEMO', Icons.description_outlined),
    'events': ('EVENTS', '7 DAYS', Icons.calendar_today_outlined),
    'stats': ('STATS', 'RANK', Icons.star_outline),
    'timer': ('TIMER', 'FOCUS', Icons.timer_outlined),
    'finance': ('FINANCE', 'WALLET', Icons.account_balance_wallet_outlined),
    'ask': ('ASK', 'CMD/AI', Icons.chat_bubble_outline),
    'settings': ('CONFIG', 'SETUP', Icons.tune),
  };

  @override
  void initState() {
    super.initState();
    _bobCtrl = AnimationController(vsync: this, duration: const Duration(milliseconds: 2400))..repeat(reverse: true);
    _burstCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 360),
      value: widget.menuOpen ? 1.0 : 0.0,
    );
    _scheduleBlink();
    _syncMouthTimer();
  }

  @override
  void didUpdateWidget(MorganaStageWidget oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.menuOpen != widget.menuOpen) {
      if (widget.menuOpen) {
        _burstCtrl.forward();
      } else {
        _burstCtrl.reverse();
      }
    }
    if (oldWidget.talking != widget.talking) {
      _syncMouthTimer();
    }
  }

  void _scheduleBlink() {
    _blinkTimer?.cancel();
    _blinkTimer = Timer(Duration(milliseconds: 2600 + Random().nextInt(2600)), () {
      if (!mounted) return;
      setState(() => _eyeState = 2);
      _blinkTimer = Timer(const Duration(milliseconds: 115), () {
        if (!mounted) return;
        setState(() => _eyeState = 0);
        _scheduleBlink();
      });
    });
  }

  void _syncMouthTimer() {
    _mouthTimer?.cancel();
    if (!widget.talking) {
      if (_mouthState != 0 && mounted) setState(() => _mouthState = 0);
      return;
    }
    var step = 0;
    _mouthTimer = Timer.periodic(const Duration(milliseconds: 110), (_) {
      if (!mounted) return;
      step = (step + 1) % 4;
      setState(() => _mouthState = step == 0 ? 1 : (step == 2 ? 3 : 2));
    });
  }

  @override
  void dispose() {
    _blinkTimer?.cancel();
    _mouthTimer?.cancel();
    _bobCtrl.dispose();
    _burstCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final tabs = widget.enabledTabs.where(_tabMeta.containsKey).toList();
    final half = (tabs.length + 1) ~/ 2;
    final leftTabs = tabs.sublist(0, half);
    final rightTabs = tabs.sublist(half);

    return LayoutBuilder(
      builder: (context, constraints) {
        final w = constraints.maxWidth;
        final h = constraints.maxHeight.clamp(250.0, 340.0);
        final spriteSize = (w * 0.44).clamp(132.0, 186.0);

        return SizedBox(
          width: w,
          height: h,
          child: Stack(
            alignment: Alignment.center,
            clipBehavior: Clip.none,
            children: [
              // 1) Stepped Persona 5 Joker Assist Menu Spikes behind Morgana
              Positioned.fill(
                child: AnimatedBuilder(
                  animation: Listenable.merge([_burstCtrl, _bobCtrl]),
                  builder: (_, _) => CustomPaint(
                    painter: JokerSpikesPainter(
                      burst: Curves.easeOutBack.transform(_burstCtrl.value).clamp(0.0, 1.12),
                      pulse: _bobCtrl.value,
                      hasLeft: leftTabs.isNotEmpty,
                      hasRight: rightTabs.isNotEmpty,
                    ),
                  ),
                ),
              ),

              // 2) Offset crimson Joker silhouette + live Morgana sprite
              AnimatedBuilder(
                animation: Listenable.merge([_bobCtrl, _burstCtrl]),
                builder: (_, _) {
                  final dy = sin(_bobCtrl.value * pi) * -5.0;
                  final burst = _burstCtrl.value;
                  return Transform.translate(
                    offset: Offset(0, dy),
                    child: GestureDetector(
                      onTap: () {
                        HapticFeedback.lightImpact();
                        widget.onTapMorgana();
                      },
                      onLongPress: () {
                        HapticFeedback.mediumImpact();
                        widget.onToggleMenu();
                      },
                      child: SizedBox(
                        width: spriteSize,
                        height: spriteSize,
                        child: Stack(
                          alignment: Alignment.center,
                          clipBehavior: Clip.none,
                          children: [
                            // Signature Joker Assist Menu crimson offset silhouette shadow
                            if (burst > 0.04)
                              Transform.translate(
                                offset: Offset(-10.0 * burst, -1.5 * burst),
                                child: Opacity(
                                  opacity: (0.86 * burst).clamp(0.0, 0.9),
                                  child: ColorFiltered(
                                    colorFilter: const ColorFilter.mode(P5Colors.red, BlendMode.srcATop),
                                    child: _MorganaSpriteLayer(
                                      emotion: widget.emotion,
                                      eyeState: 0,
                                      mouthState: 0,
                                      size: spriteSize,
                                    ),
                                  ),
                                ),
                              ),
                            _MorganaSpriteLayer(
                              emotion: widget.emotion,
                              eyeState: _eyeState,
                              mouthState: _mouthState,
                              size: spriteSize,
                            ),
                          ],
                        ),
                      ),
                    ),
                  );
                },
              ),

              // 3) Left adaptive Persona 5 radial ribbons
              Positioned(
                left: 4,
                top: 10,
                bottom: 10,
                width: (w * 0.34).clamp(108.0, 152.0),
                child: AnimatedBuilder(
                  animation: _burstCtrl,
                  builder: (_, child) {
                    final v = Curves.easeOutCubic.transform(_burstCtrl.value);
                    return IgnorePointer(
                      ignoring: v < 0.3,
                      child: Opacity(
                        opacity: v,
                        child: Transform.translate(offset: Offset(28 * (1 - v), 0), child: child),
                      ),
                    );
                  },
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      for (var i = 0; i < leftTabs.length; i++)
                        _RadialRibbon(
                          label: _tabMeta[leftTabs[i]]!.$1,
                          sub: _tabMeta[leftTabs[i]]!.$2,
                          icon: _tabMeta[leftTabs[i]]!.$3,
                          tiltDeg: -6.0 + i * 3.8,
                          highlight: leftTabs[i] == 'finance' || leftTabs[i] == 'ask',
                          alignRight: false,
                          onTap: () => widget.onSelectTab(leftTabs[i]),
                        ),
                    ],
                  ),
                ),
              ),

              // 4) Right adaptive Persona 5 radial ribbons
              Positioned(
                right: 4,
                top: 10,
                bottom: 10,
                width: (w * 0.34).clamp(108.0, 152.0),
                child: AnimatedBuilder(
                  animation: _burstCtrl,
                  builder: (_, child) {
                    final v = Curves.easeOutCubic.transform(_burstCtrl.value);
                    return IgnorePointer(
                      ignoring: v < 0.3,
                      child: Opacity(
                        opacity: v,
                        child: Transform.translate(offset: Offset(-28 * (1 - v), 0), child: child),
                      ),
                    );
                  },
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      for (var i = 0; i < rightTabs.length; i++)
                        _RadialRibbon(
                          label: _tabMeta[rightTabs[i]]!.$1,
                          sub: _tabMeta[rightTabs[i]]!.$2,
                          icon: _tabMeta[rightTabs[i]]!.$3,
                          tiltDeg: 6.0 - i * 3.8,
                          highlight: rightTabs[i] == 'finance' || rightTabs[i] == 'ask',
                          alignRight: true,
                          onTap: () => widget.onSelectTab(rightTabs[i]),
                        ),
                    ],
                  ),
                ),
              ),

              // 5) Toggle spikes / radial pill button at bottom-center
              Positioned(
                bottom: 0,
                child: GestureDetector(
                  onTap: () {
                    HapticFeedback.selectionClick();
                    widget.onToggleMenu();
                  },
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
                    decoration: BoxDecoration(
                      color: P5Colors.black,
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: widget.menuOpen ? P5Colors.red : P5Colors.gold, width: 1.2),
                    ),
                    child: Text(
                      widget.menuOpen ? 'TAP MORGANA TO TALK · HIDE MENU' : 'TAP TO OPEN ASSIST MENU',
                      style: P5Text.display(10, color: widget.menuOpen ? P5Colors.white : P5Colors.gold),
                    ),
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _MorganaSpriteLayer extends StatelessWidget {
  final String emotion;
  final int eyeState;
  final int mouthState;
  final double size;

  const _MorganaSpriteLayer({
    required this.emotion,
    required this.eyeState,
    required this.mouthState,
    required this.size,
  });

  @override
  Widget build(BuildContext context) {
    final meta = kMorganaExpressions[emotion] ?? kMorganaExpressions['neutral']!;
    final layout = meta.layout;
    // Match desktop STAGE normalization (srcSize 768 or 1536)
    final scale = size / layout.srcSize;
    return SizedBox(
      width: size,
      height: size,
      child: Stack(
        clipBehavior: Clip.none,
        children: [
          Image.asset(
            meta.baseAsset(),
            width: size,
            height: size,
            fit: BoxFit.contain,
            gaplessPlayback: true,
            errorBuilder: (_, _, _) => Center(
              child: Icon(Icons.pets, size: size * 0.55, color: P5Colors.gold),
            ),
          ),
          if (meta.hasEyes && eyeState > 0)
            Positioned(
              left: layout.eyeX * scale,
              top: layout.eyeY * scale,
              child: IgnorePointer(
                child: Image.asset(
                  meta.eyeAsset(eyeState),
                  scale: 1.0 / scale,
                  gaplessPlayback: true,
                  errorBuilder: (_, _, _) => const SizedBox.shrink(),
                ),
              ),
            ),
          if (mouthState > 0)
            Positioned(
              left: layout.mouthX * scale,
              top: layout.mouthY * scale,
              child: IgnorePointer(
                child: Image.asset(
                  meta.mouthAsset(mouthState),
                  scale: 1.0 / scale,
                  gaplessPlayback: true,
                  errorBuilder: (_, _, _) => const SizedBox.shrink(),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _RadialRibbon extends StatefulWidget {
  final String label;
  final String sub;
  final IconData icon;
  final double tiltDeg;
  final bool highlight;
  final bool alignRight;
  final VoidCallback onTap;

  const _RadialRibbon({
    required this.label,
    required this.sub,
    required this.icon,
    required this.tiltDeg,
    required this.highlight,
    required this.alignRight,
    required this.onTap,
  });

  @override
  State<_RadialRibbon> createState() => _RadialRibbonState();
}

class _RadialRibbonState extends State<_RadialRibbon> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    final bg = _pressed ? P5Colors.red : P5Colors.black;
    final border = _pressed ? P5Colors.white : (widget.highlight ? P5Colors.gold : P5Colors.white);
    return GestureDetector(
      onTapDown: (_) => setState(() => _pressed = true),
      onTapUp: (_) => setState(() => _pressed = false),
      onTapCancel: () => setState(() => _pressed = false),
      onTap: () {
        HapticFeedback.lightImpact();
        widget.onTap();
      },
      child: Transform.rotate(
        angle: widget.tiltDeg * pi / 180.0,
        child: AnimatedScale(
          scale: _pressed ? 0.94 : 1.0,
          duration: const Duration(milliseconds: 70),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
            decoration: BoxDecoration(
              color: bg,
              border: Border.all(color: border, width: 1.6),
              boxShadow: [
                BoxShadow(
                  color: widget.highlight ? P5Colors.red.withValues(alpha: 0.7) : Colors.black.withValues(alpha: 0.7),
                  offset: Offset(widget.alignRight ? -3 : 3, 3),
                ),
              ],
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(widget.icon, size: 13, color: widget.highlight && !_pressed ? P5Colors.gold : P5Colors.white),
                const SizedBox(width: 4),
                Flexible(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        widget.label,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: P5Text.heading(12.5, color: P5Colors.white),
                      ),
                      Text(
                        widget.sub,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: P5Text.display(8.5, color: widget.highlight && !_pressed ? P5Colors.gold : P5Colors.muted),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Port of desktop `main.py` Persona 5 Joker Assist Menu stepped crimson lightning-slash & obsidian counter-spears.
class JokerSpikesPainter extends CustomPainter {
  final double burst;
  final double pulse;
  final bool hasLeft;
  final bool hasRight;

  JokerSpikesPainter({
    required this.burst,
    required this.pulse,
    required this.hasLeft,
    required this.hasRight,
  });

  Path _orientedPoly(Offset c, double angle, List<Offset> pts) {
    final ca = cos(angle);
    final sa = sin(angle);
    final path = Path();
    for (var i = 0; i < pts.length; i++) {
      final px = pts[i].dx;
      final py = pts[i].dy;
      final rx = c.dx + px * ca - py * sa;
      final ry = c.dy + px * sa + py * ca;
      if (i == 0) {
        path.moveTo(rx, ry);
      } else {
        path.lineTo(rx, ry);
      }
    }
    path.close();
    return path;
  }

  @override
  void paint(Canvas canvas, Size size) {
    final opacity = (burst * 1.4).clamp(0.0, 1.0);
    if (opacity <= 0.01) return;

    final cx = size.width * 0.5;
    final cy = size.height * 0.49;
    final c = Offset(cx, cy);
    final live = burst.clamp(0.0, 1.0);
    final sc = (0.22 + 0.78 * burst) * (1.0 + 0.016 * sin(pulse * pi * 2) * live);
    final rScale = min(size.width, size.height) / 310.0;

    canvas.save();
    canvas.translate(cx, cy);
    canvas.rotate((1.0 - live) * -0.22);
    canvas.scale(sc, sc);
    canvas.translate(-cx, -cy);

    final redPaint = Paint()..color = P5Colors.red.withValues(alpha: opacity);
    final blackPaint = Paint()..color = P5Colors.black.withValues(alpha: opacity);

    // 1) Compact jagged crimson teeth around Morgana's core
    for (final side in [-1.0, 1.0]) {
      if ((side < 0 && !hasLeft) || (side > 0 && !hasRight)) continue;
      final teeth = Path()
        ..moveTo(cx + side * 12 * rScale, cy - 55 * rScale)
        ..lineTo(cx + side * 102 * rScale, cy - 78 * rScale)
        ..lineTo(cx + side * 66 * rScale, cy - 28 * rScale)
        ..lineTo(cx + side * 116 * rScale, cy - 4 * rScale)
        ..lineTo(cx + side * 64 * rScale, cy + 28 * rScale)
        ..lineTo(cx + side * 98 * rScale, cy + 74 * rScale)
        ..lineTo(cx + side * 12 * rScale, cy + 52 * rScale)
        ..close();
      canvas.drawPath(teeth, redPaint);
    }

    // 2) Signature stepped Joker upper-right hero crimson lightning-slash
    final heroAng = -0.56;
    final hLen = min(size.width * 0.56, 215.0 * rScale);
    final hW = 28.0 * rScale;
    final heroPts = [
      Offset(-20 * rScale, -0.58 * hW),
      Offset(hLen * 0.24, -0.92 * hW),
      Offset(hLen * 0.68, -0.34 * hW),
      Offset(hLen, 0),
      Offset(hLen * 0.70, 0.24 * hW),
      Offset(hLen * 0.65, 0.54 * hW),
      Offset(hLen * 0.22, 0.76 * hW),
      Offset(-20 * rScale, 0.48 * hW),
    ];
    canvas.drawPath(_orientedPoly(c, heroAng, heroPts), redPaint);

    // 3) Opposite continuous razor crimson tail slicing lower-left
    final tLen = min(size.width * 0.52, 195.0 * rScale);
    final tW = 13.0 * rScale;
    final tailPts = [
      Offset(-18 * rScale, -0.44 * tW),
      Offset(tLen * 0.16, -0.58 * tW),
      Offset(tLen * 0.42, -0.24 * tW),
      Offset(tLen, 0),
      Offset(tLen * 0.40, 0.26 * tW),
      Offset(tLen * 0.14, 0.62 * tW),
      Offset(-18 * rScale, 0.46 * tW),
    ];
    canvas.drawPath(_orientedPoly(c, heroAng + pi, tailPts), redPaint);

    // 4) Upper-left crimson counter-fin
    final ulLen = 132.0 * rScale;
    final ulW = 16.0 * rScale;
    canvas.drawPath(
      _orientedPoly(c, pi + 0.52, [
        Offset(-14 * rScale, -0.48 * ulW),
        Offset(ulLen * 0.20, -0.76 * ulW),
        Offset(ulLen, 0),
        Offset(ulLen * 0.18, 0.64 * ulW),
        Offset(-14 * rScale, 0.42 * ulW),
      ]),
      redPaint,
    );

    // 5) Piercing obsidian-black counter-spears
    final bkLen = tLen * 0.86;
    final bkW = 14.0 * rScale;
    canvas.drawPath(
      _orientedPoly(c, heroAng + pi + 0.06, [
        Offset(-14 * rScale, -4 * rScale - 0.48 * bkW),
        Offset(bkLen * 0.20, -4 * rScale - 0.76 * bkW),
        Offset(bkLen, -4 * rScale),
        Offset(bkLen * 0.18, -4 * rScale + 0.64 * bkW),
        Offset(-14 * rScale, -4 * rScale + 0.42 * bkW),
      ]),
      blackPaint,
    );
    final brLen = 128.0 * rScale;
    final brW = 10.0 * rScale;
    canvas.drawPath(
      _orientedPoly(c, 0.48, [
        Offset(-12 * rScale, -0.46 * brW),
        Offset(brLen * 0.22, -0.72 * brW),
        Offset(brLen, 0),
        Offset(brLen * 0.20, 0.62 * brW),
        Offset(-12 * rScale, 0.42 * brW),
      ]),
      blackPaint,
    );

    canvas.restore();
  }

  @override
  bool shouldRepaint(JokerSpikesPainter old) =>
      old.burst != burst || old.pulse != pulse || old.hasLeft != hasLeft || old.hasRight != hasRight;
}
