import 'dart:convert';

// ignore: deprecated_member_use
import 'package:flutter/material.dart';

import '../core/app_state.dart';
import '../core/native_bridge.dart';
import '../core/sheets_sync.dart';
import '../core/storage.dart';
import '../theme/p5_theme.dart';
import '../widgets/p5_widgets.dart';

/// Full v1.5.3 Settings screen: Floating Bubble Overlay, Audio, Curfew, Radial Tabs,
/// Gemini/OpenAI AI companion, and Google Sheets Service Account sync.
class SettingsScreen extends StatefulWidget {
  final MorganaAppState app;

  const SettingsScreen({super.key, required this.app});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late bool _soundEnabled;
  late double _voiceVol;
  late double _sfxVol;
  late bool _curfewEnabled;
  late bool _autoStatRewards;
  late bool _manualDnd;
  late List<String> _enabledTabs;

  late bool _aiEnabled;
  late String _aiProvider;
  bool _hideApiKey = true;

  late bool _sheetsEnabled;
  late bool _dataSheetsEnabled;
  late String _sheetsKeyName;
  late String _sheetsKeyJson;

  final _bedtimeCtrl = TextEditingController();
  final _wakeCtrl = TextEditingController();
  final _callUserCtrl = TextEditingController();
  final _aiModelCtrl = TextEditingController();
  final _aiKeyCtrl = TextEditingController();
  final _aiEndpointCtrl = TextEditingController();
  final _sheetIdCtrl = TextEditingController();
  final _sheetsTabCtrl = TextEditingController();

  bool _testingAi = false;
  bool _testingSheets = false;
  bool _syncingData = false;

  @override
  void initState() {
    super.initState();
    _loadFromStore();
  }

  void _loadFromStore() {
    final s = widget.app.settings;
    _soundEnabled = s['sound_enabled'] == true;
    _voiceVol = ((s['voice_volume'] as num?)?.toDouble() ?? 80).clamp(0, 100);
    _sfxVol = ((s['sfx_volume'] as num?)?.toDouble() ?? 75).clamp(0, 100);
    _curfewEnabled = s['curfew_enabled'] == true;
    _autoStatRewards = s['auto_stat_rewards'] == true;
    _manualDnd = s['manual_dnd'] == true;
    _enabledTabs = List<String>.from(s.enabledTabs);

    _bedtimeCtrl.text = (s['bedtime'] ?? '23:00').toString();
    _wakeCtrl.text = (s['wake'] ?? '06:00').toString();
    _callUserCtrl.text = (s['ai_call_user'] ?? 'Joker').toString();

    _aiEnabled = s['ai_enabled'] == true;
    _aiProvider = (s['ai_provider'] ?? 'gemini').toString();
    _aiModelCtrl.text = (s['ai_model'] ?? 'gemini-3.5-flash-lite').toString();
    _aiKeyCtrl.text = (s['ai_api_key'] ?? '').toString();
    _aiEndpointCtrl.text = (s['ai_endpoint'] ?? 'http://localhost:11434/v1/chat/completions').toString();

    _sheetsEnabled = s['finance_sheets_enabled'] == true;
    _dataSheetsEnabled = s['data_sheets_enabled'] == true;
    _sheetIdCtrl.text = (s['sheets_sync_id'] ?? '').toString();
    _sheetsTabCtrl.text = (s['sheets_sync_tab'] ?? 'Morgana').toString();
    _sheetsKeyName = (s['sheets_key_name'] ?? '').toString();
    _sheetsKeyJson = (s['sheets_key_json'] ?? '').toString();
  }

  @override
  void dispose() {
    _bedtimeCtrl.dispose();
    _wakeCtrl.dispose();
    _callUserCtrl.dispose();
    _aiModelCtrl.dispose();
    _aiKeyCtrl.dispose();
    _aiEndpointCtrl.dispose();
    _sheetIdCtrl.dispose();
    _sheetsTabCtrl.dispose();
    super.dispose();
  }

  String _serviceAccountEmailPreview() {
    if (_sheetsKeyJson.trim().isEmpty) return '';
    try {
      final obj = jsonDecode(_sheetsKeyJson);
      if (obj is Map && obj['client_email'] is String) {
        return obj['client_email'] as String;
      }
    } catch (_) {}
    return '';
  }

  Future<void> _importGoogleKey() async {
    final picked = await NativeBridge.pickJsonFile();
    if (picked == null) {
      widget.app.notify('No JSON file selected', tone: P5ToastTone.info, ms: 1600);
      return;
    }
    final (info, err) = validateServiceAccountJson(picked.content);
    if (info == null || err.isNotEmpty) {
      widget.app.audio.playSfx('cancel');
      widget.app.notify(err.isNotEmpty ? err : 'Invalid Google Service Account JSON!', tone: P5ToastTone.err, ms: 3200);
      return;
    }
    setState(() {
      _sheetsKeyName = picked.name;
      _sheetsKeyJson = picked.content;
    });
    widget.app.audio.playSfx('select');
    widget.app.notify("Loaded '${picked.name}'! Tap SAVE to apply.", tone: P5ToastTone.ok);
  }

  Future<void> _saveSettings() async {
    final cleanTabs = _enabledTabs.isEmpty ? List<String>.from(kRadialTabKeys) : _enabledTabs;
    final rawSheet = _sheetIdCtrl.text.trim();
    final extracted = extractSpreadsheetId(rawSheet);
    final storedSheetId = extracted.isNotEmpty ? extracted : rawSheet;
    final cleanTab = _sheetsTabCtrl.text.trim().isEmpty ? 'Morgana' : _sheetsTabCtrl.text.trim();

    await widget.app.settings.updateAll({
      'sound_enabled': _soundEnabled,
      'voice_volume': _voiceVol.round(),
      'sfx_volume': _sfxVol.round(),
      'curfew_enabled': _curfewEnabled,
      'bedtime': _bedtimeCtrl.text.trim().isEmpty ? '23:00' : _bedtimeCtrl.text.trim(),
      'wake': _wakeCtrl.text.trim().isEmpty ? '06:00' : _wakeCtrl.text.trim(),
      'auto_stat_rewards': _autoStatRewards,
      'manual_dnd': _manualDnd,
      'enabled_tabs': cleanTabs,
      'ai_enabled': _aiEnabled,
      'ai_provider': _aiProvider,
      'ai_model': _aiModelCtrl.text.trim().isEmpty ? 'gemini-3.5-flash-lite' : _aiModelCtrl.text.trim(),
      'ai_api_key': _aiKeyCtrl.text.trim(),
      'ai_endpoint': _aiEndpointCtrl.text.trim(),
      'ai_call_user': _callUserCtrl.text.trim().isEmpty ? 'Joker' : _callUserCtrl.text.trim(),
      'finance_sheets_enabled': _sheetsEnabled,
      'data_sheets_enabled': _dataSheetsEnabled,
      'sheets_sync_id': storedSheetId,
      'sheets_sync_tab': cleanTab,
      'sheets_key_name': _sheetsKeyName,
      'sheets_key_json': _sheetsKeyJson,
    });

    widget.app.audio.applySettings(
      soundEnabled: _soundEnabled,
      voiceVol: _voiceVol.round(),
      sfxVol: _sfxVol.round(),
    );
    widget.app.audio.playSfx('select');
    widget.app.notify('Settings saved!', tone: P5ToastTone.ok);
    if (widget.app.settings.financeSheetsActive) {
      widget.app.refreshFinanceSheet(silent: true);
    }
    if (widget.app.settings.dataSheetsActive) {
      widget.app.syncCompanionData(silent: true);
    }
    setState(() {});
  }

  Future<void> _testAi() async {
    if (_testingAi) return;
    setState(() => _testingAi = true);
    final (ok, msg) = await widget.app.aiClient.testConnection(
      provider: _aiProvider,
      apiKey: _aiKeyCtrl.text.trim(),
      model: _aiModelCtrl.text.trim(),
      endpoint: _aiEndpointCtrl.text.trim(),
      callUser: _callUserCtrl.text.trim(),
    );
    if (!mounted) return;
    setState(() => _testingAi = false);
    widget.app.notify(msg, tone: ok ? P5ToastTone.ok : P5ToastTone.err, ms: 3200);
  }

  Future<void> _testSheets() async {
    if (_testingSheets) return;
    setState(() => _testingSheets = true);
    final (ok, msg) = await widget.app.sheetsClient.testConnection(
      rawSheetId: _sheetIdCtrl.text.trim(),
      keyJson: _sheetsKeyJson,
      checkFinance: _sheetsEnabled || !_dataSheetsEnabled,
      checkDataSync: _dataSheetsEnabled || !_sheetsEnabled,
    );
    if (!mounted) return;
    setState(() => _testingSheets = false);
    widget.app.notify(msg, tone: ok ? P5ToastTone.ok : P5ToastTone.err, ms: 3400);
  }

  Future<void> _syncAllDataNow() async {
    if (_syncingData) return;
    setState(() => _syncingData = true);
    await widget.app.syncCompanionData(
      silent: false,
      forceDeadlines: true,
      sheetIdOverride: _sheetIdCtrl.text.trim(),
      keyJsonOverride: _sheetsKeyJson,
      deadlineTabOverride: _sheetsTabCtrl.text.trim(),
    );
    if (!mounted) return;
    setState(() => _syncingData = false);
  }

  Future<void> _resetDefaults() async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: P5Colors.card,
        title: Text('RESET SETTINGS?', style: P5Text.display(16, color: P5Colors.gold)),
        content: Text(
          'Restore all settings to defaults? Your tasks, notes, finance, and stats will NOT be deleted.',
          style: P5Text.body(13, color: P5Colors.white),
        ),
        actions: [
          P5Button(label: 'CANCEL', variant: P5BtnVariant.soft, onTap: () => Navigator.pop(ctx, false)),
          P5Button(label: 'RESET', variant: P5BtnVariant.danger, onTap: () => Navigator.pop(ctx, true)),
        ],
      ),
    );
    if (confirm != true) return;
    await widget.app.settings.updateAll(defaultSettings());
    _loadFromStore();
    widget.app.notify('Restored default settings', tone: P5ToastTone.gold);
    setState(() {});
  }

  void _toggleRadialTab(String key) {
    if (_enabledTabs.contains(key)) {
      if (_enabledTabs.length <= 1) {
        widget.app.audio.playSfx('cancel');
        widget.app.notify('Keep at least 1 radial menu tab enabled!', tone: P5ToastTone.warn);
        return;
      }
      setState(() => _enabledTabs.remove(key));
    } else {
      setState(() => _enabledTabs.add(key));
    }
  }

  @override
  Widget build(BuildContext context) {
    final app = widget.app;
    final saEmail = _serviceAccountEmailPreview();

    return Column(
      children: [
        P5Header(
          title: 'Settings',
          onAvatarTap: () => app.navigateTo('home'),
          actions: [
            P5Button(
              label: 'SAVE',
              variant: P5BtnVariant.primary,
              height: 32,
              fontSize: 12,
              onTap: _saveSettings,
            ),
          ],
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 86),
            children: [
              // 1. Floating Morgana Bubble Overlay (Android)
              P5Card(
                topColor: const Color(0xFF280D15),
                borderColor: app.overlayRunning ? P5Colors.mintBorder : P5Colors.red,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            'FLOATING MORGANA BUBBLE',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: P5Text.display(13, color: P5Colors.gold),
                          ),
                        ),
                        const SizedBox(width: 8),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                          decoration: BoxDecoration(
                            color: app.overlayRunning ? P5Colors.mintBg : P5Colors.surface,
                            borderRadius: BorderRadius.circular(4),
                            border: Border.all(
                              color: app.overlayRunning ? P5Colors.mintBorder : P5Colors.border,
                            ),
                          ),
                          child: Text(
                            app.overlayRunning
                                ? 'LIVE'
                                : (app.overlayPermission ? 'OFF' : 'NEED PERMISSION'),
                            style: P5Text.display(10, color: app.overlayRunning ? P5Colors.mint : P5Colors.muted),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Text(
                      'Keep a draggable Morgana badge floating above other apps with quick shortcuts to Finance, Tasks & Ask.',
                      style: P5Text.body(12, color: P5Colors.muted),
                    ),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        Expanded(
                          child: SwitchListTile(
                            dense: true,
                            contentPadding: EdgeInsets.zero,
                            title: Text('Enable Floating Overlay', style: P5Text.body(13, color: P5Colors.white, bold: true)),
                            value: app.overlayRunning,
                            onChanged: (v) => app.setFloatingBubbleEnabled(v),
                          ),
                        ),
                      ],
                    ),
                    if (!app.overlayPermission)
                      P5Button(
                        label: 'GRANT OVERLAY PERMISSION',
                        variant: P5BtnVariant.gold,
                        height: 34,
                        expand: true,
                        onTap: () => app.setFloatingBubbleEnabled(true),
                      ),
                  ],
                ),
              ),
              const SizedBox(height: 10),

              // 2. Audio & Companion Behavior
              P5Card(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('AUDIO & COMPANION', style: P5Text.display(13, color: P5Colors.gold)),
                    SwitchListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      title: Text('Sound Effects & Morgana Voice', style: P5Text.body(13, color: P5Colors.white)),
                      value: _soundEnabled,
                      onChanged: (v) => setState(() => _soundEnabled = v),
                    ),
                    Row(
                      children: [
                        SizedBox(width: 68, child: Text('VOICE ${_voiceVol.round()}%', style: P5Text.display(11, color: P5Colors.muted))),
                        Expanded(
                          child: Slider(
                            value: _voiceVol,
                            min: 0,
                            max: 100,
                            activeColor: P5Colors.red,
                            onChanged: (v) => setState(() => _voiceVol = v),
                          ),
                        ),
                        P5Button(
                          label: 'TEST',
                          variant: P5BtnVariant.soft,
                          height: 28,
                          fontSize: 10.5,
                          onTap: () {
                            app.audio.applySettings(
                              soundEnabled: true,
                              voiceVol: _voiceVol.round(),
                              sfxVol: _sfxVol.round(),
                            );
                            app.audio.playVoice('looking_cool');
                          },
                        ),
                      ],
                    ),
                    Row(
                      children: [
                        SizedBox(width: 68, child: Text('UI SFX ${_sfxVol.round()}%', style: P5Text.display(11, color: P5Colors.muted))),
                        Expanded(
                          child: Slider(
                            value: _sfxVol,
                            min: 0,
                            max: 100,
                            activeColor: P5Colors.gold,
                            onChanged: (v) => setState(() => _sfxVol = v),
                          ),
                        ),
                        P5Button(
                          label: 'TEST',
                          variant: P5BtnVariant.soft,
                          height: 28,
                          fontSize: 10.5,
                          onTap: () {
                            app.audio.applySettings(
                              soundEnabled: true,
                              voiceVol: _voiceVol.round(),
                              sfxVol: _sfxVol.round(),
                            );
                            app.audio.playSfx('select');
                          },
                        ),
                      ],
                    ),
                    const Divider(color: P5Colors.border),
                    SwitchListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      title: Text('Auto Social Stat Rewards on Task Done', style: P5Text.body(13, color: P5Colors.white)),
                      value: _autoStatRewards,
                      onChanged: (v) => setState(() => _autoStatRewards = v),
                    ),
                    SwitchListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      title: Text('Bedtime Curfew Reminder', style: P5Text.body(13, color: P5Colors.white)),
                      value: _curfewEnabled,
                      onChanged: (v) => setState(() => _curfewEnabled = v),
                    ),
                    SwitchListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      title: Text('Do Not Disturb (Mute Voice)', style: P5Text.body(13, color: P5Colors.white)),
                      value: _manualDnd,
                      onChanged: (v) => setState(() => _manualDnd = v),
                    ),
                    const SizedBox(height: 6),
                    Row(
                      children: [
                        Expanded(
                          child: TextField(
                            controller: _bedtimeCtrl,
                            decoration: const InputDecoration(labelText: 'Bedtime (HH:MM)'),
                          ),
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: TextField(
                            controller: _wakeCtrl,
                            decoration: const InputDecoration(labelText: 'Wake (HH:MM)'),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    TextField(
                      controller: _callUserCtrl,
                      decoration: const InputDecoration(labelText: 'Call me (e.g. Joker)'),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 10),

              // 3. Radial Menu Tabs
              P5Card(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('RADIAL MENU TABS', style: P5Text.display(13, color: P5Colors.gold)),
                    const SizedBox(height: 4),
                    Text('Choose which ribbons fan out around Morgana on the Home stage.', style: P5Text.body(11.5, color: P5Colors.muted)),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        for (final key in kRadialTabKeys)
                          P5Button(
                            label: key.toUpperCase(),
                            active: _enabledTabs.contains(key),
                            activeVariant: P5BtnVariant.primary,
                            height: 30,
                            fontSize: 11,
                            onTap: () => _toggleRadialTab(key),
                          ),
                      ],
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 10),

              // 4. Google Sheets Sync (2-Way Tasks/Notes/Stats/Events + bot-wa Finance)
              P5Card(
                borderColor: (_sheetsEnabled || _dataSheetsEnabled) ? P5Colors.mintBorder : P5Colors.border,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('GOOGLE SHEETS SYNC (2-WAY + BOT-WA)', style: P5Text.display(13, color: P5Colors.gold)),
                    SwitchListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      title: Text('Sync Tasks, Notes, Stats & Events (2-Way)', style: P5Text.body(13, color: P5Colors.white, bold: true)),
                      subtitle: Text('Laptop ↔ Android via Morgana_Sync + WhatsApp deadline tab', style: P5Text.body(11, color: P5Colors.muted)),
                      value: _dataSheetsEnabled,
                      onChanged: (v) => setState(() => _dataSheetsEnabled = v),
                    ),
                    SwitchListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      title: Text('Live Monthly_Budget Finance Sync (bot-wa)', style: P5Text.body(13, color: P5Colors.white)),
                      value: _sheetsEnabled,
                      onChanged: (v) => setState(() => _sheetsEnabled = v),
                    ),
                    const SizedBox(height: 4),
                    TextField(
                      controller: _sheetIdCtrl,
                      decoration: const InputDecoration(
                        labelText: 'Spreadsheet URL or ID',
                        hintText: 'https://docs.google.com/spreadsheets/d/...',
                      ),
                    ),
                    const SizedBox(height: 8),
                    TextField(
                      controller: _sheetsTabCtrl,
                      decoration: const InputDecoration(
                        labelText: 'Tasks Deadline Tab (bot-wa)',
                        hintText: 'Morgana',
                      ),
                    ),
                    const SizedBox(height: 8),
                    Container(
                      width: double.infinity,
                      padding: const EdgeInsets.all(9),
                      decoration: BoxDecoration(
                        color: P5Colors.surface,
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(color: _sheetsKeyJson.isNotEmpty ? P5Colors.mintBorder : P5Colors.border),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            _sheetsKeyJson.isNotEmpty
                                ? 'KEY LOADED: ${_sheetsKeyName.isEmpty ? "kunci-google.json" : _sheetsKeyName}'
                                : 'NO SERVICE ACCOUNT KEY LOADED',
                            style: P5Text.display(
                              11.5,
                              color: _sheetsKeyJson.isNotEmpty ? P5Colors.mint : P5Colors.muted,
                            ),
                          ),
                          if (saEmail.isNotEmpty) ...[
                            const SizedBox(height: 2),
                            Text(saEmail, style: P5Text.body(11, color: P5Colors.muted)),
                          ],
                        ],
                      ),
                    ),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        Expanded(
                          child: P5Button(
                            label: 'IMPORT kunci-google.json',
                            icon: Icons.upload_file,
                            variant: P5BtnVariant.gold,
                            height: 36,
                            fontSize: 11.5,
                            expand: true,
                            onTap: _importGoogleKey,
                          ),
                        ),
                        if (_sheetsKeyJson.isNotEmpty) ...[
                          const SizedBox(width: 6),
                          P5Button(
                            label: 'CLEAR',
                            variant: P5BtnVariant.danger,
                            height: 36,
                            fontSize: 11,
                            onTap: () => setState(() {
                              _sheetsKeyJson = '';
                              _sheetsKeyName = '';
                            }),
                          ),
                        ],
                      ],
                    ),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        Expanded(
                          child: P5Button(
                            label: _testingSheets ? 'TESTING...' : 'TEST SHEETS',
                            variant: P5BtnVariant.soft,
                            height: 36,
                            fontSize: 11.5,
                            expand: true,
                            onTap: _testingSheets ? null : _testSheets,
                          ),
                        ),
                        const SizedBox(width: 6),
                        Expanded(
                          child: P5Button(
                            label: (_syncingData || app.companionSyncBusy) ? 'SYNCING...' : 'SYNC ALL DATA',
                            icon: Icons.sync,
                            variant: P5BtnVariant.mint,
                            height: 36,
                            fontSize: 11.5,
                            expand: true,
                            onTap: (_syncingData || app.companionSyncBusy) ? null : _syncAllDataNow,
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 10),

              // 5. Gemini / OpenAI AI Companion
              P5Card(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('AI COMPANION (OPT-IN)', style: P5Text.display(13, color: P5Colors.gold)),
                    SwitchListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      title: Text('Enable Live AI Chat in Ask Morgana', style: P5Text.body(13, color: P5Colors.white)),
                      value: _aiEnabled,
                      onChanged: (v) => setState(() => _aiEnabled = v),
                    ),
                    Row(
                      children: [
                        Expanded(
                          child: P5Button(
                            label: 'GEMINI',
                            active: _aiProvider == 'gemini',
                            activeVariant: P5BtnVariant.primary,
                            height: 32,
                            expand: true,
                            onTap: () => setState(() => _aiProvider = 'gemini'),
                          ),
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: P5Button(
                            label: 'OPENAI / LOCAL',
                            active: _aiProvider == 'openai',
                            activeVariant: P5BtnVariant.gold,
                            height: 32,
                            expand: true,
                            onTap: () => setState(() => _aiProvider = 'openai'),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    TextField(
                      controller: _aiModelCtrl,
                      decoration: const InputDecoration(labelText: 'Model Name'),
                    ),
                    const SizedBox(height: 6),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        P5Button(
                          label: 'gemini-3.5-flash-lite',
                          variant: P5BtnVariant.soft,
                          height: 26,
                          fontSize: 10.5,
                          displayFont: false,
                          onTap: () => setState(() => _aiModelCtrl.text = 'gemini-3.5-flash-lite'),
                        ),
                        P5Button(
                          label: 'gemini-2.5-flash',
                          variant: P5BtnVariant.soft,
                          height: 26,
                          fontSize: 10.5,
                          displayFont: false,
                          onTap: () => setState(() => _aiModelCtrl.text = 'gemini-2.5-flash'),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    TextField(
                      controller: _aiKeyCtrl,
                      obscureText: _hideApiKey,
                      decoration: InputDecoration(
                        labelText: 'API Key',
                        suffixIcon: IconButton(
                          icon: Icon(_hideApiKey ? Icons.visibility : Icons.visibility_off, color: P5Colors.muted, size: 19),
                          onPressed: () => setState(() => _hideApiKey = !_hideApiKey),
                        ),
                      ),
                    ),
                    if (_aiProvider == 'openai') ...[
                      const SizedBox(height: 8),
                      TextField(
                        controller: _aiEndpointCtrl,
                        decoration: const InputDecoration(labelText: 'Chat Completions Endpoint URL'),
                      ),
                    ],
                    const SizedBox(height: 8),
                    P5Button(
                      label: _testingAi ? 'TESTING AI...' : 'TEST AI CONNECTION',
                      variant: P5BtnVariant.soft,
                      height: 36,
                      expand: true,
                      onTap: _testingAi ? null : _testAi,
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 14),

              Row(
                children: [
                  Expanded(
                    child: P5Button(
                      label: 'SAVE ALL SETTINGS',
                      variant: P5BtnVariant.primary,
                      height: 44,
                      fontSize: 14,
                      expand: true,
                      onTap: _saveSettings,
                    ),
                  ),
                  const SizedBox(width: 8),
                  P5Button(
                    label: 'RESET',
                    variant: P5BtnVariant.danger,
                    height: 44,
                    onTap: _resetDefaults,
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }
}
