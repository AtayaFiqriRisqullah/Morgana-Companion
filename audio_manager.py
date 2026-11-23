# -*- coding: utf-8 -*-
"""Cross-platform Persona 5 audio engine for UI SFX, Social Stats fanfare, and Morgana voicelines.

Loads assets from `assets/audio/` via `app_paths.resource_path()` (PyInstaller-safe).
Uses three non-clashing playback channels (backed by Qt6 FFmpeg):
  1. Preloaded `QMediaPlayer` slots for snappy MP3 UI cues (hover, open radial, open/close window).
  2. Preloaded `QMediaPlayer` slots for WAV Social Stats sequences (auto-resampled across devices).
  3. Dedicated Morgana voice `QMediaPlayer` channel so voicelines never talk over each other.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, QUrl

import app_paths

log = logging.getLogger("pet.audio")

AUDIO_DIR = app_paths.resource_path("assets/audio")

UI_SOUNDS: dict[str, str] = {
    "hover": "ui hover.mp3",
    "open_radial": "ui open radial menu.mp3",
    "open_window": "ui open window.mp3",
    "close": "ui close.mp3",
}

STATS_SOUNDS: dict[str, str] = {
    "before_1": "social stats add before animation.wav",      # 0.98s (1-2 pts chime)
    "before_2": "social stats add before animation 2.wav",    # 1.48s (3+ pts chime)
    "add_point": "social stats add point animation.wav",      # 1.36s (pentagon swell)
    "level_up": "social stats level up animation.wav",        # 2.45s (rank-up fanfare)
}

VOICE_SOUNDS: dict[str, str] = {
    "dousuru": "dousuru morgana.mp3",
    "hmm": "hmm approve morgana.mp3",
    "hoi": "hoi morgana .mp3",
    "naa": "naa naa morgana.mp3",
    "oh_asking": "oh asking morgana.mp3",
    "oh_excited": "oh excited morgana.mp3",
    "uwooh": "uwooh morgana.mp3",
    "yareyare": "yareyare morgana .mp3",
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
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    _MULTIMEDIA_OK = True
except Exception as exc:  # pragma: no cover - optional runtime safeguard
    log.warning("QtMultimedia unavailable, audio disabled: %s", exc)
    QAudioOutput = QMediaPlayer = None
    _MULTIMEDIA_OK = False


def resolve_audio(filename: str) -> Path | None:
    path = AUDIO_DIR / filename
    return path if path.is_file() else None


class AudioManager(QObject):
    """Manages UI SFX, P5R Social Stats audio sequences, and Morgana's Japanese voicelines."""

    def __init__(self, parent=None, enabled: bool = True, volume_pct: int = 80) -> None:
        super().__init__(parent)
        self._enabled = bool(enabled)
        self._volume = max(0.0, min(1.0, volume_pct / 100.0))
        self._ready = False
        self._ui_players: dict[str, tuple[object, object]] = {}
        self._stats_players: dict[str, tuple[object, object]] = {}
        self._voice_urls: dict[str, QUrl] = {}
        self._voice_player = None
        self._voice_out = None
        self._last_hover_t = 0.0
        self._last_voice_t = 0.0
        self._seq_timers: list[QTimer] = []
        self._init_channels()

    def _init_channels(self) -> None:
        if not _MULTIMEDIA_OK or not AUDIO_DIR.is_dir():
            return
        try:
            # 1) Preload MP3 UI cues (one player per cue for snappy non-clashing playback)
            for key, fname in UI_SOUNDS.items():
                path = resolve_audio(fname)
                if path is None:
                    continue
                out = QAudioOutput(self)
                out.setVolume(self._ui_vol(key))
                player = QMediaPlayer(self)
                player.setAudioOutput(out)
                player.setSource(QUrl.fromLocalFile(str(path)))
                self._ui_players[key] = (player, out)

            # 2) Preload WAV Social Stats cues via QMediaPlayer so Qt6 FFmpeg resamples
            #    44.1 kHz mono & 48 kHz stereo cleanly on any Windows/Linux audio device.
            for key, fname in STATS_SOUNDS.items():
                path = resolve_audio(fname)
                if path is None:
                    continue
                out = QAudioOutput(self)
                out.setVolume(self._volume)
                player = QMediaPlayer(self)
                player.setAudioOutput(out)
                player.setSource(QUrl.fromLocalFile(str(path)))
                self._stats_players[key] = (player, out)

            # 3) Dedicated Morgana voice channel
            for key, fname in VOICE_SOUNDS.items():
                path = resolve_audio(fname)
                if path is not None:
                    self._voice_urls[key] = QUrl.fromLocalFile(str(path))
            if self._voice_urls:
                self._voice_out = QAudioOutput(self)
                self._voice_out.setVolume(self._volume)
                self._voice_player = QMediaPlayer(self)
                self._voice_player.setAudioOutput(self._voice_out)

            self._ready = True
        except Exception as exc:
            log.warning("Failed to initialize audio channels: %s", exc)
            self._ready = False

    def _ui_vol(self, key: str) -> float:
        return self._volume * (0.72 if key == "hover" else 0.90)

    # -- settings -------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def volume_pct(self) -> int:
        return int(round(self._volume * 100))

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        if not self._enabled:
            self.stop_all()

    def set_volume(self, volume_pct: int) -> None:
        self._volume = max(0.0, min(1.0, int(volume_pct) / 100.0))
        for key, (_pl, out) in self._ui_players.items():
            out.setVolume(self._ui_vol(key))
        for _pl, out in self._stats_players.values():
            out.setVolume(self._volume)
        if self._voice_out is not None:
            self._voice_out.setVolume(self._volume)

    def stop_all(self) -> None:
        self._cancel_sequences()
        for pl, _out in self._ui_players.values():
            pl.stop()
        for pl, _out in self._stats_players.values():
            pl.stop()
        if self._voice_player is not None:
            self._voice_player.stop()

    # -- playback -------------------------------------------------------------
    def play_ui(self, key: str) -> None:
        if not (self._enabled and self._ready and self._volume > 0.001):
            return
        slot = self._ui_players.get(key)
        if slot is None:
            return
        now = time.monotonic()
        if key == "hover":
            if now - self._last_hover_t < 0.045:
                return
            self._last_hover_t = now
        player, _out = slot
        try:
            player.stop()
            player.setPosition(0)
            player.play()
        except Exception as exc:
            log.debug("UI sound %s failed: %s", key, exc)

    def play_voice(self, key: str) -> None:
        if not (self._enabled and self._ready and self._volume > 0.001):
            return
        if self._voice_player is None or key not in self._voice_urls:
            return
        now = time.monotonic()
        if now - self._last_voice_t < 0.12:
            return
        self._last_voice_t = now
        url = self._voice_urls[key]
        try:
            self._voice_player.stop()
            if self._voice_player.source() != url:
                self._voice_player.setSource(url)
            else:
                self._voice_player.setPosition(0)
            self._voice_player.play()
        except Exception as exc:
            log.debug("Voice %s failed: %s", key, exc)

    def play_stat(self, key: str) -> None:
        if not (self._enabled and self._ready and self._volume > 0.001):
            return
        slot = self._stats_players.get(key)
        if slot is None:
            return
        player, _out = slot
        try:
            player.stop()
            player.setPosition(0)
            player.play()
        except Exception as exc:
            log.debug("Stats sound %s failed: %s", key, exc)

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
        if not (self._enabled and self._ready and self._volume > 0.001):
            if on_point_anim:
                on_point_anim()
            if leveled_up and on_level_up:
                on_level_up()
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


def init_audio(parent=None, enabled: bool = True, volume_pct: int = 80) -> AudioManager:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = AudioManager(parent=parent, enabled=enabled, volume_pct=volume_pct)
    else:
        _INSTANCE.set_enabled(enabled)
        _INSTANCE.set_volume(volume_pct)
    return _INSTANCE


def audio() -> AudioManager | None:
    return _INSTANCE


def play_ui(key: str) -> None:
    if _INSTANCE is not None:
        _INSTANCE.play_ui(key)


def play_voice(key: str) -> None:
    if _INSTANCE is not None:
        _INSTANCE.play_voice(key)
