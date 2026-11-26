import 'dart:async';
import 'dart:math';

// ignore: deprecated_member_use
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/app_state.dart';
import '../theme/p5_theme.dart';

enum P5BtnVariant { primary, gold, mint, danger, soft, ghost }

/// Persona 5 Royal tactile button with press spring, diagonal slash-flash burst, and haptics.
class P5Button extends StatefulWidget {
  final String label;
  final IconData? icon;
  final P5BtnVariant variant;
  final bool active;
  final P5BtnVariant activeVariant;
  final VoidCallback? onTap;
  final double height;
  final double fontSize;
  final bool displayFont;
  final EdgeInsetsGeometry? padding;
  final bool expand;

  const P5Button({
    super.key,
    required this.label,
    this.icon,
    this.variant = P5BtnVariant.soft,
    this.active = false,
    this.activeVariant = P5BtnVariant.primary,
    this.onTap,
    this.height = 38,
    this.fontSize = 13,
    this.displayFont = true,
    this.padding,
    this.expand = false,
  });

  @override
  State<P5Button> createState() => _P5ButtonState();
}

class _P5ButtonState extends State<P5Button> with SingleTickerProviderStateMixin {
  late final AnimationController _flashCtrl;
  bool _pressed = false;

  @override
  void initState() {
    super.initState();
    _flashCtrl = AnimationController(vsync: this, duration: const Duration(milliseconds: 260));
  }

  @override
  void dispose() {
    _flashCtrl.dispose();
    super.dispose();
  }

  (Color bg, Color bd, Color fg) _palette(P5BtnVariant v) {
    switch (v) {
      case P5BtnVariant.primary:
        return (P5Colors.red, P5Colors.white, P5Colors.white);
      case P5BtnVariant.gold:
        return (P5Colors.goldBg, P5Colors.goldBorder, P5Colors.goldFg);
      case P5BtnVariant.mint:
        return (P5Colors.mintBg, P5Colors.mintBorder, P5Colors.mint);
      case P5BtnVariant.danger:
        return (P5Colors.redBg, const Color(0xFF731823), P5Colors.redFg);
      case P5BtnVariant.ghost:
        return (Colors.transparent, Colors.transparent, P5Colors.text);
      case P5BtnVariant.soft:
        return (P5Colors.surface, P5Colors.border, P5Colors.text);
    }
  }

  void _triggerTap() {
    if (widget.onTap == null) return;
    HapticFeedback.selectionClick();
    _flashCtrl.forward(from: 0.0);
    widget.onTap!();
  }

  @override
  Widget build(BuildContext context) {
    final enabled = widget.onTap != null;
    final effective = widget.active ? widget.activeVariant : widget.variant;
    final (bg, bd, fg) = _palette(effective);
    final isAccent = effective == P5BtnVariant.primary ||
        effective == P5BtnVariant.gold ||
        effective == P5BtnVariant.mint ||
        effective == P5BtnVariant.danger;

    final child = GestureDetector(
      onTapDown: enabled ? (_) => setState(() => _pressed = true) : null,
      onTapUp: enabled ? (_) => setState(() => _pressed = false) : null,
      onTapCancel: enabled ? () => setState(() => _pressed = false) : null,
      onTap: enabled ? _triggerTap : null,
      child: AnimatedScale(
        scale: _pressed ? 0.955 : 1.0,
        duration: const Duration(milliseconds: 80),
        curve: Curves.easeOut,
        child: Opacity(
          opacity: enabled ? 1.0 : 0.38,
          child: AnimatedBuilder(
            animation: _flashCtrl,
            builder: (context, inner) {
              return CustomPaint(
                foregroundPainter: _flashCtrl.isAnimating
                    ? _P5SlashFlashPainter(progress: _flashCtrl.value)
                    : null,
                child: Container(
                  height: widget.height,
                  padding: widget.padding ?? EdgeInsets.symmetric(horizontal: widget.label.isEmpty ? 8 : 11),
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(7),
                    gradient: bg.a > 0
                        ? LinearGradient(
                            begin: Alignment.topCenter,
                            end: Alignment.bottomCenter,
                            colors: [
                              Color.lerp(bg, P5Colors.white, isAccent ? 0.09 : 0.05)!,
                              Color.lerp(bg, P5Colors.black, 0.12)!,
                            ],
                          )
                        : null,
                    border: bd.a > 0 ? Border.all(color: bd, width: widget.active ? 1.5 : 1.15) : null,
                  ),
                  child: inner,
                ),
              );
            },
            child: Row(
              mainAxisSize: widget.expand ? MainAxisSize.max : MainAxisSize.min,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                if (widget.icon != null) ...[
                  Icon(widget.icon, size: widget.fontSize + 2, color: fg),
                  if (widget.label.isNotEmpty) const SizedBox(width: 5),
                ],
                if (widget.label.isNotEmpty)
                  Flexible(
                    child: Text(
                      widget.label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: widget.displayFont
                          ? P5Text.display(widget.fontSize + 1, color: fg)
                          : P5Text.body(widget.fontSize, color: fg, bold: true),
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
    return widget.expand ? SizedBox(width: double.infinity, child: child) : child;
  }
}

class _P5SlashFlashPainter extends CustomPainter {
  final double progress;
  _P5SlashFlashPainter({required this.progress});

  @override
  void paint(Canvas canvas, Size size) {
    final fl = (1.0 - progress).clamp(0.0, 1.0);
    if (fl <= 0.02) return;
    final rrect = RRect.fromRectAndRadius(Offset.zero & size, const Radius.circular(7));
    canvas.save();
    canvas.clipRRect(rrect);
    final fx = -20.0 + progress * (size.width + 52.0);
    final fw = max(18.0, size.width * 0.25);
    final path = Path()
      ..moveTo(fx, 0)
      ..lineTo(fx + fw, 0)
      ..lineTo(fx + fw - 12, size.height)
      ..lineTo(fx - 12, size.height)
      ..close();
    canvas.drawPath(path, Paint()..color = Colors.white.withValues(alpha: 0.55 * fl));
    canvas.drawRect(Offset.zero & size, Paint()..color = Colors.white.withValues(alpha: 0.16 * fl));
    canvas.restore();
  }

  @override
  bool shouldRepaint(_P5SlashFlashPainter old) => old.progress != progress;
}

/// Sculpted Persona 5 dark obsidian card matching desktop `QFrame#card`.
class P5Card extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry padding;
  final Color? borderColor;
  final double borderWidth;
  final Color? topColor;

  const P5Card({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(12),
    this.borderColor,
    this.borderWidth = 1.1,
    this.topColor,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: padding,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(10),
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [topColor ?? P5Colors.cardTop, P5Colors.card],
        ),
        border: Border.all(color: borderColor ?? P5Colors.border, width: borderWidth),
      ),
      child: child,
    );
  }
}

/// Authentic Persona 5 slanted crimson header bar with trailing razor white slash.
class P5Header extends StatelessWidget {
  final String title;
  final List<Widget> actions;
  final VoidCallback? onAvatarTap;

  const P5Header({
    super.key,
    required this.title,
    this.actions = const [],
    this.onAvatarTap,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 54,
      decoration: const BoxDecoration(
        color: Color(0xFF08080D),
        border: Border(bottom: BorderSide(color: P5Colors.red, width: 2.0)),
      ),
      child: CustomPaint(
        painter: _P5HeaderSlashPainter(titleLength: title.length),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 10),
          child: Row(
            children: [
              GestureDetector(
                onTap: onAvatarTap,
                child: Container(
                  width: 32,
                  height: 32,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    border: Border.all(color: P5Colors.white, width: 1.5),
                    color: P5Colors.black,
                  ),
                  clipBehavior: Clip.antiAlias,
                  child: Image.asset(
                    'assets/Morgana/b003_000_00.png',
                    fit: BoxFit.cover,
                    alignment: const Alignment(0, -0.55),
                    errorBuilder: (_, _, _) => const Icon(Icons.pets, size: 18, color: P5Colors.gold),
                  ),
                ),
              ),
              const SizedBox(width: 9),
              Expanded(
                child: Text(
                  title.toUpperCase(),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: P5Text.heading(22),
                ),
              ),
              const SizedBox(width: 6),
              ...actions,
            ],
          ),
        ),
      ),
    );
  }
}

class _P5HeaderSlashPainter extends CustomPainter {
  final int titleLength;
  _P5HeaderSlashPainter({required this.titleLength});

  @override
  void paint(Canvas canvas, Size size) {
    final slant = 20.0;
    final cut = (titleLength * 13.5 + 88.0).clamp(155.0, size.width * 0.56);
    final redPath = Path()
      ..moveTo(0, 0)
      ..lineTo(cut, 0)
      ..lineTo(cut - slant, size.height)
      ..lineTo(0, size.height)
      ..close();
    canvas.drawPath(
      redPath,
      Paint()
        ..shader = const LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [Color(0xFFF50C1F), Color(0xFFC40010)],
        ).createShader(Rect.fromLTWH(0, 0, cut, size.height)),
    );
    // Signature Persona 5 Royal white trailing slash
    final whitePath = Path()
      ..moveTo(cut + 4, 0)
      ..lineTo(cut + 9, 0)
      ..lineTo(cut + 9 - slant, size.height)
      ..lineTo(cut + 4 - slant, size.height)
      ..close();
    canvas.drawPath(whitePath, Paint()..color = P5Colors.white);

    // Subtle diagonal racing texture on right side
    final stripePaint = Paint()..color = Colors.white.withValues(alpha: 0.04);
    var sx = size.width - 18.0;
    for (var i = 0; i < 4; i++) {
      final sp = Path()
        ..moveTo(sx, 0)
        ..lineTo(sx + 9, 0)
        ..lineTo(sx - 5, size.height)
        ..lineTo(sx - 14, size.height)
        ..close();
      canvas.drawPath(sp, stripePaint);
      sx -= 20;
    }
  }

  @override
  bool shouldRepaint(_P5HeaderSlashPainter old) => old.titleLength != titleLength;
}

/// Interactive clickable Persona 5 KPI summary card with update pulse & active glow.
class P5KpiCard extends StatefulWidget {
  final String title;
  final String value;
  final String subtitle;
  final Color accent;
  final Color tint;
  final bool active;
  final String actionHint;
  final VoidCallback? onTap;

  const P5KpiCard({
    super.key,
    required this.title,
    required this.value,
    required this.subtitle,
    required this.accent,
    required this.tint,
    this.active = false,
    this.actionHint = '',
    this.onTap,
  });

  @override
  State<P5KpiCard> createState() => _P5KpiCardState();
}

class _P5KpiCardState extends State<P5KpiCard> with SingleTickerProviderStateMixin {
  late final AnimationController _pulseCtrl;

  @override
  void initState() {
    super.initState();
    _pulseCtrl = AnimationController(vsync: this, duration: const Duration(milliseconds: 340));
  }

  @override
  void didUpdateWidget(P5KpiCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.value != widget.value) {
      _pulseCtrl.forward(from: 0.0);
    }
  }

  @override
  void dispose() {
    _pulseCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: widget.onTap == null
          ? null
          : () {
              HapticFeedback.selectionClick();
              _pulseCtrl.forward(from: 0.0);
              widget.onTap!();
            },
      child: AnimatedBuilder(
        animation: _pulseCtrl,
        builder: (context, child) {
          return CustomPaint(
            foregroundPainter: _pulseCtrl.isAnimating
                ? _P5SlashFlashPainter(progress: _pulseCtrl.value)
                : null,
            child: Container(
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(9),
                gradient: LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: [
                    widget.active ? Color.lerp(widget.tint, widget.accent, 0.16)! : widget.tint,
                    const Color(0xFF14141E),
                  ],
                ),
                border: Border.all(
                  color: widget.active ? widget.accent : P5Colors.border,
                  width: widget.active ? 1.6 : 1.0,
                ),
              ),
              clipBehavior: Clip.antiAlias,
              child: child,
            ),
          );
        },
        child: IntrinsicHeight(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Container(width: widget.active ? 5.0 : 3.5, color: widget.accent),
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(9, 7, 8, 7),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              widget.title,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: P5Text.display(10.5, color: const Color(0xFFA6ABC0)),
                            ),
                          ),
                          if (widget.actionHint.isNotEmpty)
                            Text(widget.actionHint, style: P5Text.display(9.5, color: widget.accent)),
                        ],
                      ),
                      const SizedBox(height: 2),
                      Text(
                        widget.value,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: P5Text.display(18, color: widget.accent),
                      ),
                      const SizedBox(height: 1),
                      Text(
                        widget.subtitle,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: P5Text.body(10.5, color: P5Colors.muted),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Floating slanted Persona 5 confirmation/warning toast pinned cleanly above bottom nav.
class P5ToastOverlay extends StatefulWidget {
  final P5ToastMessage? message;
  final void Function(int id) onDismiss;

  const P5ToastOverlay({super.key, required this.message, required this.onDismiss});

  @override
  State<P5ToastOverlay> createState() => _P5ToastOverlayState();
}

class _P5ToastOverlayState extends State<P5ToastOverlay> {
  Timer? _timer;

  @override
  void didUpdateWidget(P5ToastOverlay oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.message?.id != oldWidget.message?.id) {
      _timer?.cancel();
      final msg = widget.message;
      if (msg != null) {
        _timer = Timer(Duration(milliseconds: max(900, msg.durationMs)), () {
          widget.onDismiss(msg.id);
        });
      }
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  (Color accent, Color glyphFg, String glyph) _style(P5ToastTone tone) {
    switch (tone) {
      case P5ToastTone.ok:
        return (P5Colors.mint, P5Colors.black, '✓');
      case P5ToastTone.gold:
        return (P5Colors.gold, P5Colors.black, '★');
      case P5ToastTone.warn:
        return (P5Colors.gold, P5Colors.black, '!');
      case P5ToastTone.err:
        return (P5Colors.red, P5Colors.white, '×');
      case P5ToastTone.info:
        return (P5Colors.red, P5Colors.white, '★');
    }
  }

  @override
  Widget build(BuildContext context) {
    final msg = widget.message;
    return IgnorePointer(
      child: AnimatedSlide(
        offset: msg != null ? Offset.zero : const Offset(0, 0.6),
        duration: const Duration(milliseconds: 220),
        curve: Curves.easeOutBack,
        child: AnimatedOpacity(
          opacity: msg != null ? 1.0 : 0.0,
          duration: const Duration(milliseconds: 180),
          child: msg == null
              ? const SizedBox.shrink()
              : Builder(builder: (context) {
                  final (accent, fg, glyph) = _style(msg.tone);
                  return Center(
                    child: Container(
                      constraints: const BoxConstraints(maxWidth: 360),
                      margin: const EdgeInsets.symmetric(horizontal: 18),
                      decoration: BoxDecoration(
                        boxShadow: [
                          BoxShadow(color: accent.withValues(alpha: 0.75), offset: const Offset(3, 3)),
                          BoxShadow(color: Colors.black.withValues(alpha: 0.65), blurRadius: 10, offset: const Offset(4, 6)),
                        ],
                      ),
                      child: ClipPath(
                        clipper: _SlantClipper(skew: 9),
                        child: Container(
                          color: const Color(0xFF0B0B11),
                          foregroundDecoration: BoxDecoration(border: Border.all(color: P5Colors.white, width: 1.6)),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Container(
                                width: 34,
                                padding: const EdgeInsets.symmetric(vertical: 9),
                                color: accent,
                                alignment: Alignment.center,
                                child: Text(glyph, style: P5Text.display(15, color: fg)),
                              ),
                              Flexible(
                                child: Padding(
                                  padding: const EdgeInsets.fromLTRB(11, 8, 16, 8),
                                  child: Text(
                                    msg.text,
                                    maxLines: 2,
                                    overflow: TextOverflow.ellipsis,
                                    style: P5Text.body(13, color: P5Colors.white, bold: true),
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                  );
                }),
        ),
      ),
    );
  }
}

class _SlantClipper extends CustomClipper<Path> {
  final double skew;
  _SlantClipper({this.skew = 9});

  @override
  Path getClip(Size size) => Path()
    ..moveTo(skew, 0)
    ..lineTo(size.width, 0)
    ..lineTo(size.width - skew, size.height)
    ..lineTo(0, size.height)
    ..close();

  @override
  bool shouldReclip(_SlantClipper old) => old.skew != skew;
}

/// 5-second Persona 5 crimson Undo bar.
class P5UndoBar extends StatelessWidget {
  final String message;
  final VoidCallback onUndo;

  const P5UndoBar({super.key, required this.message, required this.onUndo});

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(8),
        gradient: const LinearGradient(colors: [Color(0xFF2E0910), Color(0xFF1E070C)]),
        border: Border.all(color: P5Colors.red, width: 1.4),
      ),
      child: Row(
        children: [
          const Icon(Icons.history, size: 16, color: P5Colors.gold),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              message,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: P5Text.body(12.5, color: P5Colors.white, bold: true),
            ),
          ),
          const SizedBox(width: 8),
          P5Button(label: 'UNDO', variant: P5BtnVariant.gold, height: 28, fontSize: 11, onTap: onUndo),
        ],
      ),
    );
  }
}

/// Gold-flashing validation highlight wrapper for text inputs (matches desktop `nudge_input`).
class P5NudgeBox extends StatelessWidget {
  final bool nudged;
  final Widget child;

  const P5NudgeBox({super.key, required this.nudged, required this.child});

  @override
  Widget build(BuildContext context) {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 140),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(9),
        border: Border.all(
          color: nudged ? P5Colors.gold : Colors.transparent,
          width: nudged ? 2.0 : 0.0,
        ),
        boxShadow: nudged
            ? [BoxShadow(color: P5Colors.gold.withValues(alpha: 0.35), blurRadius: 8)]
            : null,
      ),
      child: child,
    );
  }
}
