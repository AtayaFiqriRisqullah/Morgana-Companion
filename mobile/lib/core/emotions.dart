// Morgana expression selector, voiceline selector, and sprite layout tables.
// Exact port of desktop `emotions.py` & `sprites.py`.

class SpriteLayout {
  final double eyeX;
  final double eyeY;
  final double mouthX;
  final double mouthY;
  final int srcSize;

  const SpriteLayout({
    required this.eyeX,
    required this.eyeY,
    required this.mouthX,
    required this.mouthY,
    required this.srcSize,
  });
}

class ExpressionMeta {
  final String fileNum;
  final SpriteLayout layout;
  final bool hasEyes;

  const ExpressionMeta(this.fileNum, this.layout, {this.hasEyes = true});

  String baseAsset() => 'assets/Morgana/b003_${fileNum}_00.png';
  String eyeAsset(int eyeState) =>
      eyeState == 1 ? 'assets/Morgana/b003_${fileNum}_00_e2.png' : 'assets/Morgana/b003_${fileNum}_00_e1.png';
  String mouthAsset(int mouthState) => 'assets/Morgana/b003_${fileNum}_00_m$mouthState.png';
}

const SpriteLayout _layoutA = SpriteLayout(eyeX: 194, eyeY: 176, mouthX: 207, mouthY: 451, srcSize: 768);
const SpriteLayout _layoutB = SpriteLayout(eyeX: 151, eyeY: 213, mouthX: 182, mouthY: 470, srcSize: 768);
const SpriteLayout _layoutC = SpriteLayout(eyeX: 456, eyeY: 280, mouthX: 448, mouthY: 662, srcSize: 1536);

const Map<String, ExpressionMeta> kMorganaExpressions = {
  'neutral': ExpressionMeta('000', _layoutA),
  'wink': ExpressionMeta('001', _layoutA, hasEyes: false),
  'serious': ExpressionMeta('002', _layoutA),
  'worried': ExpressionMeta('003', _layoutB),
  'angry': ExpressionMeta('004', _layoutC),
  'sad': ExpressionMeta('005', _layoutB),
  'uneasy': ExpressionMeta('007', _layoutB),
  'excited': ExpressionMeta('008', _layoutA, hasEyes: false),
  'shout': ExpressionMeta('010', _layoutC),
  'smug': ExpressionMeta('011', _layoutA),
};

const Map<String, String> kVoiceAssets = {
  // Original 8 Japanese Morgana clips
  'dousuru': 'audio/dousuru morgana.mp3',
  'hmm': 'audio/hmm approve morgana.mp3',
  'hoi': 'audio/hoi morgana .mp3',
  'naa': 'audio/naa naa morgana.mp3',
  'oh_asking': 'audio/oh asking morgana.mp3',
  'oh_excited': 'audio/oh excited morgana.mp3',
  'uwooh': 'audio/uwooh morgana.mp3',
  'yareyare': 'audio/yareyare morgana .mp3',
  // Curated 36 Persona 5 Royal Japanese Morgana reaction clips
  'ohayou': 'audio/morgana_ohayou.wav',
  'choushi': 'audio/morgana_choushi.wav',
  'taihen': 'audio/morgana_taihen.wav',
  'oi': 'audio/morgana_oi.wav',
  'nyaa': 'audio/morgana_nyaa.wav',
  'maana': 'audio/morgana_maana.wav',
  'sigh': 'audio/morgana_sigh.wav',
  'oicho': 'audio/morgana_oicho.wav',
  'gokurou': 'audio/morgana_gokurou.wav',
  'chigau': 'audio/morgana_chigau.wav',
  'maji': 'audio/morgana_maji.wav',
  'sleepy_nyaa': 'audio/morgana_sleepy_nyaa.wav',
  'iizo': 'audio/morgana_iizo.wav',
  'wahaha': 'audio/morgana_wahaha.wav',
  'ikuzo': 'audio/morgana_ikuzo.wav',
  'shigoto': 'audio/morgana_shigoto.wav',
  'nyahaha': 'audio/morgana_nyahaha.wav',
  'kensan': 'audio/morgana_kensan.wav',
  'icchou': 'audio/morgana_icchou.wav',
  'yarujanee': 'audio/morgana_yarujanee.wav',
  'yawn': 'audio/morgana_yawn.wav',
  'asameshi': 'audio/morgana_asameshi.wav',
  'kaigi_start': 'audio/morgana_kaigi_start.wav',
  'kaigi_end': 'audio/morgana_kaigi_end.wav',
  'naruhodo': 'audio/morgana_naruhodo.wav',
  'kangaero': 'audio/morgana_kangaero.wav',
  'dekita': 'audio/morgana_dekita.wav',
  'kaketa': 'audio/morgana_kaketa.wav',
  'kettei': 'audio/morgana_kettei.wav',
  'sugoi': 'audio/morgana_sugoi.wav',
  'fumu': 'audio/morgana_fumu.wav',
  'unun': 'audio/morgana_unun.wav',
  'sateto': 'audio/morgana_sateto.wav',
  'yoshi': 'audio/morgana_yoshi.wav',
  'acchaa': 'audio/morgana_acchaa.wav',
  'anosaa': 'audio/morgana_anosaa.wav',
};

/// Semantic anti-repeat rotation pools matching desktop `audio_manager.VOICE_POOLS`.
const Map<String, List<String>> kVoicePools = {
  'oh_excited': ['oh_excited', 'sugoi', 'yarujanee', 'wahaha', 'yoshi'],
  'hmm': ['hmm', 'unun', 'iizo', 'maana', 'fumu'],
  'hoi': ['hoi', 'oi', 'kangaero'],
  'naa': ['naa', 'choushi', 'nyaa', 'sateto'],
  'dousuru': ['dousuru', 'sateto', 'fumu'],
  'oh_asking': ['oh_asking', 'naruhodo', 'anosaa'],
  'uwooh': ['uwooh', 'oicho', 'maji'],
  'yareyare': ['yareyare', 'sigh'],
  // High-level contextual pools
  'sleep_on': ['sleepy_nyaa', 'yawn'],
  'task_done': ['icchou', 'dekita', 'iizo', 'yoshi', 'gokurou'],
  'focus_start': ['ikuzo', 'shigoto', 'sateto'],
  'stat_log': ['iizo', 'kensan', 'yoshi', 'maana'],
  'rank_up': ['sugoi', 'yarujanee', 'wahaha', 'oh_excited'],
  'wink_happy': ['nyahaha', 'unun', 'nyaa', 'hmm'],
  'smug_win': ['asameshi', 'maana', 'iizo', 'hmm'],
  'worried_cue': ['taihen', 'anosaa', 'oh_asking'],
  'error_cue': ['acchaa', 'oh_asking'],
};

const Map<String, String> kUiSfxAssets = {
  'select': 'audio/ui open window.mp3',
  'open_radial': 'audio/ui open radial menu.mp3',
  'close': 'audio/ui close.mp3',
  'cancel': 'audio/ui close.mp3',
  'hover': 'audio/ui hover.mp3',
  'stat_point': 'audio/social stats add point animation.wav',
  'rank_up': 'audio/social stats level up animation.wav',
};

const Map<String, String> _exprAliases = {
  'happy': 'wink',
  'smile': 'wink',
  'cheerful': 'wink',
  'joy': 'excited',
  'proud': 'smug',
  'confident': 'smug',
  'determined': 'serious',
  'focused': 'serious',
  'concerned': 'worried',
  'anxious': 'worried',
  'nervous': 'uneasy',
  'confused': 'uneasy',
  'awkward': 'uneasy',
  'shocked': 'shout',
  'surprised': 'shout',
  'annoyed': 'angry',
  'mad': 'angry',
  'tired': 'sad',
  'exhausted': 'sad',
  'sleepy': 'neutral',
};

const Set<String> kPositiveExcitedVoices = {
  'oh_excited', 'sugoi', 'yarujanee', 'wahaha', 'yoshi', 'icchou', 'dekita',
  'rank_up', 'task_done',
};
const Set<String> kPositiveUpbeatVoices = {
  'hmm', 'iizo', 'maana', 'asameshi', 'gokurou', 'ikuzo', 'shigoto',
  'kensan', 'kaketa', 'kettei', 'kaigi_start', 'kaigi_end', 'ohayou',
  'choushi', 'nyahaha', 'nyaa', 'unun', 'sateto', 'focus_start', 'stat_log',
  'wink_happy', 'smug_win',
};
const Set<String> kUrgentAngryVoices = {'hoi', 'oi', 'kangaero', 'chigau'};
const Set<String> kShockVoices = {'uwooh', 'oicho', 'maji'};
const Set<String> kSadTiredVoices = {'yareyare', 'sigh', 'yawn'};
const Set<String> kSleepCozyVoices = {'sleepy_nyaa', 'sleep_on'};
const Set<String> kUneasyWorriedVoices = {'acchaa', 'taihen', 'anosaa', 'error_cue', 'worried_cue'};

// Order matters: positive/celebratory cues win over worried/sad so upbeat lines
// mentioning rest/sleep/tasks never display the sad/sweating sprite.
const List<MapEntry<String, List<String>>> _emotionRules = [
  MapEntry('shout', ['hurry', 'due now:', 'starting now:', 'go to bed!', 'lights out', "time's up", 'uwooh']),
  MapEntry('angry', ["shouldn't be staying up", 'not letting this slide', 'past bedtime', 'over budget', 'not a cat', 'bukan kucing']),
  MapEntry('excited', [
    'rank up', 'leveled up', 'level up', 'ooh', 'whoa', 'paying off', 'awesome', 'amazing',
    'great effort', 'great work', 'great job', 'completed', 'crushed it', 'on fire',
    'looking cool', 'mantap', 'keren', 'hebat', 'semangat', 'berhasil', 'sukses',
  ]),
  MapEntry('uneasy', ['are you sure', "cats can't", "couldn't", 'expired', 'failed', 'error', 'invalid']),
  MapEntry('smug', [
    'focus session', 'focus sprint', 'lock in', 'locked in', 'momentum', 'steady progress',
    'timer set', 'logged +', 'recorded', 'piece of cake', 'sharp', 'streak', 'scheduled', 'saved',
  ]),
  MapEntry('wink', [
    'good morning', 'welcome back', 'take it easy', 'enjoy your rest', 'plenty of time', 'synced',
    "i'm awake", 'wide awake', 'sleep mode is off', 'good night', 'sweet dreams', 'guilt-free',
    'breather', 'recharged', 'well-rested', 'jotted that down',
  ]),
  MapEntry('worried', ['overdue', "don't forget", 'deadline', 'upcoming', 'running low', 'careful']),
  MapEntry('sad', [
    "aren't you tired", 'must be tired', 'worn out', 'good grief', 'yawn...',
    'rough day', 'feeling down', 'capek', 'lelah', 'ngantuk', 'sedih',
  ]),
  MapEntry('serious', ["shouldn't you", 'study', 'money', 'budget', 'plan']),
];

const List<MapEntry<String, List<String>>> _voiceRules = [
  MapEntry('chigau', ['not a cat', 'bukan kucing', 'i am not a cat']),
  MapEntry('uwooh', ['uwooh', 'almost morning']),
  MapEntry('hoi', ["time's up", 'due now:', 'starting now:', 'hurry', 'go to bed!', 'past bedtime']),
  MapEntry('kangaero', ['over budget']),
  MapEntry('ohayou', ['good morning', 'selamat pagi', "i'm awake", 'wide awake', 'sleep mode is off']),
  MapEntry('sleep_on', ['good night', 'sweet dreams', 'sleep mode is on', "assume you're asleep", 'selamat tidur']),
  MapEntry('rank_up', ['rank up', 'leveled up', 'level up']),
  MapEntry('oh_excited', [
    'ooh', 'whoa', 'awesome', 'amazing', 'great effort', 'great work', 'great job',
    'paying off', 'looking cool', 'mantap', 'keren', 'hebat', 'semangat',
  ]),
  MapEntry('kaketa', ['saved quick note', 'jotted that down', 'notebook', 'journal']),
  MapEntry('kensan', ['social stat', 'confidant', 'daily habit']),
  MapEntry('focus_start', ['focus sprint', 'focus session', 'locked in', 'lock in']),
  MapEntry('gokurou', ['breather', 'break started', 'well done', 'earned']),
  MapEntry('task_done', ['completed', 'checked off', 'task done', 'cleared', 'finished']),
  MapEntry('kettei', ['timer set', 'scheduled', 'recorded', 'synced', 'added task']),
  MapEntry('acchaa', ["couldn't", 'expired', 'failed', 'error', 'invalid']),
  MapEntry('yareyare', ["aren't you tired", 'must be tired', 'worn out', 'good grief', 'yawn', 'bedtime']),
  MapEntry('taihen', ['overdue', 'running low']),
  MapEntry('dousuru', ['what should', "what's next", "what's the move", 'plan']),
  MapEntry('oh_asking', ['are you sure', 'checking']),
  MapEntry('hmm', ['logged +', 'momentum', 'steady progress', 'take it easy']),
];

const Map<String, String> _emotionVoiceFallback = {
  'excited': 'oh_excited',
  'shout': 'uwooh',
  'angry': 'hoi',
  'sad': 'yareyare',
  'uneasy': 'acchaa',
  'worried': 'taihen',
  'smug': 'hmm',
  'wink': 'wink_happy',
  'serious': 'dousuru',
  'neutral': 'naa',
};

String normalizeExpression(String? emotion, {String fallback = 'neutral'}) {
  final fb = kMorganaExpressions.containsKey(fallback) ? fallback : 'neutral';
  if (emotion == null || emotion.trim().isEmpty) return fb;
  final low = emotion.trim().toLowerCase();
  if (kMorganaExpressions.containsKey(low)) return low;
  final mapped = _exprAliases[low];
  return (mapped != null && kMorganaExpressions.containsKey(mapped)) ? mapped : fb;
}

String emotionForText(String text, {String fallback = 'neutral'}) {
  final low = text.toLowerCase();
  for (final rule in _emotionRules) {
    if (rule.value.any((k) => low.contains(k))) return rule.key;
  }
  return normalizeExpression(fallback);
}

String voiceForDialogue(String text, {String emotion = 'neutral', String context = '', int? hour}) {
  final low = text.toLowerCase();
  final emo = normalizeExpression(emotion);
  if (context == 'rank_up') return 'rank_up';
  if (context == 'timer_up') return 'hoi';
  if (context == 'sleep_on') return 'sleep_on';
  if (context == 'wake_up') return (hour != null && hour >= 4 && hour < 12) ? 'ohayou' : 'choushi';
  if (context == 'bedtime') return (emo == 'shout' || emo == 'angry') ? 'hoi' : 'yareyare';
  if (context == 'startup') return (hour != null && hour >= 4 && hour < 12) ? 'ohayou' : 'choushi';
  if (context == 'stat_log') return 'stat_log';
  if (context == 'task_done') return 'task_done';
  if (context == 'note_save') return 'kaketa';
  if (context == 'focus_start') return 'focus_start';
  if (context == 'cat_tease') return 'chigau';
  for (final rule in _voiceRules) {
    if (rule.value.any((k) => low.contains(k))) return rule.key;
  }
  if (low.contains('?') && (emo == 'neutral' || emo == 'worried' || emo == 'serious' || emo == 'uneasy')) {
    return 'oh_asking';
  }
  return _emotionVoiceFallback[emo] ?? 'naa';
}

/// Cross-validates `(emotion, voice)` so Morgana's sprite, sentence, and audio NEVER contradict.
({String emotion, String voice}) resolveReaction(
  String text, {
  String? emotion,
  String? voice,
  String fallback = 'neutral',
  String context = '',
  int? hour,
}) {
  final probeText = (context == 'startup' && text.contains('\n')) ? text.split('\n').first : text;
  var emo = emotion != null && emotion.trim().isNotEmpty
      ? normalizeExpression(emotion, fallback: fallback)
      : emotionForText(probeText, fallback: fallback);

  final forcedVoice = voice != null && voice.trim().isNotEmpty;
  var vkey = forcedVoice
      ? voice.trim()
      : voiceForDialogue(probeText, emotion: emo, context: context, hour: hour);

  // 1) Never show a sad/worried/uneasy face when playing an excited or upbeat voice!
  if (kPositiveExcitedVoices.contains(vkey)) {
    if (emo == 'sad' || emo == 'worried' || emo == 'uneasy' || emo == 'neutral') {
      emo = 'excited';
    }
  } else if (kPositiveUpbeatVoices.contains(vkey)) {
    if (emo == 'sad' || emo == 'worried' || emo == 'uneasy') {
      emo = const {'ohayou', 'choushi', 'nyahaha', 'nyaa', 'unun', 'wink_happy', 'gokurou'}.contains(vkey)
          ? 'wink'
          : 'smug';
    }
  } else if (kSleepCozyVoices.contains(vkey)) {
    if (emo == 'sad') emo = 'wink';
  } else if (kUrgentAngryVoices.contains(vkey)) {
    if (emo == 'sad') emo = 'angry';
  } else if (kShockVoices.contains(vkey)) {
    if (emo == 'sad') emo = 'shout';
  }

  // 2) If expression is upbeat/excited and voice was auto-selected, never play a sad/tired clip.
  if (!forcedVoice) {
    if (emo == 'excited' && (kSadTiredVoices.contains(vkey) || kUneasyWorriedVoices.contains(vkey))) {
      vkey = 'oh_excited';
    } else if ((emo == 'wink' || emo == 'smug') &&
        (kSadTiredVoices.contains(vkey) || kUneasyWorriedVoices.contains(vkey))) {
      vkey = emo == 'wink' ? 'wink_happy' : 'smug_win';
    }
  }

  return (emotion: emo, voice: vkey);
}

const List<String> kMorganaTapQuips = [
  "Hey! I am NOT a cat! I'm Morgana—now what's the move?",
  "Looking cool, Joker! Ready to tackle today's tasks or log your budget?",
  "Whoa, steady progress! Tap a radial tab and let's get moving.",
  "Don't forget to track your spending—Phantom Thieves keep sharp books!",
  "Need a Focus sprint? I'll watch the clock while you lock in!",
  "Keep building your Social Stats—even small daily habits add up fast!",
  "Checking your schedule? Open Events so nothing sneaks up on us!",
];

String greetingForHour(int hour, String callUser) {
  final name = callUser.trim().isEmpty ? 'Joker' : callUser.trim();
  if (hour >= 5 && hour < 12) {
    return "Good morning, $name! Ready to make today count?";
  }
  if (hour >= 12 && hour < 17) {
    return "Afternoon, $name! Keep that momentum going—what's next on the plan?";
  }
  if (hour >= 17 && hour < 22) {
    return "Evening, $name! Let's wrap up your tasks and log today's finances.";
  }
  return "Whoa, it's getting late, $name! Shouldn't you get some rest soon?";
}
