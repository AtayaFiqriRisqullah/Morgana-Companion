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
  'dousuru': 'audio/dousuru morgana.mp3',
  'hmm': 'audio/hmm approve morgana.mp3',
  'hoi': 'audio/hoi morgana .mp3',
  'naa': 'audio/naa naa morgana.mp3',
  'oh_asking': 'audio/oh asking morgana.mp3',
  'oh_excited': 'audio/oh excited morgana.mp3',
  'uwooh': 'audio/uwooh morgana.mp3',
  'yareyare': 'audio/yareyare morgana .mp3',
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

const List<MapEntry<String, List<String>>> _emotionRules = [
  MapEntry('shout', ['hurry', 'due now:', 'starting now:', 'go to bed!', 'lights out', "time's up", 'uwooh']),
  MapEntry('angry', ["shouldn't be staying up", 'not letting this slide', 'past bedtime', 'over budget']),
  MapEntry('uneasy', ['are you sure', "cats can't", "couldn't", 'expired', 'failed', 'error']),
  MapEntry('excited', ['ooh', 'whoa', 'paying off', 'awesome', 'great effort', 'rank up', 'level up', 'completed']),
  MapEntry('smug', ['focus session', 'lock in', 'momentum', 'timer set', 'logged +', 'recorded']),
  MapEntry('wink', ['good morning', 'take it easy', 'enjoy your rest', 'plenty of time', 'synced']),
  MapEntry('worried', ["don't forget", 'deadline', 'upcoming', 'next up:', 'pending task']),
  MapEntry('sad', ['tired', 'sleep', 'rest up', 'bedtime', 'yawn']),
  MapEntry('serious', ["shouldn't you", 'study', 'money', 'budget', 'plan']),
];

const List<MapEntry<String, List<String>>> _voiceRules = [
  MapEntry('hoi', ["time's up", 'due now:', 'starting now:', 'hurry', 'go to bed!', 'past bedtime', 'over budget']),
  MapEntry('uwooh', ['uwooh', 'whoa!', 'almost morning']),
  MapEntry('oh_excited', ['rank up', 'level up', 'ooh', 'whoa', 'awesome', 'great effort', 'completed']),
  MapEntry('yareyare', ['tired', 'sleep', 'rest up', 'bedtime', 'good grief']),
  MapEntry('dousuru', ['what should', "what's next", "what's the move", 'plan']),
  MapEntry('oh_asking', ['are you sure', 'checking', 'failed', 'error']),
  MapEntry('hmm', ['logged +', 'timer set', 'focus session', 'recorded', 'synced', 'good work']),
];

const Map<String, String> _emotionVoiceFallback = {
  'excited': 'oh_excited',
  'shout': 'uwooh',
  'angry': 'hoi',
  'sad': 'yareyare',
  'uneasy': 'oh_asking',
  'worried': 'naa',
  'smug': 'hmm',
  'wink': 'hmm',
  'serious': 'dousuru',
  'neutral': 'naa',
};

String emotionForText(String text, {String fallback = 'neutral'}) {
  final low = text.toLowerCase();
  for (final rule in _emotionRules) {
    if (rule.value.any((k) => low.contains(k))) return rule.key;
  }
  return kMorganaExpressions.containsKey(fallback) ? fallback : 'neutral';
}

String voiceForDialogue(String text, {String emotion = 'neutral', String context = ''}) {
  final low = text.toLowerCase();
  if (context == 'rank_up') return 'oh_excited';
  if (context == 'timer_up') return 'hoi';
  if (context == 'bedtime') return (emotion == 'shout' || emotion == 'angry') ? 'hoi' : 'yareyare';
  if (context == 'startup') return 'naa';
  if (context == 'stat_log') return 'hmm';
  for (final rule in _voiceRules) {
    if (rule.value.any((k) => low.contains(k))) return rule.key;
  }
  if (low.contains('?')) return 'oh_asking';
  return _emotionVoiceFallback[emotion] ?? 'naa';
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
