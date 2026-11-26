import 'dart:async';

// ignore: deprecated_member_use
import 'package:flutter/material.dart';

import '../core/app_state.dart';
import '../theme/p5_theme.dart';
import '../widgets/p5_widgets.dart';

class _ChatBubbleItem {
  final bool isUser;
  final String text;
  final String badge;
  final String actionLabel;
  final String actionTab;
  final bool accentRed;

  const _ChatBubbleItem({
    required this.isUser,
    required this.text,
    this.badge = '',
    this.actionLabel = '',
    this.actionTab = '',
    this.accentRed = false,
  });
}

/// Spotlight Quick Command + Gemini/OpenAI Morgana companion chat screen.
class AskScreen extends StatefulWidget {
  final MorganaAppState app;

  const AskScreen({super.key, required this.app});

  @override
  State<AskScreen> createState() => _AskScreenState();
}

class _AskScreenState extends State<AskScreen> {
  final _inputCtrl = TextEditingController();
  final _focusNode = FocusNode();
  final _scrollCtrl = ScrollController();

  final List<_ChatBubbleItem> _messages = [];
  bool _sending = false;
  bool _nudged = false;
  Timer? _nudgeTimer;

  @override
  void dispose() {
    _nudgeTimer?.cancel();
    _inputCtrl.dispose();
    _focusNode.dispose();
    _scrollCtrl.dispose();
    super.dispose();
  }

  Future<void> _submit([String? preset]) async {
    final text = (preset ?? _inputCtrl.text).trim();
    if (text.isEmpty) {
      widget.app.audio.playSfx('cancel');
      widget.app.notify('Type a command or ask Morgana something!', tone: P5ToastTone.warn);
      setState(() => _nudged = true);
      _nudgeTimer?.cancel();
      _nudgeTimer = Timer(const Duration(milliseconds: 450), () {
        if (mounted) setState(() => _nudged = false);
      });
      return;
    }
    if (_sending) return;
    if (preset == null) _inputCtrl.clear();

    setState(() {
      _messages.add(_ChatBubbleItem(isUser: true, text: text));
      _sending = true;
    });
    _scrollToBottom();

    final res = await widget.app.runQuickCommand(text);
    if (!mounted) return;
    setState(() {
      _sending = false;
      _messages.add(_ChatBubbleItem(
        isUser: false,
        text: res.reply,
        badge: res.badge,
        actionLabel: res.actionLabel,
        actionTab: res.actionTab,
        accentRed: res.accentRed,
      ));
    });
    _scrollToBottom();
  }

  void _prefill(String prefix) {
    _inputCtrl.text = prefix;
    _inputCtrl.selection = TextSelection.fromPosition(TextPosition(offset: prefix.length));
    _focusNode.requestFocus();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollCtrl.hasClients) {
        _scrollCtrl.animateTo(
          _scrollCtrl.position.maxScrollExtent + 120,
          duration: const Duration(milliseconds: 220),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final app = widget.app;
    final aiOn = app.settings['ai_enabled'] == true;

    return Column(
      children: [
        P5Header(
          title: 'Ask Morgana',
          onAvatarTap: () => app.navigateTo('home'),
          actions: [
            if (_messages.isNotEmpty || app.aiClient.hasHistory)
              P5Button(
                label: 'NEW CHAT',
                variant: P5BtnVariant.soft,
                height: 30,
                fontSize: 11,
                onTap: () {
                  app.aiClient.clearHistory();
                  setState(() => _messages.clear());
                  app.notify('Started fresh conversation', tone: P5ToastTone.info, ms: 1600);
                },
              ),
          ],
        ),
        Expanded(
          child: Column(
            children: [
              // Quick command chips strip
              Container(
                width: double.infinity,
                padding: const EdgeInsets.fromLTRB(12, 9, 12, 8),
                decoration: const BoxDecoration(
                  color: P5Colors.panel,
                  border: Border(bottom: BorderSide(color: P5Colors.border)),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                          decoration: BoxDecoration(
                            color: aiOn ? P5Colors.mintBg : P5Colors.goldBg,
                            borderRadius: BorderRadius.circular(4),
                            border: Border.all(color: aiOn ? P5Colors.mintBorder : P5Colors.goldBorder),
                          ),
                          child: Text(
                            aiOn ? 'AI ONLINE' : 'OFFLINE COMMANDS',
                            style: P5Text.display(10, color: aiOn ? P5Colors.mint : P5Colors.gold),
                          ),
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            aiOn
                                ? 'Chat freely or trigger instant commands below.'
                                : 'Instant offline commands ready · Enable Gemini AI in Settings.',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: P5Text.body(11, color: P5Colors.muted),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 7),
                    SingleChildScrollView(
                      scrollDirection: Axis.horizontal,
                      child: Row(
                        children: [
                          _chip('+ TASK', () => _prefill('task ')),
                          _chip('+ NOTE', () => _prefill('note ')),
                          _chip('+ EVENT', () => _prefill('event ')),
                          _chip('FOCUS 25M', () => _submit('focus 25')),
                          _chip('5M BREAK', () => _submit('break 5')),
                          _chip('SALDO', () => _submit('saldo')),
                          _chip('RECAP', () => _submit('status')),
                          _chip('DND', () => _submit('dnd')),
                        ],
                      ),
                    ),
                  ],
                ),
              ),

              // Conversation feed
              Expanded(
                child: _messages.isEmpty
                    ? ListView(
                        padding: const EdgeInsets.fromLTRB(12, 16, 12, 16),
                        children: [
                          P5Card(
                            topColor: const Color(0xFF280D15),
                            borderColor: P5Colors.red,
                            child: Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Container(
                                  width: 48,
                                  height: 48,
                                  decoration: BoxDecoration(
                                    shape: BoxShape.circle,
                                    border: Border.all(color: P5Colors.white, width: 1.6),
                                    color: P5Colors.black,
                                  ),
                                  clipBehavior: Clip.antiAlias,
                                  child: Image.asset(
                                    'assets/Morgana/b003_000_00.png',
                                    fit: BoxFit.cover,
                                    alignment: const Alignment(0, -0.55),
                                    errorBuilder: (_, _, _) => const Icon(Icons.pets, color: P5Colors.gold),
                                  ),
                                ),
                                const SizedBox(width: 11),
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text('MORGANA', style: P5Text.display(13, color: P5Colors.gold)),
                                      const SizedBox(height: 4),
                                      Text(
                                        app.morganaSpeech.isEmpty
                                            ? "Looking cool! Give me a command like 'task review slides', 'saldo', or 'focus 25'!"
                                            : app.morganaSpeech,
                                        style: P5Text.body(13.5, color: P5Colors.white, bold: true),
                                      ),
                                    ],
                                  ),
                                ),
                              ],
                            ),
                          ),
                          const SizedBox(height: 10),
                          P5Card(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text('TRY SAYING', style: P5Text.display(11.5, color: P5Colors.gold)),
                                const SizedBox(height: 6),
                                Text(
                                  '• task finish Android APK\n'
                                  '• note coffee shop Wi-Fi #ideas\n'
                                  '• event team standup\n'
                                  '• jajan 15rb kopi  (quick expense)\n'
                                  '• saldo / status / focus 25 / break 5',
                                  style: P5Text.body(12.5, color: P5Colors.muted),
                                ),
                              ],
                            ),
                          ),
                        ],
                      )
                    : ListView.builder(
                        controller: _scrollCtrl,
                        padding: const EdgeInsets.fromLTRB(12, 10, 12, 12),
                        itemCount: _messages.length + (_sending ? 1 : 0),
                        itemBuilder: (context, idx) {
                          if (_sending && idx == _messages.length) {
                            return Align(
                              alignment: Alignment.centerLeft,
                              child: Container(
                                margin: const EdgeInsets.only(bottom: 8),
                                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                                decoration: BoxDecoration(
                                  color: P5Colors.card,
                                  borderRadius: BorderRadius.circular(10),
                                  border: Border.all(color: P5Colors.goldBorder),
                                ),
                                child: Text('Morgana is thinking...', style: P5Text.body(12.5, color: P5Colors.gold)),
                              ),
                            );
                          }
                          final m = _messages[idx];
                          return _buildMessageBubble(m);
                        },
                      ),
              ),

              // Input composer pinned above bottom nav
              Container(
                padding: const EdgeInsets.fromLTRB(12, 8, 12, 74),
                decoration: const BoxDecoration(
                  color: P5Colors.panel,
                  border: Border(top: BorderSide(color: P5Colors.border)),
                ),
                child: Row(
                  children: [
                    Expanded(
                      child: P5NudgeBox(
                        nudged: _nudged,
                        child: TextField(
                          controller: _inputCtrl,
                          focusNode: _focusNode,
                          decoration: const InputDecoration(
                            hintText: 'Command or chat with Morgana...',
                          ),
                          textInputAction: TextInputAction.send,
                          onSubmitted: (_) => _submit(),
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    P5Button(
                      label: _sending ? '...' : 'SEND',
                      variant: P5BtnVariant.primary,
                      height: 44,
                      onTap: _sending ? null : () => _submit(),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _chip(String label, VoidCallback onTap) {
    return Padding(
      padding: const EdgeInsets.only(right: 6),
      child: P5Button(
        label: label,
        variant: P5BtnVariant.soft,
        height: 28,
        fontSize: 11,
        onTap: onTap,
      ),
    );
  }

  Widget _buildMessageBubble(_ChatBubbleItem m) {
    if (m.isUser) {
      return Align(
        alignment: Alignment.centerRight,
        child: Container(
          constraints: const BoxConstraints(maxWidth: 300),
          margin: const EdgeInsets.only(bottom: 8),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
          decoration: BoxDecoration(
            color: P5Colors.red,
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: P5Colors.white, width: 1.1),
          ),
          child: Text(m.text, style: P5Text.body(13.5, color: P5Colors.white, bold: true)),
        ),
      );
    }

    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 330),
        margin: const EdgeInsets.only(bottom: 9),
        padding: const EdgeInsets.all(11),
        decoration: BoxDecoration(
          color: P5Colors.card,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: m.accentRed ? P5Colors.red : P5Colors.border, width: 1.3),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (m.badge.isNotEmpty)
              Container(
                margin: const EdgeInsets.only(bottom: 5),
                padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                decoration: BoxDecoration(
                  color: m.accentRed ? P5Colors.redBg : P5Colors.goldBg,
                  borderRadius: BorderRadius.circular(4),
                  border: Border.all(color: m.accentRed ? P5Colors.red : P5Colors.goldBorder),
                ),
                child: Text(m.badge, style: P5Text.display(10, color: m.accentRed ? P5Colors.redFg : P5Colors.gold)),
              ),
            Text(m.text, style: P5Text.body(13.5, color: P5Colors.white)),
            if (m.actionTab.isNotEmpty && m.actionLabel.isNotEmpty) ...[
              const SizedBox(height: 8),
              P5Button(
                label: m.actionLabel,
                variant: P5BtnVariant.gold,
                height: 28,
                fontSize: 11,
                onTap: () => widget.app.navigateTo(m.actionTab),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
