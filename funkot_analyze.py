#!/usr/bin/env python3
"""
funkot_analyze.py -- measure what is actually wrong with each track.

Prints a per-track diagnosis and writes a JSON report that funkot_restore.py
consumes, so every processing decision traces back to a measurement rather than
a guess. Reads only; never writes to the source folder.

    python funkot_analyze.py "<FOLDER>" [-o laporan.json]
"""

from __future__ import annotations

import argparse, json, re, shutil, subprocess, sys, tempfile
from dataclasses import dataclass, asdict, field
from pathlib import Path

# --- thresholds: the line between "measured" and "worth acting on" ----------

NOISE_FLOOR_ACT_DB   = -60.0   # noise floor above this is audible between beats
HISS_ACT_DB          = -65.0   # 8-16 kHz energy in quiet frames
HUM_ACT_DB           =   6.0   # mains bin this far above its neighbours
CLIP_ACT_PCT         =   0.05  # % of samples pinned at full scale
CLICK_ACT_PER_MIN    =   5.0
DC_ACT               =   0.005
MUD_ACT              =   0.42  # 250-500 Hz share of the 60-6000 Hz band
DULL_ACT             =   0.02  # 6-12 kHz share below this reads as dull
CORR_LOW             =   0.20  # stereo correlation below this risks mono collapse
DEAD_AIR_ACT_S       =   1.50
MAINS_HZ             = (50.0, 100.0, 150.0, 200.0)   # Indonesia: 50 Hz mains


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"gagal: {' '.join(cmd[:4])}...\n{p.stderr[-1500:]}")
    return p


def require_tools() -> None:
    miss = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if miss:
        sys.exit(f"ERROR: {', '.join(miss)} tidak ada di PATH. Pasang FFmpeg dulu.")
    try:
        import numpy, scipy, soundfile, pyloudnorm  # noqa: F401
    except ImportError as e:
        sys.exit(f"ERROR: paket Python kurang ({e.name}).\n"
                 "  python -m pip install numpy scipy soundfile librosa pyloudnorm")


def track_number(p: Path):
    m = re.search(r"\d+", p.stem)
    return int(m.group()) if m else None


def ordered(folder: Path) -> list[Path]:
    exts = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"}
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts]
    if not files:
        sys.exit(f"ERROR: tidak ada file audio di {folder}")
    return sorted(files, key=lambda p: (track_number(p) is None,
                                        track_number(p) or 0, p.name.lower()))


@dataclass
class Diag:
    file: str
    sr: int = 0
    channels: int = 0
    bitrate_kbps: int = 0
    duration_s: float = 0.0
    codec: str = ""
    lowpass_hz: float = 0.0
    noise_floor_db: float = 0.0
    hiss_db: float = 0.0
    hum: dict = field(default_factory=dict)
    clip_pct: float = 0.0
    flat_runs: int = 0
    true_peak_db: float = 0.0
    sample_peak_db: float = 0.0
    lufs: float = 0.0
    lra: float = 0.0
    crest_db: float = 0.0
    dc_offset: float = 0.0
    stereo_corr: float = 0.0
    side_ratio: float = 0.0
    clicks_per_min: float = 0.0
    lead_silence_s: float = 0.0
    tail_silence_s: float = 0.0
    long_gaps: list = field(default_factory=list)
    bands: dict = field(default_factory=dict)
    mud_index: float = 0.0
    air_index: float = 0.0
    issues: list = field(default_factory=list)
    actions: list = field(default_factory=list)


def probe(path: Path) -> dict:
    p = run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=sample_rate,channels,bit_rate,codec_name:format=bit_rate,duration",
             "-of", "json", str(path)])
    d = json.loads(p.stdout)
    s = (d.get("streams") or [{}])[0]
    f = d.get("format") or {}
    br = s.get("bit_rate") or f.get("bit_rate") or 0
    return dict(sr=int(s.get("sample_rate") or 44100),
                channels=int(s.get("channels") or 2),
                bitrate_kbps=int(round(int(br) / 1000)) if int(br) else 0,
                codec=s.get("codec_name") or "",
                duration_s=float(f.get("duration") or 0.0))


def decode(path: Path, dst: Path) -> None:
    run(["ffmpeg", "-v", "error", "-y", "-i", str(path),
         "-map", "0:a:0", "-c:a", "pcm_f32le", "-f", "wav", str(dst)])


def db(x, floor=1e-12):
    import numpy as np
    return 20.0 * np.log10(np.maximum(np.abs(x), floor))


def analyse(path: Path) -> Diag:
    import numpy as np, soundfile as sf, scipy.signal as sg, pyloudnorm as pyln

    d = Diag(file=path.name, **probe(path))
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        decode(path, wav)
        x, sr = sf.read(str(wav), dtype="float64", always_2d=True)

    d.sr = sr
    n, ch = x.shape
    d.duration_s = n / sr
    mono = x.mean(axis=1)

    # ---- levels -----------------------------------------------------------
    d.sample_peak_db = float(db(np.abs(x).max()))
    up = sg.resample_poly(mono, 4, 1)          # 4x oversample -> inter-sample peaks
    d.true_peak_db = float(db(np.abs(up).max()))
    rms = float(np.sqrt((mono ** 2).mean()))
    d.crest_db = float(db(np.abs(mono).max()) - db(rms))
    try:
        meter = pyln.Meter(sr)
        d.lufs = float(meter.integrated_loudness(x if ch > 1 else mono))
    except Exception:
        d.lufs = float("nan")

    # loudness range, from short-term loudness spread
    try:
        win, hop = int(3 * sr), int(1 * sr)
        st = [meter.integrated_loudness(x[i:i + win])
              for i in range(0, max(1, n - win), hop)]
        st = [v for v in st if np.isfinite(v)]
        d.lra = float(np.percentile(st, 95) - np.percentile(st, 10)) if len(st) > 4 else 0.0
    except Exception:
        d.lra = 0.0

    # ---- clipping ---------------------------------------------------------
    at_fs = np.abs(x) >= 0.9985
    d.clip_pct = float(at_fs.sum() / at_fs.size * 100.0)
    flat = 0
    m = np.abs(mono)
    hot = m >= 0.9985
    i = 0
    while i < n:
        if hot[i]:
            j = i
            while j + 1 < n and hot[j + 1] and abs(mono[j + 1] - mono[i]) < 1e-6:
                j += 1
            if j - i + 1 >= 3:
                flat += 1
            i = j + 1
        else:
            i += 1
    d.flat_runs = int(flat)

    # ---- DC + stereo ------------------------------------------------------
    d.dc_offset = float(np.abs(x.mean(axis=0)).max())
    if ch >= 2:
        L, R = x[:, 0], x[:, 1]
        sd = L.std() * R.std()
        d.stereo_corr = float(((L - L.mean()) * (R - R.mean())).mean() / sd) if sd > 0 else 1.0
        mid, side = (L + R) / 2, (L - R) / 2
        mp = float(np.sqrt((mid ** 2).mean()))
        d.side_ratio = float(np.sqrt((side ** 2).mean()) / mp) if mp > 0 else 0.0
    else:
        d.stereo_corr, d.side_ratio = 1.0, 0.0

    # ---- spectrum: long-term average --------------------------------------
    nfft = 1 << 15
    f, P = sg.welch(mono, sr, nperseg=min(nfft, n), noverlap=min(nfft, n) // 2)
    Pdb = 10 * np.log10(np.maximum(P, 1e-20))
    ref = float(np.median(Pdb[(f >= 1000) & (f <= 4000)]))

    # lowpass cutoff: highest frequency still within 50 dB of the midband
    alive = np.flatnonzero(Pdb > ref - 50.0)
    d.lowpass_hz = float(f[alive[-1]]) if alive.size else float(sr / 2)

    def band(lo, hi):
        m_ = (f >= lo) & (f < hi)
        return float(P[m_].sum()) if m_.any() else 0.0

    bands = {"sub_20_60": band(20, 60), "bass_60_150": band(60, 150),
             "lowbass_150_250": band(150, 250), "mud_250_500": band(250, 500),
             "mid_500_2k": band(500, 2000), "presence_2k_6k": band(2000, 6000),
             "brilliance_6k_12k": band(6000, 12000), "air_12k_20k": band(12000, 20000)}
    tot = sum(bands.values()) or 1.0
    d.bands = {k: round(v / tot, 4) for k, v in bands.items()}
    core = bands["bass_60_150"] + bands["lowbass_150_250"] + bands["mud_250_500"] \
        + bands["mid_500_2k"] + bands["presence_2k_6k"]
    d.mud_index = round(bands["mud_250_500"] / core, 4) if core else 0.0
    d.air_index = round(bands["brilliance_6k_12k"] / tot, 4)

    # ---- mains hum: narrow bin vs its own neighbourhood -------------------
    nf2 = 1 << 16
    f2, P2 = sg.welch(mono, sr, nperseg=min(nf2, n), noverlap=min(nf2, n) // 2)
    P2db = 10 * np.log10(np.maximum(P2, 1e-20))
    for hz in MAINS_HZ:
        tgt = (f2 >= hz - 1.5) & (f2 <= hz + 1.5)
        nb = ((f2 >= hz - 20) & (f2 <= hz - 4)) | ((f2 >= hz + 4) & (f2 <= hz + 20))
        if tgt.any() and nb.any():
            d.hum[str(int(hz))] = round(float(P2db[tgt].max() - np.median(P2db[nb])), 2)

    # ---- noise floor + hiss, measured on the quietest frames --------------
    fl, hp = 2048, 512
    nfr = max(1, (n - fl) // hp)
    fr = np.lib.stride_tricks.as_strided(
        mono, (nfr, fl), (mono.strides[0] * hp, mono.strides[0]))
    fr_rms = np.sqrt((fr ** 2).mean(axis=1))
    # Digital silence (lead-in padding, muted gaps) is not noise -- including it
    # reports a floor tens of dB below the real one and skips denoising that is
    # actually needed. Measure the floor only on frames carrying signal.
    live = np.flatnonzero(fr_rms > 10 ** (-90 / 20))
    pool = live if live.size >= 8 else np.arange(nfr)
    quiet_idx = pool[np.argsort(fr_rms[pool])][:max(1, pool.size // 100)]
    d.noise_floor_db = float(db(np.median(fr_rms[quiet_idx]) + 1e-12))

    qs = np.concatenate([fr[i] for i in quiet_idx[:64]]) if quiet_idx.size else mono[:fl]
    if qs.size >= 1024:
        fq, Pq = sg.welch(qs, sr, nperseg=1024)
        hb = (fq >= 8000) & (fq <= 16000)
        d.hiss_db = float(10 * np.log10(max(Pq[hb].sum(), 1e-20))) if hb.any() else -120.0
    else:
        d.hiss_db = -120.0

    # ---- clicks ----------------------------------------------------------
    # Residual against a short median filter, scored against a rolling MAD so
    # loud passages raise their own bar. Reported as an INDEX, not a count:
    # validated against known click positions in 128 kbps material it keeps the
    # ranking between tracks and almost never fires falsely, but finds roughly
    # half the clicks -- the codec smears a single-sample impulse, so an exact
    # count is not recoverable from a lossy source. Enough to decide whether
    # declick is warranted, which is all it is used for.
    res = mono - sg.medfilt(mono, 7)
    w = int(0.050 * sr) | 1
    scale = sg.oaconvolve(np.abs(res), np.ones(w) / w, mode="same") * 1.4826 + 1e-9
    thr = np.maximum(15.0 * scale, 0.02 * float(np.abs(mono).max()))
    hit = np.flatnonzero(np.abs(res) > thr)
    events = 0
    if hit.size:
        events = 1 + int((np.diff(hit) > int(0.012 * sr)).sum())
    d.clicks_per_min = round(events / max(d.duration_s / 60.0, 1e-9), 2)

    # ---- silence / dead air ----------------------------------------------
    thr = max(np.abs(mono).max() * 10 ** (-50 / 20), 1e-6)
    loud = np.flatnonzero(np.abs(mono) > thr)
    if loud.size:
        d.lead_silence_s = round(float(loud[0] / sr), 3)
        d.tail_silence_s = round(float((n - loud[-1]) / sr), 3)
        env = np.sqrt((fr ** 2).mean(axis=1))
        q = env <= thr
        runs, i = [], 0
        while i < len(q):
            if q[i]:
                j = i
                while j + 1 < len(q) and q[j + 1]:
                    j += 1
                dur = (j - i + 1) * hp / sr
                if dur >= DEAD_AIR_ACT_S and i > 0 and j < len(q) - 1:
                    runs.append({"mulai_s": round(i * hp / sr, 2), "durasi_s": round(dur, 2)})
                i = j + 1
            else:
                i += 1
        d.long_gaps = runs[:12]
    return d


def judge(d: Diag) -> Diag:
    """Turn measurements into findings and the actions they justify."""
    iss, act = d.issues, d.actions

    if d.lowpass_hz < 17000:
        iss.append(f"Pita atas terpotong di ~{d.lowpass_hz/1000:.1f} kHz "
                   f"(ciri sumber MP3 bitrate rendah{', ' + str(d.bitrate_kbps) + ' kbps' if d.bitrate_kbps else ''}).")
        act.append(f"JANGAN denoise/EQ di atas {d.lowpass_hz/1000:.1f} kHz — di sana tidak ada sinyal, "
                   "hanya menambah artefak. 'Air' tidak bisa dikembalikan; kalau ada master lebih baik, pakai itu.")

    hum_hit = {k: v for k, v in d.hum.items() if v >= HUM_ACT_DB}
    if hum_hit:
        s = ", ".join(f"{k} Hz +{v:.1f} dB" for k, v in hum_hit.items())
        iss.append(f"Dengung listrik: {s} di atas sekitarnya.")
        act.append("Notch sempit (Q 30) pada frekuensi itu saja, mulai -6 dB lalu naikkan "
                   "secukupnya. Jangan high-pass lebar — bass funkot ada di 50-60 Hz.")

    if d.noise_floor_db > NOISE_FLOOR_ACT_DB:
        iss.append(f"Noise floor tinggi: {d.noise_floor_db:.1f} dBFS (terdengar di sela beat).")
        act.append(f"Denoise ringan, nf sekitar {d.noise_floor_db:.0f} dB, reduksi maksimal 6-9 dB. "
                   "Lebih dari itu hi-hat dan reverb funkot jadi berair.")
    if d.hiss_db > HISS_ACT_DB and d.lowpass_hz >= 14000:
        iss.append(f"Hiss pita 8-16 kHz: {d.hiss_db:.1f} dB saat bagian sepi.")
        act.append("De-hiss lembut terbatas di atas 8 kHz.")

    if d.clip_pct >= CLIP_ACT_PCT or d.flat_runs > 0:
        iss.append(f"Clipping: {d.clip_pct:.3f}% sampel mentok, {d.flat_runs} puncak rata.")
        act.append("Declip untuk merekonstruksi puncak, lalu turunkan gain. "
                   "JANGAN dilimit ulang di level yang sama — itu memotong lagi.")
    if d.true_peak_db > -0.3:
        iss.append(f"True peak {d.true_peak_db:+.2f} dBTP — akan clip saat di-encode ke MP3.")
        act.append("Beri ceiling true-peak -1.0 dBTP.")

    if d.crest_db < 8.0:
        iss.append(f"Crest factor {d.crest_db:.1f} dB — sudah sangat dipadatkan di sumbernya.")
        act.append("JANGAN tambah kompresi. Menambah compressor di materi seperti ini "
                   "menghasilkan pumping, bukan punch.")
    if abs(d.dc_offset) > DC_ACT:
        iss.append(f"DC offset {d.dc_offset:.4f} — memakan headroom.")
        act.append("High-pass 20 Hz untuk membuang DC tanpa menyentuh sub-bass.")

    if d.mud_index > MUD_ACT:
        iss.append(f"Muddy: pita 250-500 Hz memegang {d.mud_index*100:.0f}% energi inti.")
        act.append("Cut lebar -2 sampai -3 dB di 300-400 Hz (Q 1.0). Jangan lebih dalam, "
                   "badan vokal funkot ada di situ.")
    if d.air_index < DULL_ACT and d.lowpass_hz >= 14000:
        iss.append(f"Agak tumpul: 6-12 kHz hanya {d.air_index*100:.1f}% dari total.")
        act.append("High-shelf +1.5 sampai +2 dB di 8 kHz. Lebih dari itu jadi cempreng.")

    if d.clicks_per_min >= CLICK_ACT_PER_MIN:
        iss.append(f"Klik/crackle: indeks {d.clicks_per_min:.1f}/menit "
                   "(indikatif — pada sumber bitrate rendah jumlah pastinya tak terukur).")
        act.append("Declick ringan — hanya pada spike terdeteksi, bukan seluruh lagu.")

    if d.channels >= 2 and d.stereo_corr < CORR_LOW:
        iss.append(f"Korelasi stereo {d.stereo_corr:.2f} — berisiko hilang saat sistem mono.")
        act.append("Jadikan bass di bawah 150 Hz mono. Sound system klub sering mono di sub.")

    if d.lead_silence_s > 0.5:
        iss.append(f"Hening di awal {d.lead_silence_s:.2f} s.")
        act.append("Potong hening awal sampai ~10 ms sebelum transien pertama.")
    if d.tail_silence_s > 1.0:
        iss.append(f"Hening di akhir {d.tail_silence_s:.2f} s.")
        act.append("Rapikan ekor, sisakan decay alami.")
    for g in d.long_gaps:
        iss.append(f"Jeda {g['durasi_s']}s pada menit {int(g['mulai_s']//60)}:{int(g['mulai_s']%60):02d}.")
    if d.long_gaps:
        act.append("PERIKSA MANUAL dulu — di funkot jeda panjang sering memang break/drop "
                   "yang disengaja. Potong hanya kalau benar-benar kosong.")

    if not iss:
        iss.append("Tidak ada masalah di atas ambang. Materi sudah layak pakai.")
    return d


def report(ds: list[Diag]) -> str:
    L = ["=" * 78, "LAPORAN ANALISIS FUNKOT", "=" * 78, ""]
    L += [f"{'#':>3}  {'berkas':<26}{'LUFS':>7}{'dBTP':>7}{'crest':>7}"
          f"{'LPF kHz':>9}{'noise':>8}{'clip%':>7}", "-" * 78]
    for i, d in enumerate(ds, 1):
        nm = d.file if len(d.file) <= 26 else d.file[:23] + "..."
        L.append(f"{i:>3}  {nm:<26}{d.lufs:>7.1f}{d.true_peak_db:>7.2f}{d.crest_db:>7.1f}"
                 f"{d.lowpass_hz/1000:>9.1f}{d.noise_floor_db:>8.1f}{d.clip_pct:>7.3f}")
    lf = [d.lufs for d in ds if d.lufs == d.lufs]
    if len(lf) > 1:
        L += ["", f"Sebaran loudness antar lagu: {min(lf):.1f} s/d {max(lf):.1f} LUFS "
                  f"(selisih {max(lf)-min(lf):.1f} dB).",
              "Untuk DJ set, selisih di atas 3 dB berarti harus main gain tiap ganti lagu."]
    for i, d in enumerate(ds, 1):
        L += ["", "=" * 78, f"[{i}] {d.file}", "-" * 78,
              f"format   : {d.codec} {d.bitrate_kbps} kbps, {d.sr} Hz, "
              f"{d.channels} ch, {d.duration_s:.1f} s",
              f"loudness : {d.lufs:.1f} LUFS | LRA {d.lra:.1f} | "
              f"true peak {d.true_peak_db:+.2f} dBTP | crest {d.crest_db:.1f} dB",
              f"spektrum : batas atas {d.lowpass_hz/1000:.1f} kHz | "
              f"mud {d.mud_index*100:.0f}% | air {d.air_index*100:.1f}%",
              f"stereo   : korelasi {d.stereo_corr:+.2f} | side/mid {d.side_ratio:.2f} | "
              f"DC {d.dc_offset:.4f}",
              f"dengung  : " + (", ".join(f"{k}Hz {v:+.1f}dB" for k, v in d.hum.items()) or "-"),
              "", "MASALAH:"]
        L += [f"  - {t}" for t in d.issues]
        if d.actions:
            L += ["", "TINDAKAN:"] + [f"  > {t}" for t in d.actions]
    L += ["", "=" * 78,
          "Semua angka di atas hasil pengukuran berkas, bukan perkiraan.",
          "Catatan: indeks klik bersifat indikatif. Divalidasi terhadap posisi klik",
          "yang diketahui pada materi 128 kbps: urutan antar lagu benar dan nyaris",
          "tanpa false positive, tetapi hanya menemukan sekitar separuh klik --",
          "encoder lossy menghapus jejak impuls satu-sampel. Cukup untuk memutuskan",
          "perlu declick atau tidak, dan hanya untuk itu dipakai.",
          "Belum ada audio yang diproses oleh skrip ini.", "=" * 78]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("analisis_funkot.json"))
    a = ap.parse_args()
    require_tools()
    src = a.folder.expanduser().resolve()
    if not src.is_dir():
        sys.exit(f"ERROR: folder tidak ada: {src}")
    files = ordered(src)
    print(f"SUMBER: {src}  ({len(files)} berkas, hanya dibaca)\n")
    out = []
    for i, p in enumerate(files, 1):
        print(f"[{i}/{len(files)}] mengukur {p.name} ...", flush=True)
        out.append(judge(analyse(p)))
    txt = report(out)
    print("\n" + txt)
    a.out.write_text(txt.join(["", ""]) if False else
                     json.dumps([asdict(d) for d in out], indent=2), encoding="utf-8")
    Path(a.out).with_suffix(".txt").write_text(txt, encoding="utf-8")
    print(f"\nJSON  : {a.out}\nTeks  : {Path(a.out).with_suffix('.txt')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
