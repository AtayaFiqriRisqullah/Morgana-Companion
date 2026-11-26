import 'dart:async';

// ignore: deprecated_member_use
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/app_state.dart';
import '../core/storage.dart';
import '../theme/p5_theme.dart';
import '../widgets/p5_widgets.dart';

class NotesScreen extends StatefulWidget {
  final MorganaAppState app;

  const NotesScreen({super.key, required this.app});

  @override
  State<NotesScreen> createState() => _NotesScreenState();
}

class _NotesScreenState extends State<NotesScreen> {
  final TextEditingController _searchCtrl = TextEditingController();
  final TextEditingController _titleCtrl = TextEditingController();
  final TextEditingController _bodyCtrl = TextEditingController();

  String _filter = 'all'; // 'all' | 'pinned' | 'archived'
  String _activeTag = '';
  String? _editingNoteId;
  bool _editorOpen = false;
  bool _previewMode = false;
  String _category = 'Personal';
  bool _pinned = false;
  bool _archived = false;
  bool _titleNudged = false;

  Map<String, dynamic>? _undoNote;
  String _undoMsg = '';
  Timer? _undoTimer;
  Timer? _nudgeTimer;

  static const Map<String, (String title, String body, String cat)> _templates = {
    'blank': ('', '', 'Personal'),
    'meeting': (
      'Meeting Notes',
      '## Agenda\n- Main topic\n\n## Key Points\n- Takeaway\n\n## Action Items\n- [ ] Follow up\n\n#meeting',
      'Work',
    ),
    'study': (
      'Study Summary',
      '## Core Concept\nSummarize main idea.\n\n## Key Takeaways\n- Insight 1\n- Insight 2\n\n#study',
      'Study',
    ),
    'daily': (
      'Daily Log',
      '## Top 3 Priorities\n- [ ] Priority 1\n- [ ] Priority 2\n\n## Reflection\n...\n\n#daily',
      'Journal',
    ),
  };

  @override
  void dispose() {
    _undoTimer?.cancel();
    _nudgeTimer?.cancel();
    _searchCtrl.dispose();
    _titleCtrl.dispose();
    _bodyCtrl.dispose();
    super.dispose();
  }

  void _openNew([String tmpl = 'blank']) {
    final t = _templates[tmpl] ?? _templates['blank']!;
    setState(() {
      _editingNoteId = null;
      _titleCtrl.text = t.$1;
      _bodyCtrl.text = t.$2;
      _category = t.$3;
      _pinned = false;
      _archived = false;
      _previewMode = false;
      _editorOpen = true;
    });
  }

  void _openExisting(Map<String, dynamic> n) {
    setState(() {
      _editingNoteId = n['id'].toString();
      _titleCtrl.text = (n['title'] ?? '').toString();
      _bodyCtrl.text = (n['content'] ?? '').toString();
      _category = kNoteCategories.contains(n['category']) ? n['category'].toString() : 'Personal';
      _pinned = n['pinned'] == true;
      _archived = n['archived'] == true;
      _previewMode = false;
      _editorOpen = true;
    });
  }

  Future<void> _saveNote() async {
    final t = _titleCtrl.text.trim();
    final c = _bodyCtrl.text.trim();
    if (t.isEmpty && c.isEmpty) {
      widget.app.audio.playSfx('cancel');
      widget.app.notify('Add a title or content first!', tone: P5ToastTone.warn);
      setState(() => _titleNudged = true);
      _nudgeTimer?.cancel();
      _nudgeTimer = Timer(const Duration(milliseconds: 450), () {
        if (mounted) setState(() => _titleNudged = false);
      });
      return;
    }
    final title = t.isNotEmpty ? t : c.split('\n').first.replaceFirst(RegExp(r'^[#>\-*• ]+'), '').trim();
    if (_editingNoteId != null) {
      await widget.app.notes.update(_editingNoteId!, title, c, category: _category);
    } else {
      final created = await widget.app.notes.add(title, c, category: _category);
      _editingNoteId = created['id'].toString();
    }
    widget.app.audio.playSfx('select');
    widget.app.notify("Saved '$title'", tone: P5ToastTone.ok);
    setState(() {});
  }

  Future<void> _promoteToTask() async {
    final bodyLines = _bodyCtrl.text.trim().split('\n');
    final title = _titleCtrl.text.trim().isNotEmpty
        ? _titleCtrl.text.trim()
        : (bodyLines.isNotEmpty ? bodyLines.first.trim() : '');
    if (title.isEmpty) {
      widget.app.notify('Write a note first to send to Tasks', tone: P5ToastTone.warn);
      return;
    }
    await widget.app.tasks.add(title);
    widget.app.notify("Sent to Tasks: '$title'", tone: P5ToastTone.ok);
  }

  Future<void> _copyMarkdown() async {
    final t = _titleCtrl.text.trim();
    final b = _bodyCtrl.text.trim();
    final payload = t.isNotEmpty ? '# $t\n\n$b'.trim() : b;
    if (payload.isEmpty) {
      widget.app.notify('Nothing to copy yet', tone: P5ToastTone.warn);
      return;
    }
    await Clipboard.setData(ClipboardData(text: payload));
    widget.app.notify('Copied Markdown to clipboard!', tone: P5ToastTone.ok);
  }

  @override
  Widget build(BuildContext context) {
    final app = widget.app;
    if (_editorOpen) return _buildEditorView(app);

    final q = _searchCtrl.text.trim().toLowerCase();
    final tags = app.notes.allTags();
    final notes = app.notes.all().where((n) {
      final arch = n['archived'] == true;
      if (_filter == 'archived') {
        if (!arch) return false;
      } else {
        if (arch) return false;
        if (_filter == 'pinned' && n['pinned'] != true) return false;
      }
      final nt = NoteStore.noteTags(n);
      if (_activeTag.isNotEmpty && !nt.contains(_activeTag)) return false;
      if (q.isNotEmpty) {
        final blob = "${n['title']} ${n['content']} ${n['category']} ${nt.map((e) => '#$e').join(' ')}".toLowerCase();
        if (!blob.contains(q)) return false;
      }
      return true;
    }).toList()
      ..sort((a, b) {
        final pa = a['pinned'] == true ? 0 : 1;
        final pb = b['pinned'] == true ? 0 : 1;
        if (pa != pb) return pa.compareTo(pb);
        return (b['updated'] ?? '').toString().compareTo((a['updated'] ?? '').toString());
      });

    return Column(
      children: [
        P5Header(
          title: 'NOTES',
          onAvatarTap: () => app.navigateTo('home'),
          actions: [
            if (app.isDataSyncActive) ...[
              P5Button(
                label: '',
                icon: Icons.sync,
                variant: app.companionSyncBusy ? P5BtnVariant.gold : P5BtnVariant.mint,
                height: 28,
                fontSize: 13,
                onTap: app.companionSyncBusy ? null : () => app.syncCompanionData(silent: false),
              ),
              const SizedBox(width: 6),
            ],
            P5Button(
              label: 'NEW',
              icon: Icons.add,
              variant: P5BtnVariant.primary,
              height: 28,
              fontSize: 11,
              onTap: () => _openNew('blank'),
            ),
            const SizedBox(width: 6),
            PopupMenuButton<String>(
              color: P5Colors.card,
              onSelected: _openNew,
              itemBuilder: (_) => const [
                PopupMenuItem(value: 'meeting', child: Text('Meeting Template')),
                PopupMenuItem(value: 'study', child: Text('Study Summary')),
                PopupMenuItem(value: 'daily', child: Text('Daily Log')),
              ],
              child: Container(
                height: 28,
                padding: const EdgeInsets.symmetric(horizontal: 9),
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: P5Colors.goldBg,
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(color: P5Colors.goldBorder),
                ),
                child: Text('TMPL ▾', style: P5Text.display(11, color: P5Colors.goldFg)),
              ),
            ),
          ],
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 84),
            children: [
              TextField(
                controller: _searchCtrl,
                style: P5Text.body(13.5, color: P5Colors.white),
                decoration: InputDecoration(
                  hintText: 'Search notes or #tags...',
                  prefixIcon: const Icon(Icons.search, size: 17, color: P5Colors.muted),
                  suffixIcon: q.isNotEmpty
                      ? IconButton(
                          icon: const Icon(Icons.close, size: 16, color: P5Colors.muted),
                          onPressed: () => setState(() => _searchCtrl.clear()),
                        )
                      : null,
                ),
                onChanged: (_) => setState(() {}),
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  for (final f in const [('ALL', 'all'), ('PINNED', 'pinned'), ('ARCHIVED', 'archived')]) ...[
                    Expanded(
                      child: P5Button(
                        label: f.$1,
                        active: _filter == f.$2,
                        height: 28,
                        fontSize: 10.5,
                        onTap: () => setState(() => _filter = f.$2),
                      ),
                    ),
                    if (f.$2 != 'archived') const SizedBox(width: 6),
                  ],
                ],
              ),
              if (tags.isNotEmpty) ...[
                const SizedBox(height: 8),
                SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: Row(
                    children: [
                      for (final tg in tags) ...[
                        P5Button(
                          label: '#$tg',
                          active: _activeTag == tg,
                          activeVariant: P5BtnVariant.gold,
                          height: 25,
                          fontSize: 10.5,
                          displayFont: false,
                          onTap: () => setState(() => _activeTag = _activeTag == tg ? '' : tg),
                        ),
                        const SizedBox(width: 5),
                      ],
                    ],
                  ),
                ),
              ],
              const SizedBox(height: 10),
              if (notes.isEmpty)
                P5Card(
                  padding: const EdgeInsets.symmetric(vertical: 28, horizontal: 16),
                  child: Center(
                    child: Text(
                      'No notes yet.\nTap NEW or pick a template above!',
                      textAlign: TextAlign.center,
                      style: P5Text.body(13.5, color: P5Colors.muted),
                    ),
                  ),
                )
              else
                for (final n in notes) _buildNoteCard(n),
              if (_undoNote != null)
                P5UndoBar(
                  message: _undoMsg,
                  onUndo: () async {
                    final r = _undoNote!;
                    setState(() => _undoNote = null);
                    await app.notes.restore(r);
                    app.notify('Note restored!', tone: P5ToastTone.ok);
                  },
                ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildNoteCard(Map<String, dynamic> n) {
    final pinned = n['pinned'] == true;
    final tags = NoteStore.noteTags(n);
    final preview = (n['content'] ?? '').toString().replaceAll('\n', ' ').trim();

    return GestureDetector(
      onTap: () => _openExisting(n),
      child: Container(
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.all(11),
        decoration: BoxDecoration(
          color: P5Colors.surface,
          borderRadius: BorderRadius.circular(9),
          border: Border.all(color: pinned ? P5Colors.goldBorder : P5Colors.border),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                if (pinned) ...[
                  const Icon(Icons.push_pin, size: 14, color: P5Colors.gold),
                  const SizedBox(width: 5),
                ],
                Expanded(
                  child: Text(
                    (n['title'] ?? 'Untitled').toString(),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: P5Text.body(14.5, color: P5Colors.white, bold: true),
                  ),
                ),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(color: P5Colors.card, borderRadius: BorderRadius.circular(4)),
                  child: Text((n['category'] ?? 'Personal').toString().toUpperCase(),
                      style: P5Text.display(9.5, color: P5Colors.goldFg)),
                ),
              ],
            ),
            if (preview.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(
                preview,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: P5Text.body(12.5, color: P5Colors.muted),
              ),
            ],
            if (tags.isNotEmpty) ...[
              const SizedBox(height: 5),
              Text(tags.map((t) => '#$t').join('  '), style: P5Text.body(11, color: P5Colors.gold, bold: true)),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildEditorView(MorganaAppState app) {
    return Column(
      children: [
        P5Header(
          title: 'EDITOR',
          onAvatarTap: () => setState(() => _editorOpen = false),
          actions: [
            P5Button(
              label: 'BACK',
              icon: Icons.arrow_back,
              height: 28,
              fontSize: 10.5,
              onTap: () => setState(() => _editorOpen = false),
            ),
            const SizedBox(width: 6),
            P5Button(
              label: 'SAVE',
              icon: Icons.save,
              variant: P5BtnVariant.primary,
              height: 28,
              fontSize: 10.5,
              onTap: _saveNote,
            ),
          ],
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 84),
            children: [
              // Category chips + Pin/Archive/Preview/Copy/Send-to-Task
              SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(
                  children: [
                    for (final c in kNoteCategories) ...[
                      P5Button(
                        label: c.toUpperCase(),
                        active: _category == c,
                        activeVariant: P5BtnVariant.gold,
                        height: 26,
                        fontSize: 10,
                        onTap: () => setState(() => _category = c),
                      ),
                      const SizedBox(width: 5),
                    ],
                  ],
                ),
              ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 6,
                runSpacing: 6,
                children: [
                  P5Button(
                    label: _previewMode ? 'EDIT' : 'PREVIEW',
                    icon: _previewMode ? Icons.edit : Icons.visibility,
                    variant: _previewMode ? P5BtnVariant.gold : P5BtnVariant.soft,
                    height: 28,
                    fontSize: 10.5,
                    onTap: () => setState(() => _previewMode = !_previewMode),
                  ),
                  P5Button(
                    label: _pinned ? 'UNPIN' : 'PIN',
                    icon: Icons.push_pin,
                    variant: _pinned ? P5BtnVariant.gold : P5BtnVariant.soft,
                    height: 28,
                    fontSize: 10.5,
                    onTap: _editingNoteId == null
                        ? null
                        : () async {
                            setState(() => _pinned = !_pinned);
                            await app.notes.setPinned(_editingNoteId!, _pinned);
                            app.notify(_pinned ? 'Note pinned to top' : 'Note unpinned',
                                tone: _pinned ? P5ToastTone.gold : P5ToastTone.info);
                          },
                  ),
                  P5Button(
                    label: _archived ? 'UNARCHIVE' : 'ARCHIVE',
                    icon: Icons.archive_outlined,
                    height: 28,
                    fontSize: 10.5,
                    onTap: _editingNoteId == null
                        ? null
                        : () async {
                            setState(() => _archived = !_archived);
                            await app.notes.setArchived(_editingNoteId!, _archived);
                            app.notify(_archived ? 'Moved to Archive' : 'Restored from Archive',
                                tone: P5ToastTone.info);
                          },
                  ),
                  P5Button(
                    label: 'COPY MD',
                    icon: Icons.copy,
                    height: 28,
                    fontSize: 10.5,
                    onTap: _copyMarkdown,
                  ),
                  P5Button(
                    label: '→ TASK',
                    icon: Icons.check_circle_outline,
                    variant: P5BtnVariant.mint,
                    height: 28,
                    fontSize: 10.5,
                    onTap: _promoteToTask,
                  ),
                  if (_editingNoteId != null)
                    P5Button(
                      label: 'DEL',
                      variant: P5BtnVariant.danger,
                      height: 28,
                      fontSize: 10.5,
                      onTap: () async {
                        final rem = await app.notes.delete(_editingNoteId!);
                        if (rem != null) {
                          _undoTimer?.cancel();
                          setState(() {
                            _undoNote = rem;
                            _undoMsg = "Deleted '${rem['title']}'";
                            _editorOpen = false;
                            _editingNoteId = null;
                          });
                          app.notify("Deleted '${rem['title']}' · Undo available", tone: P5ToastTone.info);
                        }
                      },
                    ),
                ],
              ),
              const SizedBox(height: 10),
              P5NudgeBox(
                nudged: _titleNudged,
                child: TextField(
                  controller: _titleCtrl,
                  style: P5Text.heading(20, color: P5Colors.white),
                  decoration: const InputDecoration(hintText: 'Note title...'),
                ),
              ),
              const SizedBox(height: 8),
              if (_previewMode)
                P5Card(
                  padding: const EdgeInsets.all(14),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      for (final line in _bodyCtrl.text.split('\n')) _renderMarkdownLine(line),
                    ],
                  ),
                )
              else
                TextField(
                  controller: _bodyCtrl,
                  minLines: 12,
                  maxLines: 28,
                  style: P5Text.body(14.5, color: P5Colors.white),
                  decoration: const InputDecoration(
                    hintText: 'Write your note here... Use #tags to organize & filter!',
                    alignLabelWithHint: true,
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _renderMarkdownLine(String line) {
    final s = line.trimRight();
    if (s.startsWith('## ')) {
      return Padding(
        padding: const EdgeInsets.only(top: 8, bottom: 3),
        child: Text(s.substring(3), style: P5Text.display(17, color: P5Colors.gold)),
      );
    }
    if (s.startsWith('# ')) {
      return Padding(
        padding: const EdgeInsets.only(top: 8, bottom: 4),
        child: Text(s.substring(2), style: P5Text.heading(20, color: P5Colors.red)),
      );
    }
    if (s.startsWith('- [x] ') || s.startsWith('- [X] ')) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 2),
        child: Text('☑  ${s.substring(6)}',
            style: P5Text.body(14, color: P5Colors.muted).copyWith(decoration: TextDecoration.lineThrough)),
      );
    }
    if (s.startsWith('- [ ] ')) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 2),
        child: Text('☐  ${s.substring(6)}', style: P5Text.body(14, color: P5Colors.white)),
      );
    }
    if (s.startsWith('- ') || s.startsWith('* ')) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 2),
        child: Text('•  ${s.substring(2)}', style: P5Text.body(14, color: P5Colors.text)),
      );
    }
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Text(s, style: P5Text.body(14, color: s.contains('#') ? P5Colors.goldFg : P5Colors.text)),
    );
  }
}
