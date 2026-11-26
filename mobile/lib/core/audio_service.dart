import 'package:audioplayers/audioplayers.dart';

import 'emotions.dart';

/// Separate Voice & UI SFX audio player with graceful fallback on headless/test platforms.
class MorganaAudioService {
  AudioPlayer? _voicePlayer;
  AudioPlayer? _sfxPlayer;

  bool enabled = true;
  int voiceVolume = 80;
  int sfxVolume = 75;
  bool _disposed = false;

  void applySettings({required bool soundEnabled, required int voiceVol, required int sfxVol}) {
    enabled = soundEnabled;
    voiceVolume = voiceVol.clamp(0, 100);
    sfxVolume = sfxVol.clamp(0, 100);
  }

  Future<void> playVoice(String key) async {
    if (_disposed || !enabled || voiceVolume <= 0) return;
    final asset = kVoiceAssets[key];
    if (asset == null) return;
    try {
      final p = _voicePlayer ??= AudioPlayer();
      await p.stop();
      await p.setVolume(voiceVolume / 100.0);
      await p.play(AssetSource(asset));
    } catch (_) {}
  }

  Future<void> playSfx(String key) async {
    if (_disposed || !enabled || sfxVolume <= 0) return;
    final asset = kUiSfxAssets[key];
    if (asset == null) return;
    try {
      final p = _sfxPlayer ??= AudioPlayer();
      await p.stop();
      await p.setVolume(sfxVolume / 100.0);
      await p.play(AssetSource(asset));
    } catch (_) {}
  }

  Future<void> dispose() async {
    _disposed = true;
    try {
      await _voicePlayer?.dispose();
      await _sfxPlayer?.dispose();
    } catch (_) {}
  }
}
