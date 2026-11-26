import 'dart:convert';

import 'package:http/http.dart' as http;

import 'emotions.dart';

/// Opt-in Gemini & OpenAI-compatible AI companion client for Morgana <-> Joker banter.

class AiReply {
  final bool ok;
  final String text;
  final String emotion;
  final String voice;
  final String action;
  final String badge;

  const AiReply({
    required this.ok,
    required this.text,
    this.emotion = 'neutral',
    this.voice = 'hmm',
    this.action = '',
    this.badge = 'MORGANA',
  });
}

class MorganaAiClient {
  final http.Client _http;
  final List<Map<String, String>> _history = [];

  MorganaAiClient({http.Client? client}) : _http = client ?? http.Client();

  bool get hasHistory => _history.isNotEmpty;

  void clearHistory() => _history.clear();

  String _systemPrompt(String callUser, String contextNote) {
    final name = callUser.trim().isEmpty ? 'Joker' : callUser.trim();
    return '''
You are Morgana from Persona 5, acting as a witty, caring, energetic desktop & mobile companion for $name.
Rules:
- Stay in character as Morgana (insist you are NOT a cat if teased, encourage good rest, celebrate progress).
- Match the user's language (Indonesian or English) naturally.
- Keep replies punchy and conversational (1-3 short sentences).
- Return STRICT JSON only: {"reply":"...","emotion":"neutral|wink|serious|worried|angry|sad|uneasy|excited|shout|smug","action":"|focus_25|break_5|open_finance|open_tasks|open_notes|open_events|open_stats"}
Context: $contextNote
''';
  }

  Future<AiReply> ask({
    required String provider,
    required String apiKey,
    required String model,
    required String endpoint,
    required String callUser,
    required String userText,
    String contextNote = '',
  }) async {
    final prov = provider.trim().toLowerCase();
    if (prov == 'gemini' && apiKey.trim().isEmpty) {
      return const AiReply(
        ok: false,
        text: 'Add your Gemini API key in Settings to enable live AI chat!',
        emotion: 'uneasy',
        voice: 'oh_asking',
        badge: 'AI OFF',
      );
    }

    try {
      String rawText;
      if (prov == 'gemini') {
        rawText = await _callGemini(
          apiKey: apiKey.trim(),
          model: model.trim().isEmpty ? 'gemini-3.5-flash-lite' : model.trim(),
          systemPrompt: _systemPrompt(callUser, contextNote),
          userText: userText,
        );
      } else {
        rawText = await _callOpenAi(
          endpoint: endpoint.trim(),
          apiKey: apiKey.trim(),
          model: model.trim().isEmpty ? 'llama3.2' : model.trim(),
          systemPrompt: _systemPrompt(callUser, contextNote),
          userText: userText,
        );
      }

      final parsed = _parseMorganaJson(rawText);
      _history.add({'role': 'user', 'text': userText});
      _history.add({'role': 'model', 'text': parsed.text});
      if (_history.length > 16) {
        _history.removeRange(0, _history.length - 16);
      }
      return parsed;
    } catch (e) {
      return AiReply(
        ok: false,
        text: "Couldn't reach AI right now ($e). You can still use offline commands!",
        emotion: 'uneasy',
        voice: 'oh_asking',
        badge: 'AI ERR',
      );
    }
  }

  Future<String> _callGemini({
    required String apiKey,
    required String model,
    required String systemPrompt,
    required String userText,
  }) async {
    final contents = <Map<String, dynamic>>[
      for (final h in _history)
        {
          'role': h['role'] == 'user' ? 'user' : 'model',
          'parts': [
            {'text': h['text']}
          ],
        },
      {
        'role': 'user',
        'parts': [
          {'text': userText}
        ],
      },
    ];
    final body = jsonEncode({
      'system_instruction': {
        'parts': [
          {'text': systemPrompt}
        ],
      },
      'contents': contents,
      'generationConfig': {'temperature': 0.7, 'maxOutputTokens': 260},
    });

    Future<http.Response> send(String m) {
      final uri = Uri.parse(
        'https://generativelanguage.googleapis.com/v1beta/models/$m:generateContent?key=${Uri.encodeQueryComponent(apiKey)}',
      );
      return _http
          .post(uri, headers: {'Content-Type': 'application/json'}, body: body)
          .timeout(const Duration(seconds: 15));
    }

    var resp = await send(model);
    if (resp.statusCode == 404 && model != 'gemini-2.5-flash') {
      resp = await send('gemini-2.5-flash');
    }
    if (resp.statusCode != 200) {
      throw 'HTTP ${resp.statusCode}';
    }
    final data = jsonDecode(resp.body) as Map<String, dynamic>;
    final cands = data['candidates'] as List? ?? const [];
    if (cands.isEmpty) throw 'Empty response';
    final parts = ((cands.first as Map)['content'] as Map?)?['parts'] as List? ?? const [];
    return parts.map((p) => (p as Map)['text']?.toString() ?? '').join().trim();
  }

  Future<String> _callOpenAi({
    required String endpoint,
    required String apiKey,
    required String model,
    required String systemPrompt,
    required String userText,
  }) async {
    final headers = <String, String>{'Content-Type': 'application/json'};
    if (apiKey.isNotEmpty) headers['Authorization'] = 'Bearer $apiKey';
    final msgs = <Map<String, String>>[
      {'role': 'system', 'content': systemPrompt},
      for (final h in _history) {'role': h['role'] == 'user' ? 'user' : 'assistant', 'content': h['text'] ?? ''},
      {'role': 'user', 'content': userText},
    ];
    final resp = await _http
        .post(Uri.parse(endpoint), headers: headers, body: jsonEncode({'model': model, 'messages': msgs}))
        .timeout(const Duration(seconds: 18));
    if (resp.statusCode != 200) throw 'HTTP ${resp.statusCode}';
    final data = jsonDecode(resp.body) as Map<String, dynamic>;
    final choices = data['choices'] as List? ?? const [];
    if (choices.isEmpty) throw 'Empty response';
    return ((choices.first as Map)['message'] as Map?)?['content']?.toString().trim() ?? '';
  }

  AiReply _parseMorganaJson(String raw) {
    final cleaned = raw.replaceAll(RegExp(r'^```(?:json)?\s*|\s*```$'), '').trim();
    try {
      final start = cleaned.indexOf('{');
      final end = cleaned.lastIndexOf('}');
      if (start >= 0 && end > start) {
        final obj = jsonDecode(cleaned.substring(start, end + 1)) as Map<String, dynamic>;
        final reply = (obj['reply'] ?? '').toString().trim();
        if (reply.isNotEmpty) {
          final em = (obj['emotion'] ?? '').toString().trim().toLowerCase();
          final emo = kMorganaExpressions.containsKey(em) ? em : emotionForText(reply);
          final act = (obj['action'] ?? '').toString().trim().toLowerCase();
          return AiReply(
            ok: true,
            text: reply,
            emotion: emo,
            voice: voiceForDialogue(reply, emotion: emo),
            action: act,
            badge: 'MORGANA · AI',
          );
        }
      }
    } catch (_) {}
    final txt = cleaned.isEmpty ? "Looking cool! What's our next move?" : cleaned;
    final emo = emotionForText(txt);
    return AiReply(
      ok: true,
      text: txt,
      emotion: emo,
      voice: voiceForDialogue(txt, emotion: emo),
      badge: 'MORGANA · AI',
    );
  }

  Future<(bool, String)> testConnection({
    required String provider,
    required String apiKey,
    required String model,
    required String endpoint,
    required String callUser,
  }) async {
    final res = await ask(
      provider: provider,
      apiKey: apiKey,
      model: model,
      endpoint: endpoint,
      callUser: callUser,
      userText: 'Say a 5-word greeting to confirm connection.',
    );
    return (res.ok, res.ok ? 'Connected! "${res.text}"' : res.text);
  }
}
