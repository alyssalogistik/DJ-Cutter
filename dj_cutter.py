#!/usr/bin/env python3
"""
DJ-Cutter -- musical handover cleanup for a folder of numbered DJ-set MP3s.

For every consecutive pair of tracks it trims the OUTRO of track N and the PLAY
(head) of track N+1 so the handover lands on the bar grid: one kick at the seam,
no beat jump, no clipped attack, no dead air.

Tracks stay individual files. The source folder is opened read-only and is never
written to, renamed, or deleted.

Minimal cleaning only -- sample-accurate cuts plus anti-click micro-fades.
No EQ, no normalization, no noise reduction, no stem separation, no time-stretch.

Usage:
    python dj_cutter.py "<SOURCE FOLDER>" "<OUTPUT FOLDER>"
    python dj_cutter.py "<SOURCE FOLDER>" "<OUTPUT FOLDER>" --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Versi A parameters. Every tunable lives here so a change is one edit.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Params:
    # Grid / phrase
    beats_per_bar: int = 4
    grid_snap_tolerance_ms: float = 10.0

    # Attack protection: how far before a kick's peak the cut may land.
    attack_guard_max_ms: float = 15.0
    attack_floor_ratio: float = 0.10       # envelope fraction counted as "attack begun"
    snap_min_strength: float = 0.25        # transient strength needed to snap to a grid point

    # Anti-click micro-fades (not musical crossfades)
    tail_fade_ms: float = 10.0
    head_fade_ms: float = 3.0
    tail_fade_stub_ms: float = 25.0        # widened fade used to kill a seam kick stub

    # Silence / dead air
    silence_floor_db: float = -48.0        # relative to track peak
    max_tail_dead_air_ms: float = 40.0     # outro may overshoot the grid by this much
    dead_air_limit_ms: float = 25.0        # QC: longest tolerated gap at the seam

    # Double kick ("tuk-tak")
    double_kick_ratio: float = 0.55        # seam IOI below this * beat period = double kick
    beat_jump_ratio: float = 1.60          # seam IOI above this * beat period = hole
    kick_refractory_ms: float = 120.0      # min spacing between detected kicks
    kick_energy_gate: float = 0.35         # low-band energy needed to count as a kick

    # Tempo
    bpm_mismatch_flag_pct: float = 3.0     # flag only; never time-stretch

    # Analysis
    analysis_sr: int = 44100
    min_bitrate_kbps: int = 128
    bpm_search_lo: float = 85.0
    bpm_search_hi: float = 160.0
    period_fit_window_s: float = 120.0   # slab used to fit the tempo


P = Params()

LOW_BAND_HZ = (30.0, 130.0)


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a command, raising with stderr attached so failures are readable."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr[-2000:]}"
        )
    return proc


def require_tools() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        sys.exit(
            f"ERROR: {', '.join(missing)} not found on PATH.\n"
            "Install FFmpeg and reopen the terminal:\n"
            "  winget install Gyan.FFmpeg\n"
            "or download from https://www.gyan.dev/ffmpeg/builds/ and add its bin\\ to PATH."
        )
    try:
        import numpy, soundfile, librosa  # noqa: F401
    except ImportError as exc:
        sys.exit(
            f"ERROR: missing Python package ({exc.name}).\n"
            "Install the dependencies:\n"
            "  python -m pip install numpy scipy soundfile librosa"
        )


# ---------------------------------------------------------------------------
# Track ordering -- by the NUMBER in the filename, never by directory order
# ---------------------------------------------------------------------------


def track_number(path: Path) -> int | None:
    """First run of digits in the file's stem, e.g. '03 Outro Mix' -> 3."""
    m = re.search(r"\d+", path.stem)
    return int(m.group()) if m else None


def ordered_tracks(src: Path) -> list[Path]:
    mp3s = [p for p in src.iterdir() if p.is_file() and p.suffix.lower() == ".mp3"]
    if not mp3s:
        sys.exit(f"ERROR: no .mp3 files found in {src}")

    numbered, unnumbered = [], []
    for p in mp3s:
        n = track_number(p)
        (numbered if n is not None else unnumbered).append((n, p))

    if unnumbered:
        names = ", ".join(sorted(p.name for _, p in unnumbered))
        sys.exit(
            "ERROR: these files carry no number in the filename, so the real "
            f"order cannot be determined: {names}\n"
            "Rename them with a leading number (01, 02, ...) and rerun."
        )

    seen: dict[int, Path] = {}
    for n, p in numbered:
        if n in seen:
            sys.exit(
                f"ERROR: duplicate track number {n:02d}: "
                f"{seen[n].name} and {p.name}. Renumber and rerun."
            )
        seen[n] = p

    order = [seen[n] for n in sorted(seen)]
    nums = sorted(seen)
    gaps = [n for n in range(nums[0], nums[-1] + 1) if n not in seen]
    if gaps:
        print(f"  note: track numbers are not contiguous, missing {gaps} "
              f"-- processing the {len(order)} files that are present, in numeric order.")
    return order


# ---------------------------------------------------------------------------
# Probe / decode / encode
# ---------------------------------------------------------------------------


@dataclass
class SourceInfo:
    path: Path
    sr: int
    channels: int
    bitrate_kbps: int
    duration_s: float


def probe(path: Path) -> SourceInfo:
    proc = run([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels,bit_rate:format=bit_rate,duration",
        "-of", "json", str(path),
    ])
    data = json.loads(proc.stdout)
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}

    sr = int(stream.get("sample_rate") or P.analysis_sr)
    channels = int(stream.get("channels") or 2)
    raw_br = stream.get("bit_rate") or fmt.get("bit_rate") or 0
    bitrate = int(round(int(raw_br) / 1000)) if int(raw_br) else 0
    bitrate = max(bitrate, P.min_bitrate_kbps)
    duration = float(fmt.get("duration") or 0.0)
    return SourceInfo(path, sr, channels, bitrate, duration)


def decode_to_wav(src: Path, dst: Path) -> None:
    """Decode to 32-bit float WAV at native rate/layout. Source is read-only."""
    run(["ffmpeg", "-v", "error", "-y", "-i", str(src),
         "-map", "0:a:0", "-c:a", "pcm_f32le", "-f", "wav", str(dst)])


def encode_mp3(wav: Path, out: Path, info: SourceInfo) -> None:
    """Encode the trimmed audio, carrying tags and cover art over from the source."""
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-i", str(wav),
        "-i", str(info.path),
        "-map", "0:a:0",
        "-map", "1:v:0?",                 # cover art if present
        "-map_metadata", "1",
        "-c:a", "libmp3lame", "-b:a", f"{info.bitrate_kbps}k",
        "-ar", str(info.sr), "-ac", str(info.channels),
        "-c:v", "copy", "-disposition:v:0", "attached_pic",
        "-id3v2_version", "3",
        str(out),
    ]
    try:
        run(cmd)
    except RuntimeError:
        # Retry without cover art -- some files carry an unusable image stream.
        run([
            "ffmpeg", "-v", "error", "-y",
            "-i", str(wav), "-i", str(info.path),
            "-map", "0:a:0", "-map_metadata", "1",
            "-c:a", "libmp3lame", "-b:a", f"{info.bitrate_kbps}k",
            "-ar", str(info.sr), "-ac", str(info.channels),
            "-id3v2_version", "3", str(out),
        ])


# ---------------------------------------------------------------------------
# Analysis
#
# DJ-set tracks are steady 4/4, so instead of trusting a frame-level beat
# tracker we fit one global grid  t_k = phi + k * period  to the low-band onset
# envelope. Evaluating the envelope's Fourier coefficient at each candidate
# period gives both the strength of that periodicity (|Z|) and the grid phase
# (arg Z) in one O(N) pass, and the resulting grid does not drift across the
# track the way a chained median-of-intervals estimate does.
# ---------------------------------------------------------------------------


@dataclass
class Analysis:
    bpm: float
    beat_period: float
    bar_period: float
    downbeats: "object"
    audible_start: float
    audible_end: float
    kick_env: "object"
    kick_hop: int
    sr: int
    mono: "object"
    grid_confidence: float = 0.0
    last_kick: float = 0.0
    kick_strength: float = 0.0


def onset_env(mono, sr: int, hop: int):
    """Kick-band onset flux -- the envelope the grid is fitted to.

    Built from the STFT rather than a mel basis: a mel filterbank capped at the
    kick band leaves most of its filters empty at this n_fft. Broadband flux is
    no good either -- offbeat hats then contribute as much as kicks and pull the
    fitted grid phase half a beat off. Smoothing before and after the difference
    keeps a kick's pitch sweep from registering as several onsets.
    """
    import numpy as np

    band = low_band_energy(mono, sr, hop)
    flux = np.maximum(0.0, np.diff(band, prepend=band[0]))
    win = max(1, int(0.008 * sr / hop))
    return np.convolve(flux, np.ones(win) / win, mode="same")


def low_band_energy(mono, sr: int, hop: int):
    """Smoothed 30-130 Hz magnitude. Energy, not flux: stable under a kick's
    pitch sweep, which is what makes it a reliable downbeat discriminator."""
    import numpy as np
    import librosa

    S = np.abs(librosa.stft(mono, n_fft=2048, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    band = S[(freqs >= LOW_BAND_HZ[0]) & (freqs <= LOW_BAND_HZ[1])].sum(axis=0)
    win = max(1, int(0.010 * sr / hop))
    kernel = np.ones(win) / win
    return np.convolve(band, kernel, mode="same")


def _rectify(env):
    import numpy as np

    return np.maximum(env - env.mean(), 0.0)


def _fourier_at(env_r, times, period: float):
    """Fourier coefficient of the onset envelope at frequency 1/period."""
    import numpy as np

    return np.exp(-2j * np.pi * times / period) @ env_r


def fit_period(env_r, times) -> tuple[float, float]:
    """Best beat period in the DJ tempo range, plus its normalized strength."""
    import numpy as np

    def sweep(bpms):
        periods = 60.0 / np.asarray(bpms, dtype=float)
        best = (-1.0, float(periods[len(periods) // 2]))
        budget = 4_000_000
        step = max(1, budget // max(len(times), 1))
        for i in range(0, len(periods), step):
            chunk = periods[i:i + step]
            Z = np.exp(-2j * np.pi * np.outer(1.0 / chunk, times)) @ env_r
            mag = np.abs(Z)
            j = int(np.argmax(mag))
            if mag[j] > best[0]:
                best = (float(mag[j]), float(chunk[j]))
        return best

    mag, period = sweep(np.arange(P.bpm_search_lo, P.bpm_search_hi + 1e-9, 0.05))
    bpm = 60.0 / period
    mag, period = sweep(np.arange(max(P.bpm_search_lo, bpm - 0.12),
                                  min(P.bpm_search_hi, bpm + 0.12) + 1e-9, 0.002))
    confidence = mag / max(env_r.sum(), 1e-12)
    return period, float(confidence)


def fit_phase(env_r, times, period: float) -> float:
    """Grid offset in [0, period) that lines the grid up with the onsets."""
    import numpy as np

    Z = _fourier_at(env_r, times, period)
    return float((-np.angle(Z) * period / (2.0 * np.pi)) % period)


def _region(env, times, t0: float, t1: float):
    import numpy as np

    m = (times >= t0) & (times <= t1)
    if m.sum() < 8:
        return env, times
    return env[m], times[m]


def _grid(phi: float, period: float, t0: float, t1: float):
    import numpy as np

    k0 = int(np.floor((t0 - phi) / period)) - 1
    k1 = int(np.ceil((t1 - phi) / period)) + 1
    return phi + np.arange(k0, k1 + 1) * period


def _bar_offset(low_energy, sr: int, hop: int, beats) -> int:
    """Which beat-index residue carries the most kick weight = the downbeat.

    Scored over a short window around each beat rather than at a single frame,
    so a frame or two of envelope lag cannot rotate the bar by a whole beat.
    """
    import numpy as np

    beats = np.asarray(beats, dtype=float)
    if len(beats) < P.beats_per_bar * 2:
        return 0
    pre = int(0.020 * sr / hop)
    post = int(0.040 * sr / hop)
    weights = np.empty(len(beats))
    for i, t in enumerate(beats):
        c = int(t * sr / hop)
        lo, hi = max(0, c - pre), min(len(low_energy), c + post + 1)
        weights[i] = float(low_energy[lo:hi].max()) if hi > lo else 0.0
    return int(np.argmax([float(weights[r::P.beats_per_bar].mean())
                          for r in range(P.beats_per_bar)]))


def _last_transient(env, sr: int, hop: int, start: float, end: float) -> float:
    """Time of the last real onset -- where the track stops playing, as opposed
    to where it stops being audible (a decay tail carries no beat)."""
    import numpy as np

    lo = max(0, int(start * sr / hop))
    hi = min(len(env), int(end * sr / hop) + 1)
    if hi <= lo:
        return end
    seg = env[lo:hi]
    thresh = float(np.percentile(seg, 90)) * 0.5
    strong = np.flatnonzero(seg >= max(thresh, 1e-9))
    if not strong.size:
        return end
    return float((lo + strong[-1]) * hop / sr)


def audible_bounds(mono, sr: int) -> tuple[float, float]:
    """First/last time the signal rises above the relative silence floor."""
    import numpy as np
    import librosa

    hop = 256
    rms = librosa.feature.rms(y=mono, frame_length=1024, hop_length=hop)[0]
    if not rms.size or rms.max() <= 0:
        return 0.0, len(mono) / sr
    thresh = rms.max() * (10.0 ** (P.silence_floor_db / 20.0))
    loud = np.flatnonzero(rms > thresh)
    if not loud.size:
        return 0.0, len(mono) / sr
    start = librosa.frames_to_time(loud[0], sr=sr, hop_length=hop)
    end = librosa.frames_to_time(loud[-1] + 1, sr=sr, hop_length=hop)
    return float(max(0.0, start)), float(min(end, len(mono) / sr))


def analyze(mono, sr: int) -> Analysis:
    import numpy as np

    hop = 128
    env_fine = onset_env(mono, sr, hop)
    t_fine = np.arange(len(env_fine)) * hop / sr
    env_r = _rectify(env_fine)

    start, end = audible_bounds(mono, sr)

    # Fit the period on a bounded slab from the middle of the track: tempo is
    # constant within a track, and this keeps the cost independent of length.
    mid = (start + end) / 2.0
    half = min(P.period_fit_window_s / 2.0, (end - start) / 2.0)
    e_mid, t_mid = _region(env_r, t_fine, mid - half, mid + half)
    period, confidence = fit_period(e_mid, t_mid)

    # One phase, and therefore ONE beat array, for the whole track. Fitting the
    # phase separately per edge produced two arrays whose indices did not line
    # up, so a single bar-offset index meant a different bar in each -- the tail
    # then landed a beat off. The period is accurate enough that a single grid
    # stays inside the snap tolerance over a full-length track, and the cut is
    # snapped to the real transient at the end anyway.
    e_a, t_a = _region(env_r, t_fine, start, end)
    phi = fit_phase(e_a, t_a, period)

    low = low_band_energy(mono, sr, hop)
    beats = _grid(phi, period, start - period, end + period)

    bar_off = _bar_offset(low, sr, hop, beats)
    downbeats = beats[bar_off::P.beats_per_bar]

    last_kick = _last_transient(env_fine, sr, hop, start, end)

    # Typical transient strength, used to decide whether a grid point actually
    # has a kick on it worth snapping to.
    kicks = kick_times(mono, sr)
    if len(kicks):
        kf = np.clip((kicks * sr / hop).astype(int), 0, len(env_fine) - 1)
        kick_strength = float(np.median(env_fine[kf]))
    else:
        kick_strength = float(np.percentile(env_fine, 99))

    return Analysis(
        bpm=60.0 / period,
        beat_period=period,
        bar_period=period * P.beats_per_bar,
        downbeats=downbeats,
        audible_start=start,
        audible_end=end,
        kick_env=env_fine,
        kick_hop=hop,
        sr=sr,
        mono=mono,
        grid_confidence=confidence,
        last_kick=last_kick,
        kick_strength=kick_strength,
    )


def refine_to_attack(a: Analysis, t: float) -> float:
    """Snap t to the nearby kick peak, then back off to where its attack begins.

    The cut therefore lands just before a transient, never inside one: used at
    the head it keeps the attack whole, used at the tail it leaves that kick to
    the next track instead of emitting a stub.
    """
    import numpy as np

    sr, env, hop = a.sr, a.kick_env, a.kick_hop
    search = P.grid_snap_tolerance_ms / 1000.0 + 0.030
    lo = max(0, int((t - search) * sr / hop))
    hi = min(len(env), int((t + search) * sr / hop) + 1)
    if hi <= lo:
        return max(0.0, t - 0.002)

    peak_rel = int(np.argmax(env[lo:lo + (hi - lo)]))
    peak_t = (lo + peak_rel) * hop / sr

    # Only snap when a real transient sits on this grid point. Without this the
    # search locks onto the loudest frame of a decay tail and drags the cut tens
    # of milliseconds off the bar line.
    if a.kick_strength > 0 and env[lo + peak_rel] < a.kick_strength * P.snap_min_strength:
        return max(0.0, t - 0.002)

    guard = P.attack_guard_max_ms / 1000.0
    w_lo = max(0, int((peak_t - guard) * sr))
    w_hi = min(len(a.mono), int(peak_t * sr) + 1)
    if w_hi <= w_lo + 1:
        return max(0.0, peak_t - 0.002)
    window = np.abs(a.mono[w_lo:w_hi])
    floor = window.max() * P.attack_floor_ratio
    above = np.flatnonzero(window > floor)
    foot = (w_lo + above[0]) / sr if above.size else peak_t - guard
    return max(0.0, min(foot - 0.002, peak_t))


def head_cut_time(a: Analysis) -> float:
    """Where the PLAY begins: the first downbeat at or after the audible start,
    stepped back whole bars if that downbeat would fall past the opening kick.

    The guard earns its place. Without it the bar phase occasionally lands a
    beat late and the track opens on its SECOND kick -- measured at 460 ms past
    the first kick on one track of an eighteen-track set.

    The guard is deliberately narrow. Anchoring the head on the first detected
    kick instead was tried and measured worse across the same set: a weak event
    during a fade-in reads as the first kick, so the head stops being trimmed at
    all, and the lead silence survives. That cost nine of eighteen tracks their
    trim and raised flagged seams from one to eight. Hence: keep the downbeat,
    and only step back when a kick that is genuinely strong sits before it.
    """
    import numpy as np

    db = np.asarray(a.downbeats, dtype=float)
    tol = P.grid_snap_tolerance_ms / 1000.0
    cand = db[db >= a.audible_start - tol] if db.size else np.array([])
    if not cand.size:
        return a.audible_start
    h = float(cand[0])

    kicks = kick_times(a.mono, a.sr)
    if len(kicks) and a.kick_strength > 0:
        sr, hop, env = a.sr, a.kick_hop, a.kick_env
        idx = np.clip((kicks * sr / hop).astype(int), 0, len(env) - 1)
        strong = kicks[env[idx] >= 0.5 * a.kick_strength]
        strong = strong[strong >= a.audible_start - tol]
        if strong.size and h > float(strong[0]) + tol:
            while h > float(strong[0]) + tol and h - a.bar_period >= 0.0:
                h -= a.bar_period
    return refine_to_attack(a, max(0.0, h))


def tail_cut_time(a: Analysis) -> tuple[float, float]:
    """Where track N's OUTRO ends: the last bar line that keeps the outro whole.

    Cutting *at* a bar line means that bar line's kick is not included, so the
    kick heard at the seam is supplied by the next track alone.
    """
    import numpy as np

    db = np.asarray(a.downbeats, dtype=float)
    if not db.size:
        return a.audible_end, 0.0
    # Two bounds: the audible end, and one bar past the last actual transient.
    # The second one stops a long decay tail from pushing the cut a whole bar
    # beyond the last kick, which would open a hole at the handover.
    limit = min(
        a.audible_end + P.max_tail_dead_air_ms / 1000.0,
        a.last_kick + a.bar_period + P.grid_snap_tolerance_ms / 1000.0,
    )
    usable = db[(db <= limit) & (db > a.audible_start)]
    if not usable.size:
        return a.audible_end, 0.0
    cut = float(usable.max())
    return refine_to_attack(a, cut), max(0.0, a.audible_end - cut)


# ---------------------------------------------------------------------------
# Cutting
# ---------------------------------------------------------------------------


def apply_cut(audio, sr: int, start_s: float, end_s: float, tail_fade_ms: float):
    """Slice [start_s, end_s) and apply anti-click micro-fades at both edges."""
    import numpy as np

    n = audio.shape[0]
    i0 = max(0, min(n, int(round(start_s * sr))))
    i1 = max(i0 + 1, min(n, int(round(end_s * sr))))
    seg = np.array(audio[i0:i1], copy=True)

    fi = min(int(P.head_fade_ms * sr / 1000.0), seg.shape[0] // 2)
    fo = min(int(tail_fade_ms * sr / 1000.0), seg.shape[0] // 2)
    if fi > 1:
        ramp = np.linspace(0.0, 1.0, fi, dtype=seg.dtype)
        seg[:fi] = (seg[:fi].T * ramp).T if seg.ndim > 1 else seg[:fi] * ramp
    if fo > 1:
        ramp = np.linspace(1.0, 0.0, fo, dtype=seg.dtype)
        seg[-fo:] = (seg[-fo:].T * ramp).T if seg.ndim > 1 else seg[-fo:] * ramp
    return seg


# ---------------------------------------------------------------------------
# QC
# ---------------------------------------------------------------------------


def kick_times(mono, sr: int):
    """Kick transients only.

    Peak-picking the flux alone also fires on hats and other broadband hits, so
    each candidate is gated on how much 30-130 Hz energy actually follows it.
    The two populations separate by about an order of magnitude, so the gate is
    not delicate. The refractory period is kept below the double-kick threshold
    so a genuine double kick is still detectable.
    """
    import numpy as np
    import librosa

    hop = 128
    env = onset_env(mono, sr, hop)
    if not env.size or env.max() <= 0:
        return np.array([])

    delta = float(np.percentile(env, 75)) * 0.6
    cand = librosa.util.peak_pick(
        env, pre_max=12, post_max=12, pre_avg=40, post_avg=40,
        delta=max(delta, 1e-9),
        wait=max(1, int(P.kick_refractory_ms / 1000.0 * sr / hop)),
    )
    if not len(cand):
        return np.array([])

    band = low_band_energy(mono, sr, hop)
    post = max(1, int(0.040 * sr / hop))
    weight = np.array([
        float(band[c:min(len(band), c + post)].max()) if c < len(band) else 0.0
        for c in cand
    ])
    if not weight.size or weight.max() <= 0:
        return np.array([])
    keep = weight >= np.percentile(weight, 90) * P.kick_energy_gate
    return librosa.frames_to_time(cand[keep], sr=sr, hop_length=hop)


def longest_silence_ms(mono, sr: int) -> float:
    import numpy as np
    import librosa

    hop = 64
    rms = librosa.feature.rms(y=mono, frame_length=256, hop_length=hop)[0]
    if not rms.size or rms.max() <= 0:
        return len(mono) / sr * 1000.0
    thresh = rms.max() * (10.0 ** (P.silence_floor_db / 20.0))
    quiet = rms <= thresh
    best = cur = 0
    for q in quiet:
        cur = cur + 1 if q else 0
        best = max(best, cur)
    return best * hop / sr * 1000.0


@dataclass
class SeamQC:
    left: str
    right: str
    min_seam_ioi_ms: float = float("nan")
    expected_beat_ms: float = float("nan")
    double_kick: bool = False
    beat_jump: bool = False
    dead_air_ms: float = 0.0
    bpm_left: float = 0.0
    bpm_right: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def bpm_drift_pct(self) -> float:
        if self.bpm_left <= 0 or self.bpm_right <= 0:
            return 0.0
        return abs(self.bpm_left - self.bpm_right) / self.bpm_left * 100.0

    @property
    def ok(self) -> bool:
        return (
            not self.double_kick
            and not self.beat_jump
            and self.dead_air_ms <= P.dead_air_limit_ms
            and self.bpm_drift_pct <= P.bpm_mismatch_flag_pct
        )


def check_seam(tail_mono, head_mono, sr: int, beat_period: float,
               left: str, right: str, bpm_l: float, bpm_r: float) -> SeamQC:
    """Join the tail and head in memory and measure the handover."""
    import numpy as np

    qc = SeamQC(left=left, right=right,
                expected_beat_ms=beat_period * 1000.0,
                bpm_left=bpm_l, bpm_right=bpm_r)

    win = min(len(tail_mono), int(2.0 * sr))
    tail = tail_mono[-win:] if win else np.array([])
    head = head_mono[: int(2.0 * sr)]
    joined = np.concatenate([tail, head])
    seam_t = len(tail) / sr

    ks = kick_times(joined, sr)
    near = ks[(ks > seam_t - beat_period * 2) & (ks < seam_t + beat_period * 2)]
    if near.size >= 2:
        iois = np.diff(near)
        straddling = [
            float(d) for a_, d in zip(near[:-1], iois)
            if a_ <= seam_t <= a_ + d
        ]
        pool = straddling or [float(iois.min())]
        qc.min_seam_ioi_ms = min(pool) * 1000.0
        if min(pool) < beat_period * P.double_kick_ratio:
            qc.double_kick = True
        elif min(pool) > beat_period * P.beat_jump_ratio:
            qc.beat_jump = True
    else:
        qc.notes.append("too few kick transients at the seam to measure IOI")

    lo = max(0, int((seam_t - 0.30) * sr))
    hi = min(len(joined), int((seam_t + 0.30) * sr))
    qc.dead_air_ms = longest_silence_ms(joined[lo:hi], sr)

    if qc.bpm_drift_pct > P.bpm_mismatch_flag_pct:
        qc.notes.append(
            f"BPM {qc.bpm_left:.1f} -> {qc.bpm_right:.1f} "
            f"({qc.bpm_drift_pct:.1f}%); not stretched by design"
        )
    return qc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--dry-run", action="store_true",
                    help="analyze and report cut points, write nothing")
    args = ap.parse_args()

    require_tools()
    import numpy as np
    import soundfile as sf
    import librosa

    src = args.source.expanduser().resolve()
    out = args.output.expanduser().resolve()

    if not src.is_dir():
        sys.exit(f"ERROR: source folder not found: {src}")
    if out == src or src in out.parents:
        sys.exit("ERROR: output folder must be outside the source folder.")

    tracks = ordered_tracks(src)
    print(f"SOURCE : {src}")
    print(f"OUTPUT : {out}")
    print(f"ORDER  : {len(tracks)} track(s), by filename number\n")
    for p in tracks:
        print(f"  {track_number(p):02d}  {p.name}")
    print()

    if not args.dry_run:
        out.mkdir(parents=True, exist_ok=True)

    tmpdir = Path(tempfile.mkdtemp(prefix="djcutter_"))
    seams: list[SeamQC] = []
    written: list[Path] = []

    try:
        # Pass 1: decode + analyze every track.
        infos, analyses, wavs = [], [], []
        for idx, path in enumerate(tracks, 1):
            print(f"[{idx}/{len(tracks)}] analyzing {path.name} ...", flush=True)
            info = probe(path)
            wav = tmpdir / f"{idx:03d}.wav"
            decode_to_wav(path, wav)
            mono, _ = librosa.load(str(wav), sr=info.sr, mono=True)
            a = analyze(mono, info.sr)
            infos.append(info)
            analyses.append(a)
            wavs.append(wav)
            print(f"        {a.bpm:6.2f} BPM (grid conf {a.grid_confidence:.3f}) | audible "
                  f"{a.audible_start:6.3f}s -> {a.audible_end:8.3f}s")

        # Pass 2: decide cut points.
        # Head of track 1 and tail of the last track are trimmed of silence only;
        # every interior edge is snapped to the bar grid for the handover.
        cuts = []
        for i, a in enumerate(analyses):
            head = head_cut_time(a)
            tail, discarded = tail_cut_time(a)
            if tail <= head:
                tail = a.audible_end
                discarded = 0.0
            cuts.append({"head": head, "tail": tail, "discarded": discarded})

        # Pass 3: cut, QC the seam, re-cut once if a kick stub survives.
        prev_tail_mono = None
        for i, (path, info, a, wav, cut) in enumerate(
            zip(tracks, infos, analyses, wavs, cuts)
        ):
            audio, sr = sf.read(str(wav), dtype="float32", always_2d=False)
            tail_fade = P.tail_fade_ms

            def render(fade_ms: float):
                seg = apply_cut(audio, sr, cut["head"], cut["tail"], fade_ms)
                m = seg.mean(axis=1) if seg.ndim > 1 else seg
                return seg, np.ascontiguousarray(m, dtype=np.float32)

            seg, mono_seg = render(tail_fade)

            if prev_tail_mono is not None:
                beat_period = analyses[i - 1].beat_period
                qc = check_seam(prev_tail_mono, mono_seg, sr, beat_period,
                                tracks[i - 1].name, path.name,
                                analyses[i - 1].bpm, a.bpm)
                if qc.double_kick:
                    # One corrective attempt: widen the previous tail fade to
                    # swallow the stub transient, then re-measure.
                    prev = cuts[i - 1]
                    prev_audio, _ = sf.read(str(wavs[i - 1]), dtype="float32",
                                            always_2d=False)
                    fixed = apply_cut(prev_audio, sr, prev["head"], prev["tail"],
                                      P.tail_fade_stub_ms)
                    fixed_mono = (fixed.mean(axis=1) if fixed.ndim > 1 else fixed)
                    fixed_mono = np.ascontiguousarray(fixed_mono, dtype=np.float32)
                    qc2 = check_seam(fixed_mono, mono_seg, sr, beat_period,
                                     tracks[i - 1].name, path.name,
                                     analyses[i - 1].bpm, a.bpm)
                    if not qc2.double_kick:
                        qc2.notes.append(
                            f"tail fade widened to {P.tail_fade_stub_ms:.0f} ms "
                            "to remove a kick stub"
                        )
                        qc = qc2
                        cuts[i - 1]["tail_fade"] = P.tail_fade_stub_ms
                        if not args.dry_run and written:
                            tmp_wav = tmpdir / f"refix_{i:03d}.wav"
                            sf.write(str(tmp_wav), fixed, sr, subtype="FLOAT")
                            encode_mp3(tmp_wav, written[-1], infos[i - 1])
                    else:
                        qc.notes.append("STILL flagged after corrective fade -- listen")
                seams.append(qc)

            if not args.dry_run:
                seg_wav = tmpdir / f"cut_{i:03d}.wav"
                sf.write(str(seg_wav), seg, sr, subtype="FLOAT")
                dest = out / path.name
                encode_mp3(seg_wav, dest, info)
                written.append(dest)
                print(f"        wrote {dest.name}  "
                      f"[{cut['head']:.3f}s -> {cut['tail']:.3f}s, "
                      f"{cut['tail'] - cut['head']:.2f}s]")

            prev_tail_mono = mono_seg

        report = build_report(src, out, tracks, infos, analyses, cuts, seams)
        print("\n" + report)

        if not args.dry_run:
            (out / "_QC_REPORT.txt").write_text(report, encoding="utf-8")
            print(f"QC report written to {out / '_QC_REPORT.txt'}")
            print(f"\nDONE. {len(written)} MP3 file(s) in {out}")
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def build_report(src, out, tracks, infos, analyses, cuts, seams) -> str:
    lines = [
        "=" * 78,
        "DJ-Cutter QC REPORT",
        "=" * 78,
        f"source : {src}   (unmodified)",
        f"output : {out}",
        "",
        "TRACKS (numeric order)",
        "-" * 78,
        f"{'#':>3}  {'file':<34} {'BPM':>7} {'head':>8} {'tail':>9} {'kept':>8} {'trimmed':>8}",
    ]
    for i, (p, info, a, c) in enumerate(zip(tracks, infos, analyses, cuts), 1):
        kept = c["tail"] - c["head"]
        trimmed = info.duration_s - kept
        name = p.name if len(p.name) <= 34 else p.name[:31] + "..."
        lines.append(
            f"{i:>3}  {name:<34} {a.bpm:>7.2f} {c['head']:>8.3f} "
            f"{c['tail']:>9.3f} {kept:>8.2f} {trimmed:>8.2f}"
        )

    lines += ["", "HANDOVERS (OUTRO -> PLAY)", "-" * 78]
    if not seams:
        lines.append("  (single track -- no handover)")
    for s in seams:
        flag = "OK  " if s.ok else "FLAG"
        lines.append(
            f"{flag}  {s.left}  ->  {s.right}\n"
            f"        seam kick IOI {s.min_seam_ioi_ms:7.1f} ms "
            f"(beat {s.expected_beat_ms:.1f} ms, min allowed "
            f"{s.expected_beat_ms * P.double_kick_ratio:.1f} ms)\n"
            f"        double kick   {'YES' if s.double_kick else 'no'}\n"
            f"        beat jump     {'YES' if s.beat_jump else 'no'}\n"
            f"        dead air      {s.dead_air_ms:.1f} ms "
            f"(limit {P.dead_air_limit_ms:.0f} ms)\n"
            f"        BPM drift     {s.bpm_drift_pct:.2f} %"
        )
        for n in s.notes:
            lines.append(f"        note: {n}")

    bad = [s for s in seams if not s.ok]
    lines += [
        "", "SUMMARY", "-" * 78,
        f"tracks written : {len(tracks)}",
        f"handovers      : {len(seams)}",
        f"clean          : {len(seams) - len(bad)}",
        f"flagged        : {len(bad)}",
    ]
    if bad:
        lines.append("flagged seams  : " + ", ".join(f"{s.left} -> {s.right}" for s in bad))
    lines += [
        "",
        "Processing applied: sample-accurate cut on the 4/4 bar grid + anti-click",
        f"micro-fades (in {P.head_fade_ms:.0f} ms / out {P.tail_fade_ms:.0f} ms).",
        "No EQ, no normalization, no noise reduction, no stem separation,",
        "no time-stretch. Tags and cover art carried over from the source.",
        "=" * 78,
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
