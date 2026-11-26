import 'dart:math';

import 'package:audioplayers/audioplayers.dart';

import 'emotions.dart';

/// Separate Voice & UI SFX audio player with graceful fallback on headless/test platforms.
class MorganaAudioService {
  AudioPlayer? _voicePlayer;
  AudioPlayer? _sfxPlayer;
  final Random _rng = Random();
  final Map<String, String> _lastPoolChoice = {};

  bool enabled = true;
  int voiceVolume = 80;
  int sfxVolume = 75;
  bool _disposed = false;

  void applySettings({required bool soundEnabled, required int voiceVol, required int sfxVol}) {
    enabled = soundEnabled;
    voiceVolume = voiceVol.clamp(0, 100);
    sfxVolume = sfxVol.clamp(0, 100);
  }

  /// Resolve a voice key or semantic pool name with anti-repeat rotation.
  String resolveVoiceKey(String key) {
    final pool = kVoicePools[key];
    if (pool != null && pool.isNotEmpty) {
      final valid = pool.where(kVoiceAssets.containsKey).toList();
      if (valid.isNotEmpty) {
        final prev = _lastPoolChoice[key];
        final choices = (valid.length > 1 && prev != null)
            ? valid.where((k) => k != prev).toList()
            : valid;
        final picked = choices[_rng.nextInt(choices.length)];
        _lastPoolChoice[key] = picked;
        return picked;
      }
    }
    return key;
  }

  Future<void> playVoice(String key) async {
    if (_disposed || !enabled || voiceVolume <= 0) return;
    final resolved = resolveVoiceKey(key);
    final asset = kVoiceAssets[resolved];
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
