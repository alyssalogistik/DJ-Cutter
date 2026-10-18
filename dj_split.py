#!/usr/bin/env python3
"""
dj_split.py -- find where one track hands over to the next inside a continuous
DJ mix, and report the candidates with a confidence for each.

Detect-only by default: it never cuts. The candidate list is meant to be read
and approved first, because in a DJ set two tracks commonly overlap for 30-60
seconds and the "real" boundary is a judgement, not a fact.

    python dj_split.py "MIX.mp3"                 # daftar kandidat
    python dj_split.py "MIX.mp3" -o kandidat.json

Source file is opened read-only.
"""

from __future__ import annotations

import argparse, json, shutil, subprocess, sys, tempfile
from dataclasses import dataclass, asdict
from pathlib import Path

BEATS_PER_BAR   = 4
MIN_TRACK_S     = 75.0     # two boundaries closer than this are one event
KERNEL_BEATS    = 64       # checkerboard half-width, i.e. 16 bars of context
BPM_LO, BPM_HI  = 85.0, 160.0
SNAP_SEARCH_S   = 4.0      # how far to look for the bar line nearest a peak


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"gagal: {' '.join(cmd[:4])}...\n{p.stderr[-1200:]}")
    return p


def require():
    if shutil.which("ffmpeg") is None:
        sys.exit("ERROR: ffmpeg tidak ada di PATH.")
    try:
        import numpy, scipy, librosa, soundfile  # noqa: F401
    except ImportError as e:
        sys.exit(f"ERROR: paket Python kurang ({e.name}). "
                 "pip install numpy scipy soundfile librosa")


@dataclass
class Cand:
    waktu_s: float
    waktu_mmss: str
    keyakinan: float          # 0..1
    novelty: float            # puncak Foote ternormalisasi
    jarak_timbre: float       # jarak MFCC antar sisi
    jarak_harmoni: float      # jarak chroma antar sisi
    selisih_bpm: float
    turun_energi_db: float
    materi_kembali: float     # 1.0 = sesudahnya mirip sebelumnya (ciri breakdown)
    digeser_ke_bar_ms: float
    alasan: str


def mmss(t: float) -> str:
    return f"{int(t)//60}:{int(t)%60:02d}"


def load_mono(path: Path, sr: int = 22050):
    """22.05 kHz is plenty for structure; it keeps a 60-minute mix tractable."""
    import librosa
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "a.wav"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-map", "0:a:0",
             "-ac", "1", "-ar", str(sr), "-c:a", "pcm_f32le", str(w)])
        y, _ = librosa.load(str(w), sr=sr, mono=True)
    return y, sr


def low_band_flux(y, sr, hop):
    """Kick-band onset flux -- the same envelope the bar grid is fitted to in
    dj_cutter.py, for the same reasons: a mel basis capped at the kick band
    leaves empty filters, and broadband flux lets offbeat hats pull the phase."""
    import numpy as np, librosa, scipy.signal as sg
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))
    f = librosa.fft_frequencies(sr=sr, n_fft=2048)
    band = S[(f >= 30) & (f <= 130)].sum(axis=0)
    w = max(1, int(0.010 * sr / hop))
    band = np.convolve(band, np.ones(w) / w, mode="same")
    flux = np.maximum(0.0, np.diff(band, prepend=band[0]))
    w2 = max(1, int(0.008 * sr / hop))
    return np.convolve(flux, np.ones(w2) / w2, mode="same")


def fit_grid(env, times):
    """Period and phase of t_k = phi + k*period, from the envelope's Fourier
    coefficient at each candidate period. Validated in dj_cutter.py."""
    import numpy as np
    e = np.maximum(env - env.mean(), 0.0)

    def sweep(bpms):
        per = 60.0 / np.asarray(bpms, float)
        best = (-1.0, float(per[len(per) // 2]))
        step = max(1, 3_000_000 // max(len(times), 1))
        for i in range(0, len(per), step):
            c = per[i:i + step]
            Z = np.exp(-2j * np.pi * np.outer(1.0 / c, times)) @ e
            m = np.abs(Z); j = int(np.argmax(m))
            if m[j] > best[0]:
                best = (float(m[j]), float(c[j]))
        return best

    _, p = sweep(np.arange(BPM_LO, BPM_HI + 1e-9, 0.05))
    bpm = 60.0 / p
    mag, p = sweep(np.arange(max(BPM_LO, bpm - 0.12), min(BPM_HI, bpm + 0.12) + 1e-9, 0.002))
    Z = np.exp(-2j * np.pi * times / p) @ e
    phi = float((-np.angle(Z) * p / (2 * np.pi)) % p)
    return p, phi, mag / max(e.sum(), 1e-12)


def checkerboard(N):
    import numpy as np, scipy.signal.windows as win
    g = np.outer(win.gaussian(2 * N, N / 2.0), win.gaussian(2 * N, N / 2.0))
    k = np.ones((2 * N, 2 * N))
    k[:N, N:] = -1.0
    k[N:, :N] = -1.0
    return k * g


def analyse(path: Path, args) -> tuple[list[Cand], dict]:
    import numpy as np, librosa, scipy.signal as sg

    y, sr = load_mono(path)
    dur = len(y) / sr
    hop = 512

    # --- beat grid over the whole mix -------------------------------------
    env = low_band_flux(y, sr, 128)
    t_env = np.arange(len(env)) * 128 / sr
    period, phi, grid_conf = fit_grid(env, t_env)
    bar = period * BEATS_PER_BAR

    # --- beat-synchronous features ----------------------------------------
    beats_t = np.arange(phi, dur, period)
    beat_fr = librosa.time_to_frames(beats_t, sr=sr, hop_length=hop)
    beat_fr = np.clip(beat_fr, 0, max(0, len(y) // hop - 1))

    mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=hop, n_mfcc=20)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop)
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]

    Msync = librosa.util.sync(mfcc, beat_fr, aggregate=np.median)
    Csync = librosa.util.sync(chroma, beat_fr, aggregate=np.median)
    Rsync = librosa.util.sync(rms[np.newaxis, :], beat_fr, aggregate=np.median)[0]
    nb = min(Msync.shape[1], Csync.shape[1], len(Rsync))
    Msync, Csync, Rsync = Msync[:, :nb], Csync[:, :nb], Rsync[:nb]

    def znorm(A):
        s = A.std(axis=1, keepdims=True); s[s == 0] = 1.0
        return (A - A.mean(axis=1, keepdims=True)) / s

    F = np.vstack([znorm(Msync[1:]), znorm(Csync) * 0.8])   # drop MFCC0 (loudness)
    F /= (np.linalg.norm(F, axis=0, keepdims=True) + 1e-9)
    S = F.T @ F                                             # cosine self-similarity

    # --- Foote novelty -----------------------------------------------------
    N = min(KERNEL_BEATS, max(8, nb // 8))
    K = checkerboard(N)
    nov = np.zeros(nb)
    for i in range(N, nb - N):
        nov[i] = float((S[i - N:i + N, i - N:i + N] * K).sum())
    nov = np.maximum(nov, 0)
    if nov.max() > 0:
        nov /= nov.max()

    # --- candidate peaks ---------------------------------------------------
    # Deliberately NOT suppressed at minimum-track-length. Doing so let a
    # breakdown peak delete a genuine boundary 59 s away in testing. Peaks are
    # kept dense and separated by confidence instead, which is the human's call.
    pk, _ = sg.find_peaks(nov, height=args.min_novelty,
                          distance=max(4, int(16 * BEATS_PER_BAR)))
    cands: list[Cand] = []
    W = max(8, int(20.0 / period))      # 20 s of context each side

    for i in pk:
        t = float(beats_t[i]) if i < len(beats_t) else float(i * period + phi)
        if t < MIN_TRACK_S or t > dur - MIN_TRACK_S:
            continue
        a0, a1 = max(0, i - W), i
        b0, b1 = i, min(nb, i + W)
        if a1 - a0 < 4 or b1 - b0 < 4:
            continue

        tim = float(np.linalg.norm(Msync[1:, a0:a1].mean(1) - Msync[1:, b0:b1].mean(1)))
        har = float(np.linalg.norm(Csync[:, a0:a1].mean(1) - Csync[:, b0:b1].mean(1)))

        # Long-range return test, the discriminator that actually separates a
        # breakdown from a track change: after a breakdown the music comes back
        # to the same track, after a track change it does not. Compared well
        # away from the candidate so the transition itself is not sampled.
        FAR, NEAR = int(90.0 / period), int(30.0 / period)
        la0, la1 = max(0, i - FAR), max(0, i - NEAR)
        lb0, lb1 = min(nb, i + NEAR), min(nb, i + FAR)
        if la1 - la0 >= 8 and lb1 - lb0 >= 8:
            # Compared on F -- z-scored per dimension across the whole mix and
            # L2-normalised per frame. Raw MFCC means were tried first and had
            # no discriminating power at all: the mean spectral shape of any
            # 4/4 dance track resembles any other, so every candidate scored
            # 0.96-1.00 and the term was pure noise.
            va = F[:, la0:la1].mean(1); vb = F[:, lb0:lb1].mean(1)
            cs = float(va @ vb / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-9))
            kembali = max(0.0, min(1.0, (cs + 1) / 2))      # 1 = material returns
        else:
            kembali = 0.5

        ia, ib = int(beats_t[a0] * sr), int(min(len(y), beats_t[min(b1, nb - 1)] * sr))
        half = (ia + ib) // 2
        try:
            pa, _, _ = fit_grid(low_band_flux(y[ia:half], sr, 128),
                                np.arange(len(low_band_flux(y[ia:half], sr, 128))) * 128 / sr)
            pb, _, _ = fit_grid(low_band_flux(y[half:ib], sr, 128),
                                np.arange(len(low_band_flux(y[half:ib], sr, 128))) * 128 / sr)
            dbpm = abs(60.0 / pa - 60.0 / pb)
        except Exception:
            dbpm = 0.0

        ea, eb = Rsync[a0:a1].mean(), Rsync[b0:b1].mean()
        drop = float(20 * np.log10(max(eb, 1e-9) / max(ea, 1e-9)))

        # snap to the nearest bar line
        k = round((t - phi) / bar)
        t_bar = phi + k * bar
        if abs(t_bar - t) > SNAP_SEARCH_S:
            t_bar = t
        shift_ms = (t_bar - t) * 1000.0

        nvl = float(nov[i])
        beda_jauh = 1.0 - kembali
        conf = (0.45 * min(beda_jauh / 0.35, 1.0)      # materi tidak kembali
                + 0.25 * nvl
                + 0.15 * min(tim / 40.0, 1.0)
                + 0.08 * min(har / 2.0, 1.0)
                + 0.07 * min(dbpm / 3.0, 1.0))
        conf = max(0.0, min(1.0, conf))

        why = []
        if nvl > 0.6: why.append("novelty kuat")
        elif nvl > 0.35: why.append("novelty sedang")
        else: why.append("novelty lemah")
        if tim > 25: why.append("timbre berubah")
        if har > 1.5: why.append("harmoni/key berubah")
        if dbpm > 1.0: why.append(f"BPM geser {dbpm:.1f}")
        if abs(drop) > 3: why.append(f"energi {drop:+.1f} dB")
        if kembali > 0.90: why.append("MATERI KEMBALI -- mungkin breakdown, bukan ganti lagu")
        elif kembali < 0.70: why.append("materi tidak kembali")

        cands.append(Cand(round(t_bar, 3), mmss(t_bar), round(conf, 3), round(nvl, 3),
                          round(tim, 1), round(har, 2), round(dbpm, 2), round(drop, 1),
                          round(kembali, 3),
                          round(shift_ms, 1), ", ".join(why)))

    meta = dict(file=path.name, durasi_s=round(dur, 1), bpm=round(60.0 / period, 2),
                bar_s=round(bar, 4), grid_conf=round(grid_conf, 4),
                beat_terdeteksi=nb, kandidat=len(cands))
    return cands, meta


def report(c: list[Cand], m: dict) -> str:
    L = ["=" * 78, f"KANDIDAT TITIK POTONG -- {m['file']}", "=" * 78,
         f"durasi {m['durasi_s']:.1f} s ({mmss(m['durasi_s'])}) | "
         f"BPM {m['bpm']} | bar {m['bar_s']:.3f} s | grid conf {m['grid_conf']}",
         f"kandidat ditemukan: {m['kandidat']}", "",
         f"{'#':>3} {'waktu':>8} {'yakin':>7} {'nov':>6} {'timbre':>7} "
         f"{'kembali':>8} {'dBPM':>6}  catatan", "-" * 78]
    for i, x in enumerate(c, 1):
        L.append(f"{i:>3} {x.waktu_mmss:>8} {x.keyakinan:>7.2f} {x.novelty:>6.2f} "
                 f"{x.jarak_timbre:>7.1f} {x.materi_kembali:>8.2f} {x.selisih_bpm:>6.2f}"
                 f"  {x.alasan}")
    hi = [x for x in c if x.keyakinan >= 0.60]
    md = [x for x in c if 0.40 <= x.keyakinan < 0.60]
    lo = [x for x in c if x.keyakinan < 0.40]
    L += ["", f"keyakinan tinggi (>=0.60): {len(hi)}  -- hampir pasti ganti lagu",
          f"sedang (0.40-0.59)       : {len(md)}  -- perlu dicek telinga",
          f"rendah (<0.40)           : {len(lo)}  -- mungkin hanya break/drop", "",
          "BELUM ADA PEMOTONGAN. Daftar ini untuk diperiksa dan disetujui dulu.",
          "Di DJ set dua lagu sering tumpang tindih 30-60 detik, jadi batasnya",
          "penilaian, bukan fakta -- angka di atas bahan pertimbangan, bukan vonis.",
          "=" * 78]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mix", type=Path, nargs="+")
    ap.add_argument("-o", "--out", type=Path, default=Path("kandidat_potong.json"))
    ap.add_argument("--min-novelty", type=float, default=0.12)
    a = ap.parse_args()
    require()
    allout = []
    for p in a.mix:
        p = p.expanduser().resolve()
        if not p.is_file():
            print(f"LEWAT: {p} tidak ada"); continue
        print(f"menganalisis {p.name} ...", flush=True)
        c, m = analyse(p, a)
        print("\n" + report(c, m) + "\n")
        allout.append(dict(meta=m, kandidat=[asdict(x) for x in c]))
    a.out.write_text(json.dumps(allout, indent=2), encoding="utf-8")
    print(f"JSON: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
