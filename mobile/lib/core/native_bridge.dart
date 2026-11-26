import 'package:flutter/services.dart';

/// Clean MethodChannel wrapper for native Android Floating Bubble Overlay,
/// Service Account JSON file picker, and high-priority timer notifications.
/// Degrades gracefully in widget tests / non-Android hosts without throwing.
class NativeBridge {
  static const MethodChannel _ch = MethodChannel('com.morgana.companion/native');

  static void listenOpenTab(void Function(String tab) onOpenTab) {
    _ch.setMethodCallHandler((call) async {
      if (call.method == 'openTab' && call.arguments is String) {
        onOpenTab(call.arguments as String);
      }
    });
  }

  static Future<bool> canDrawOverlays() async {
    try {
      return (await _ch.invokeMethod<bool>('canDrawOverlays')) ?? false;
    } catch (_) {
      return false;
    }
  }

  static Future<void> requestOverlayPermission() async {
    try {
      await _ch.invokeMethod('requestOverlayPermission');
    } catch (_) {}
  }

  static Future<bool> startOverlay() async {
    try {
      return (await _ch.invokeMethod<bool>('startOverlay')) ?? false;
    } catch (_) {
      return false;
    }
  }

  static Future<void> stopOverlay() async {
    try {
      await _ch.invokeMethod('stopOverlay');
    } catch (_) {}
  }

  static Future<bool> isOverlayRunning() async {
    try {
      return (await _ch.invokeMethod<bool>('isOverlayRunning')) ?? false;
    } catch (_) {
      return false;
    }
  }

  static Future<String?> getPendingLaunchTab() async {
    try {
      return await _ch.invokeMethod<String>('getPendingLaunchTab');
    } catch (_) {
      return null;
    }
  }

  static Future<({String name, String content})?> pickJsonFile() async {
    try {
      final res = await _ch.invokeMethod<Map>('pickJsonFile');
      if (res == null) return null;
      return (
        name: (res['name'] ?? 'kunci-google.json').toString(),
        content: (res['content'] ?? '').toString(),
      );
    } catch (_) {
      return null;
    }
  }

  static Future<void> showNotification({
    required String title,
    required String body,
    String tab = 'timer',
  }) async {
    try {
      await _ch.invokeMethod('showNotification', {'title': title, 'body': body, 'tab': tab});
    } catch (_) {}
  }
}
