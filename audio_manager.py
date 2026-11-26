# -*- coding: utf-8 -*-
"""Low-latency cross-platform Persona 5 audio engine (RAM-PCM QAudioSink + QAudioDecoder).

Loads assets from `assets/audio/` via `app_paths.resource_path()` (PyInstaller-safe).

Why RAM-PCM + warm `QAudioSink` instead of `QMediaPlayer`:
  * `QMediaPlayer` (Qt6 FFmpeg backend) tears down and re-opens its FFmpeg demuxer
    and OS audio thread on `stop()`/`setSource()`, causing 250-500 ms startup pauses.
  * Here, all 16 short clips (~2.2 MB raw PCM total) are decoded asynchronously ONCE
    at startup into the default audio device's native `QAudioFormat` via `QAudioDecoder`,
    then streamed directly from RAM via low-buffer (35 ms) push-mode `QAudioSink`s
    that stay warm for 3 seconds after activity (0% CPU when idle, ~15-25 ms response).
"""
from __future__ import annotations

import logging
import os
import random
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, QUrl

import app_paths

log = logging.getLogger("pet.audio")

AUDIO_DIR = app_paths.resource_path("assets/audio")

UI_SOUNDS: dict[str, str] = {
    "hover": "ui hover.mp3",
    "ui_hover": "ui hover.mp3",
    "open_radial": "ui open radial menu.mp3",
    "open_window": "ui open window.mp3",
    "select": "ui open window.mp3",
    "close": "ui close.mp3",
    "cancel": "ui close.mp3",
}

STATS_SOUNDS: dict[str, str] = {
    "before_1": "social stats add before animation.wav",      # 0.98s (1-2 pts chime)
    "before_2": "social stats add before animation 2.wav",    # 1.48s (3+ pts chime)
    "add_point": "social stats add point animation.wav",      # 1.36s (pentagon swell)
    "level_up": "social stats level up animation.wav",        # 2.45s (rank-up fanfare)
}

VOICE_SOUNDS: dict[str, str] = {
    # Original 8 Japanese Morgana clips
    "dousuru": "dousuru morgana.mp3",
    "hmm": "hmm approve morgana.mp3",
    "hoi": "hoi morgana .mp3",
    "naa": "naa naa morgana.mp3",
    "oh_asking": "oh asking morgana.mp3",
    "oh_excited": "oh excited morgana.mp3",
    "uwooh": "uwooh morgana.mp3",
    "yareyare": "yareyare morgana .mp3",
    # Curated 36 Persona 5 Royal Japanese Morgana reaction clips
    "ohayou": "morgana_ohayou.wav",           # "Ohayou~" (Good morning!)
    "choushi": "morgana_choushi.wav",         # "Choushi wa dou da?" (How's it going?)
    "taihen": "morgana_taihen.wav",           # "Taihen sou da naa" (Looks rough/busy)
    "oi": "morgana_oi.wav",                   # "Oi!" (Stern hey!)
    "nyaa": "morgana_nyaa.wav",               # "Nyaa~!" (Playful meow)
    "maana": "morgana_maana.wav",             # "Ma, maa na!" (Smug confidence)
    "sigh": "morgana_sigh.wav",               # "Haaa..." (Tired sigh)
    "oicho": "morgana_oicho.wav",             # "Oi cho—!" (Startled/panicked)
    "gokurou": "morgana_gokurou.wav",         # "N? Gokurou!" (Good work!)
    "chigau": "morgana_chigau.wav",           # "Chigautsuu no!" (Not a cat / wrong!)
    "maji": "morgana_maji.wav",               # "Maji ka yo?!" (Are you serious?!)
    "sleepy_nyaa": "morgana_sleepy_nyaa.wav", # "Nyaa~..." (Cozy sleepy purr/meow)
    "iizo": "morgana_iizo.wav",               # "Ii zo!" (Nice / Good going!)
    "wahaha": "morgana_wahaha.wav",           # "Wa-ha-ha!" (Joyful laugh)
    "ikuzo": "morgana_ikuzo.wav",             # "Ikuzo!" (Let's go!)
    "shigoto": "morgana_shigoto.wav",         # "Shigoto no jikan da ze!" (Work time!)
    "nyahaha": "morgana_nyahaha.wav",         # "Nyahaha! Fufu!" (Cheeky happy giggle)
    "kensan": "morgana_kensan.wav",           # "Jiko kensan ni hagendoke!" (Self-improvement!)
    "icchou": "morgana_icchou.wav",           # "Icchou agari!" (All done!)
    "yarujanee": "morgana_yarujanee.wav",     # "Yaru ja nee ka!" (Awesome / Not bad!)
    "yawn": "morgana_yawn.wav",               # "Uuuun..." (Sleepy yawn/stretch)
    "asameshi": "morgana_asameshi.wav",       # "Asameshi mae da ze!" (Piece of cake!)
    "kaigi_start": "morgana_kaigi_start.wav", # "Dewa, sakusen kaigi wo hajimeyou ka!"
    "kaigi_end": "morgana_kaigi_end.wav",     # "Yoshi, kaigi oshimai!"
    "naruhodo": "morgana_naruhodo.wav",       # "Naruhodo!" (I see!)
    "kangaero": "morgana_kangaero.wav",       # "Oi oi, yoku kangaero!" (Think carefully!)
    "dekita": "morgana_dekita.wav",           # "Kou! Dekita ze!" (There! All done!)
    "kaketa": "morgana_kaketa.wav",           # "Umaku kaketa zo!" (Wrote it nicely!)
    "kettei": "morgana_kettei.wav",           # "Jaa, kettei da na!" (Then it's decided!)
    "sugoi": "morgana_sugoi.wav",             # "Sugoi naa!" (Amazing!)
    "fumu": "morgana_fumu.wav",               # "Fumu fumu." (Hmm hmm)
    "unun": "morgana_unun.wav",               # "Un, un!" (Warm uh-huh!)
    "sateto": "morgana_sateto.wav",           # "Sate to..." (Now then...)
    "yoshi": "morgana_yoshi.wav",             # "Yoshi!" (Crisp alright!)
    "acchaa": "morgana_acchaa.wav",           # "Acchaa..." (Oops / Oh no...)
    "anosaa": "morgana_anosaa.wav",           # "Ano saa..." (Hey, um...)
}

# Semantic anti-repeat rotation pools so repeated generic voice keys sound lively and varied
# while ALWAYS preserving the exact mood/valence of the voice key.
VOICE_POOLS: dict[str, tuple[str, ...]] = {
    "oh_excited": ("oh_excited", "sugoi", "yarujanee", "wahaha", "yoshi"),
    "hmm": ("hmm", "unun", "iizo", "maana", "fumu"),
    "hoi": ("hoi", "oi", "kangaero"),
    "naa": ("naa", "choushi", "nyaa", "sateto"),
    "dousuru": ("dousuru", "sateto", "fumu"),
    "oh_asking": ("oh_asking", "naruhodo", "anosaa"),
    "uwooh": ("uwooh", "oicho", "maji"),
    "yareyare": ("yareyare", "sigh"),
    # High-level contextual pools
    "sleep_on": ("sleepy_nyaa", "yawn"),
    "task_done": ("icchou", "dekita", "iizo", "yoshi", "gokurou"),
    "focus_start": ("ikuzo", "shigoto", "sateto"),
    "stat_log": ("iizo", "kensan", "yoshi", "maana"),
    "rank_up": ("sugoi", "yarujanee", "wahaha", "oh_excited"),
    "wink_happy": ("nyahaha", "unun", "nyaa", "hmm"),
    "smug_win": ("asameshi", "maana", "iizo", "hmm"),
    "worried_cue": ("taihen", "anosaa", "oh_asking"),
    "error_cue": ("acchaa", "oh_asking"),
}


def _configure_linux_audio_backend() -> None:
    """Prevent Qt6 PipeWire segfault on Linux environments without PipeWire client.conf."""
    if not sys.platform.startswith("linux") or "QT_AUDIO_BACKEND" in os.environ:
        return
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    has_pw_conf = (Path("/usr/share/pipewire/client.conf").is_file()
                   or Path("/etc/pipewire/client.conf").is_file())
    has_pw_sock = (runtime / "pipewire-0").exists()
    if has_pw_conf and has_pw_sock:
        return
    if (runtime / "pulse" / "native").exists():
        os.environ["QT_AUDIO_BACKEND"] = "pulseaudio"
    else:
        os.environ["QT_AUDIO_BACKEND"] = "alsa"


_configure_linux_audio_backend()

try:
    from PyQt6.QtMultimedia import (QAudio, QAudioDecoder, QAudioFormat, QAudioSink,
                                    QMediaDevices)
    _MULTIMEDIA_OK = True
except Exception as exc:  # pragma: no cover - optional runtime safeguard
    log.warning("QtMultimedia unavailable, audio disabled: %s", exc)
    QAudio = QAudioDecoder = QAudioFormat = QAudioSink = QMediaDevices = None
    _MULTIMEDIA_OK = False


def resolve_audio(filename: str) -> Path | None:
    path = AUDIO_DIR / filename
    return path if path.is_file() else None


def _default_fallback_format():
    fmt = QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    return fmt


def _pick_output_format(device):
    """Choose a device-native `QAudioFormat` guaranteed supported by `device`."""
    if device is None or device.isNull():
        return _default_fallback_format()
    pref = device.preferredFormat()
    if not pref.isValid() or pref.sampleFormat() == QAudioFormat.SampleFormat.Unknown:
        fallback = _default_fallback_format()
        return fallback if device.isFormatSupported(fallback) else pref
    # Prefer stereo when device defaults to multi-channel surround (5.1/7.1) if supported
    if pref.channelCount() > 2:
        stereo = QAudioFormat(pref)
        stereo.setChannelCount(2)
        if device.isFormatSupported(stereo):
            return stereo
    return pref


class _WarmPcmChannel(QObject):
    """Push-mode RAM PCM channel using a low-buffer `QAudioSink` that stays warm briefly.

    All `QIODevice.write()` calls occur on the Qt main thread (100% PyQt6 GIL-safe,
    zero C++ worker-thread Python callbacks) and drop to 0% CPU after 3s of silence.
    """

    BUFFER_US = 35_000          # 35 ms hardware ring buffer -> snappy low-latency playback
    PUMP_MS = 12                # feed every 12 ms while active/warm
    WARM_KEEPALIVE_S = 3.0      # keep sink warm 3s after last clip so rapid UI SFX is instant

    def __init__(self, device, fmt, parent=None) -> None:
        super().__init__(parent)
        self._device = device
        self._fmt = fmt
        self._frame_bytes = max(1, fmt.bytesPerFrame())
        self._silence_byte = (b"\x80" if fmt.sampleFormat() == QAudioFormat.SampleFormat.UInt8
                              else b"\x00")
        self._sink = None
        self._io = None
        self._pcm: bytes = b""
        self._pos: int = 0
        self._idle_since: float | None = None
        self._last_play_t: float = 0.0
        self._gain_mult: float = 1.0
        self._master_vol: float = 0.8

        self._pump_timer = QTimer(self)
        self._pump_timer.setInterval(self.PUMP_MS)
        self._pump_timer.timeout.connect(self._pump)

        if device is not None and not device.isNull():
            try:
                sink = QAudioSink(device, fmt, self)
                buf_sz = max(self._frame_bytes * 64, fmt.bytesForDuration(self.BUFFER_US))
                buf_sz -= buf_sz % self._frame_bytes
                sink.setBufferSize(buf_sz)
                self._sink = sink
            except Exception as exc:
                log.debug("Failed to create QAudioSink: %s", exc)
                self._sink = None

    @property
    def is_playing_audio(self) -> bool:
        return self._io is not None and self._pos < len(self._pcm)

    @property
    def is_warm_idle(self) -> bool:
        return self._io is not None and self._pos >= len(self._pcm)

    @property
    def is_stopped(self) -> bool:
        return self._io is None

    @property
    def last_play_t(self) -> float:
        return self._last_play_t

    def apply_master_volume(self, master: float) -> None:
        self._master_vol = max(0.0, min(1.0, master))
        if self._sink is not None:
            self._sink.setVolume(max(0.0, min(1.0, self._master_vol * self._gain_mult)))

    def play(self, pcm: bytes, master_vol: float, gain_mult: float = 1.0) -> None:
        if not pcm:
            return
        self._pcm = pcm
        self._pos = 0
        self._idle_since = None
        self._last_play_t = time.monotonic()
        self._gain_mult = gain_mult
        self._master_vol = master_vol
        if self._sink is None:
            return
        try:
            self._sink.setVolume(max(0.0, min(1.0, master_vol * gain_mult)))
            if self._io is None or self._sink.state() == QAudio.State.StoppedState:
                self._io = self._sink.start()
            self._pump()
            if not self._pump_timer.isActive():
                self._pump_timer.start()
        except Exception as exc:
            log.debug("QAudioSink play failed: %s", exc)

    def stop(self) -> None:
        self._pump_timer.stop()
        self._pcm = b""
        self._pos = 0
        self._idle_since = None
        self._io = None
        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception:
                pass

    def _pump(self) -> None:
        if self._sink is None or self._io is None:
            self._pump_timer.stop()
            return
        free = self._sink.bytesFree()
        free -= free % self._frame_bytes
        if free <= 0:
            return

        total = len(self._pcm)
        if self._pos < total:
            end = min(total, self._pos + free)
            chunk = self._pcm[self._pos:end]
            written = self._io.write(chunk)
            if written > 0:
                self._pos += written
            # Pad any remaining free space once the clip finishes so the tail plays cleanly
            rem_free = free - max(0, written)
            rem_free -= rem_free % self._frame_bytes
            if self._pos >= total and rem_free > 0:
                self._io.write(self._silence_byte * rem_free)
            if self._pos >= total:
                self._idle_since = time.monotonic()
        else:
            now = time.monotonic()
            if self._idle_since is None:
                self._idle_since = now
            if now - self._idle_since >= self.WARM_KEEPALIVE_S:
                self.stop()
                return
            self._io.write(self._silence_byte * free)


class AudioManager(QObject):
    """Manages UI SFX, P5R Social Stats audio sequences, and Morgana's Japanese voicelines."""

    _UI_VOL_MULT: dict[str, float] = {
        "hover": 0.74,
        "ui_hover": 0.48,
        "select": 0.68,
        "cancel": 0.68,
        "open_radial": 0.90,
        "open_window": 0.88,
        "close": 0.88,
    }
    # Micro-debounce ONLY on hover to prevent 1px cursor-jitter buzz; clicks & voices NEVER drop!
    _UI_COOLDOWN: dict[str, float] = {
        "hover": 0.025,
        "ui_hover": 0.025,
    }

    def __init__(self, parent=None, enabled: bool = True, volume_pct: int = 80,
                 voice_volume_pct: int | None = None, sfx_volume_pct: int | None = None) -> None:
        super().__init__(parent)
        self._enabled = bool(enabled)
        base_pct = max(0, min(100, int(volume_pct)))
        v_pct = base_pct if voice_volume_pct is None else max(0, min(100, int(voice_volume_pct)))
        s_pct = base_pct if sfx_volume_pct is None else max(0, min(100, int(sfx_volume_pct)))
        self._voice_volume = v_pct / 100.0
        self._sfx_volume = s_pct / 100.0
        self._volume = max(self._voice_volume, self._sfx_volume)
        self._ready = False
        self._device = None
        self._fmt = None
        self._devices_watcher = None

        self._pcm: dict[str, bytes] = {}            # filename -> decoded native PCM in RAM
        self._decoders: list[object] = []
        self._pending_by_file: dict[str, tuple[str, str]] = {}  # fname -> (channel_kind, key)
        self._ui_pool: list[_WarmPcmChannel] = []
        self._stats_pool: list[_WarmPcmChannel] = []
        self._voice_ch: _WarmPcmChannel | None = None
        self._last_ui_t: dict[str, float] = {}
        self._last_voice_by_pool: dict[str, str] = {}
        self._seq_timers: list[QTimer] = []
        self._init_engine()

    def _init_engine(self) -> None:
        if not _MULTIMEDIA_OK or not AUDIO_DIR.is_dir():
            return
        try:
            self._devices_watcher = QMediaDevices(self)
            self._devices_watcher.audioOutputsChanged.connect(self._on_devices_changed)
            self._device = QMediaDevices.defaultAudioOutput()
            self._fmt = _pick_output_format(self._device)
            self._rebuild_channels()
            self._decode_all_async()
            self._ready = True
        except Exception as exc:
            log.warning("Failed to initialize audio engine: %s", exc)
            self._ready = False

    def _rebuild_channels(self) -> None:
        for ch in self._all_channels():
            ch.stop()
            ch.deleteLater()
        self._ui_pool = [_WarmPcmChannel(self._device, self._fmt, self) for _ in range(4)]
        self._stats_pool = [_WarmPcmChannel(self._device, self._fmt, self) for _ in range(2)]
        self._voice_ch = _WarmPcmChannel(self._device, self._fmt, self)
        for ch in self._ui_pool + self._stats_pool:
            ch.apply_master_volume(self._sfx_volume)
        if self._voice_ch is not None:
            self._voice_ch.apply_master_volume(self._voice_volume)

    def _all_channels(self) -> list[_WarmPcmChannel]:
        chs = list(self._ui_pool) + list(self._stats_pool)
        if self._voice_ch is not None:
            chs.append(self._voice_ch)
        return chs

    def _on_devices_changed(self) -> None:
        """Handle headset/speaker plug-in cleanly without restarting."""
        if not _MULTIMEDIA_OK:
            return
        try:
            new_dev = QMediaDevices.defaultAudioOutput()
            new_fmt = _pick_output_format(new_dev)
            fmt_changed = (self._fmt is None or new_fmt != self._fmt)
            self._device = new_dev
            self._fmt = new_fmt
            self._rebuild_channels()
            if fmt_changed:
                self._pcm.clear()
                self._decode_all_async()
        except Exception as exc:
            log.debug("Audio device refresh failed: %s", exc)

    def _unique_files(self) -> list[str]:
        # Decode UI cues & startup voice first so they're ready fastest
        ordered: list[str] = []
        seen: set[str] = set()
        for mapping in (UI_SOUNDS, VOICE_SOUNDS, STATS_SOUNDS):
            for fname in mapping.values():
                if fname not in seen:
                    seen.add(fname)
                    ordered.append(fname)
        return ordered

    _MAX_CONCURRENT_DECODERS = 3

    def _decode_all_async(self) -> None:
        """Decode all audio files into RAM via a lean 2-slot queue (prevents 48-thread FFmpeg burst)."""
        for old in self._decoders:
            try:
                old.stop()
                old.deleteLater()
            except Exception:
                pass
        self._decoders.clear()
        self._decode_queue: list[str] = list(self._unique_files())
        self._pump_decode_queue()

    def _pump_decode_queue(self) -> None:
        while self._decode_queue and len(self._decoders) < self._MAX_CONCURRENT_DECODERS:
            fname = self._decode_queue.pop(0)
            path = resolve_audio(fname)
            if path is None:
                continue
            dec = QAudioDecoder(self)
            dec.setAudioFormat(self._fmt)
            dec.setSource(QUrl.fromLocalFile(str(path)))
            buf = bytearray()

            def _drain(d=dec, b=buf) -> None:
                while d.bufferAvailable():
                    ab = d.read()
                    if ab.isValid() and ab.byteCount() > 0:
                        b.extend(ab.constData().asstring(ab.byteCount()))

            def _cleanup(d=dec) -> None:
                if d in self._decoders:
                    self._decoders.remove(d)
                try:
                    d.bufferReady.disconnect()
                    d.finished.disconnect()
                    d.error.disconnect()
                except Exception:
                    pass
                d.deleteLater()
                self._pump_decode_queue()

            def _finish(d=dec, b=buf, name=fname) -> None:
                _drain(d, b)
                if b:
                    self._pcm[name] = bytes(b)
                b.clear()
                _cleanup(d)
                pending = self._pending_by_file.pop(name, None)
                if pending is not None and name in self._pcm:
                    kind, key = pending
                    if kind == "ui":
                        self.play_ui(key)
                    elif kind == "voice":
                        self.play_voice(key)
                    elif kind == "stat":
                        self.play_stat(key)

            def _error(err, d=dec, b=buf, name=fname) -> None:
                log.debug("Audio decode error for %s: %s", name, err)
                b.clear()
                _cleanup(d)

            dec.bufferReady.connect(_drain)
            dec.finished.connect(_finish)
            dec.error.connect(_error)
            dec.start()
            self._decoders.append(dec)

    @property
    def decoded_count(self) -> int:
        return len(self._pcm)

    # -- settings -------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def volume_pct(self) -> int:
        return int(round(max(self._voice_volume, self._sfx_volume) * 100))

    @property
    def voice_volume_pct(self) -> int:
        return int(round(self._voice_volume * 100))

    @property
    def sfx_volume_pct(self) -> int:
        return int(round(self._sfx_volume * 100))

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        if not self._enabled:
            self.stop_all()

    def set_volume(self, volume_pct: int) -> None:
        self.set_volumes(volume_pct, volume_pct)

    def set_voice_volume(self, volume_pct: int) -> None:
        self._voice_volume = max(0.0, min(1.0, int(volume_pct) / 100.0))
        self._volume = max(self._voice_volume, self._sfx_volume)
        if self._voice_ch is not None:
            self._voice_ch.apply_master_volume(self._voice_volume)

    def set_sfx_volume(self, volume_pct: int) -> None:
        self._sfx_volume = max(0.0, min(1.0, int(volume_pct) / 100.0))
        self._volume = max(self._voice_volume, self._sfx_volume)
        for ch in self._ui_pool + self._stats_pool:
            ch.apply_master_volume(self._sfx_volume)

    def set_volumes(self, voice_pct: int, sfx_pct: int) -> None:
        self.set_voice_volume(voice_pct)
        self.set_sfx_volume(sfx_pct)

    def stop_all(self) -> None:
        self._cancel_sequences()
        self._pending_by_file.clear()
        for ch in self._all_channels():
            ch.stop()

    # -- playback -------------------------------------------------------------
    def _pick_channel(self, pool: list[_WarmPcmChannel]) -> _WarmPcmChannel | None:
        if not pool:
            return None
        # 1) Prefer an already-warm idle channel (instant ~15ms reuse, zero new OS stream!)
        for ch in pool:
            if ch.is_warm_idle:
                return ch
        # 2) Else pick a stopped channel for clean polyphony
        for ch in pool:
            if ch.is_stopped:
                return ch
        # 3) Else steal the oldest playing channel
        return min(pool, key=lambda c: c.last_play_t)

    def play_ui(self, key: str) -> None:
        if not (self._enabled and self._ready and self._sfx_volume > 0.001):
            return
        fname = UI_SOUNDS.get(key)
        if fname is None:
            return
        cooldown = self._UI_COOLDOWN.get(key, 0.0)
        if cooldown > 0.0:
            now = time.monotonic()
            if now - self._last_ui_t.get(key, 0.0) < cooldown:
                return
            self._last_ui_t[key] = now
        pcm = self._pcm.get(fname)
        if pcm is None:
            self._pending_by_file[fname] = ("ui", key)
            return
        ch = self._pick_channel(self._ui_pool)
        if ch is not None:
            ch.play(pcm, self._sfx_volume, self._UI_VOL_MULT.get(key, 0.88))

    def _resolve_voice_key(self, key: str) -> str | None:
        pool = VOICE_POOLS.get(key)
        if pool:
            valid = [k for k in pool if k in VOICE_SOUNDS]
            if not valid:
                return None
            # Prefer clips already decoded in RAM so voice response stays instant right after startup
            ready = [k for k in valid if VOICE_SOUNDS[k] in self._pcm]
            candidates = ready or valid
            last = self._last_voice_by_pool.get(key)
            fresh = [k for k in candidates if k != last] if len(candidates) > 1 else candidates
            chosen = random.choice(fresh or candidates)
            self._last_voice_by_pool[key] = chosen
            return chosen
        return key if key in VOICE_SOUNDS else None

    def play_voice(self, key: str) -> None:
        if not (self._enabled and self._ready and self._voice_volume > 0.001):
            return
        resolved = self._resolve_voice_key(key)
        if resolved is None or self._voice_ch is None:
            return
        fname = VOICE_SOUNDS.get(resolved)
        if fname is None:
            return
        pcm = self._pcm.get(fname)
        if pcm is None:
            self._pending_by_file[fname] = ("voice", resolved)
            return
        # Dedicated single channel: immediately switches voice in RAM without self-overlap
        self._voice_ch.play(pcm, self._voice_volume, 1.0)

    def play_stat(self, key: str) -> None:
        if not (self._enabled and self._ready and self._sfx_volume > 0.001):
            return
        fname = STATS_SOUNDS.get(key)
        if fname is None:
            return
        pcm = self._pcm.get(fname)
        if pcm is None:
            self._pending_by_file[fname] = ("stat", key)
            return
        ch = self._pick_channel(self._stats_pool)
        if ch is not None:
            ch.play(pcm, self._sfx_volume, 1.0)

    def _cancel_sequences(self) -> None:
        for t in self._seq_timers:
            t.stop()
            t.deleteLater()
        self._seq_timers.clear()

    def _schedule(self, ms: int, fn) -> None:
        t = QTimer(self)
        t.setSingleShot(True)
        t.setInterval(ms)
        t.timeout.connect(fn)
        t.start()
        self._seq_timers.append(t)

    def play_stats_sequence(self, points: int, leveled_up: bool,
                            on_point_anim=None, on_level_up=None) -> None:
        """Persona 5 Royal 3-stage Social Stats sound + animation cadence:
        1) `before_1` (1-2 pts) or `before_2` (3+ pts) chime immediately;
        2) `add_point` swell + `on_point_anim()` right as the chime resolves;
        3) `level_up` fanfare + `on_level_up()` if the stat leveled up.
        """
        self._cancel_sequences()
        if not (self._enabled and self._ready and self._sfx_volume > 0.001):
            # Keep a crisp visual cadence even when muted so the auto-opened Social Stats
            # window finishes popping in before the gold star expands and bursts on Rank Up.
            if on_point_anim:
                self._schedule(240, on_point_anim)
            if leveled_up and on_level_up:
                self._schedule(1050, on_level_up)
            return

        big = int(points) >= 3
        self.play_stat("before_2" if big else "before_1")
        point_delay_ms = 1000 if big else 680

        def _stage_point() -> None:
            self.play_stat("add_point")
            if on_point_anim:
                on_point_anim()

        self._schedule(point_delay_ms, _stage_point)

        if leveled_up:
            def _stage_rank_up() -> None:
                self.play_stat("level_up")
                if on_level_up:
                    on_level_up()

            self._schedule(point_delay_ms + 1100, _stage_rank_up)


# ---------------------------------------------------------------------------
# Process-wide singleton helpers (keeps ui_dialogs.py decoupled from main.py)
# ---------------------------------------------------------------------------
_INSTANCE: AudioManager | None = None


def init_audio(parent=None, enabled: bool = True, volume_pct: int = 80,
               voice_volume_pct: int | None = None,
               sfx_volume_pct: int | None = None) -> AudioManager:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = AudioManager(
            parent=parent,
            enabled=enabled,
            volume_pct=volume_pct,
            voice_volume_pct=voice_volume_pct,
            sfx_volume_pct=sfx_volume_pct,
        )
    else:
        _INSTANCE.set_enabled(enabled)
        v_pct = volume_pct if voice_volume_pct is None else voice_volume_pct
        s_pct = volume_pct if sfx_volume_pct is None else sfx_volume_pct
        _INSTANCE.set_volumes(v_pct, s_pct)
    return _INSTANCE


def audio() -> AudioManager | None:
    return _INSTANCE


def play_ui(key: str) -> None:
    if _INSTANCE is not None:
        _INSTANCE.play_ui(key)


def play_voice(key: str) -> None:
    if _INSTANCE is not None:
        _INSTANCE.play_voice(key)
