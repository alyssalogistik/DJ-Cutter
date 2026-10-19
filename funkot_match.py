#!/usr/bin/env python3
"""
funkot_match.py -- bawa tiap lagu mendekati karakter tonal sebuah lagu acuan.

Acuan diukur, tidak ditebak: keseimbangan spektrum jangka panjang lagu acuan
diambil per pita, lalu tiap lagu target dikoreksi menuju angka itu dengan EQ
lembut berbatas. Hanya pita yang selisihnya melewati ambang yang disentuh.

Sengaja TIDAK memakai compressor, limiter, maupun loudness normalization.
Durasi tidak pernah dipotong. Berkas sumber hanya dibaca.

    python funkot_match.py --ref "ACUAN.mp3" "SUMBER" "HASIL"
"""

from __future__ import annotations

import argparse, json, shutil, subprocess, sys, tempfile
from pathlib import Path

# Pita + aturan karakter. Batas atas/bawah per pita menjaga "mantap, bulat,
# hangat" tetap mungkin sementara yang tajam tidak pernah diangkat.
BANDS = [
    # nama,            f,     Q,   cut_max, boost_max
    ("sub",            35.0,  0.9,  2.0, 1.5),
    ("kick",           70.0,  1.0,  1.5, 2.5),   # badan kick: boleh ditambah, pelit dipotong
    ("bass",          140.0,  1.0,  2.0, 2.5),   # kehangatan
    ("lowmid",        280.0,  1.1,  3.0, 1.5),   # sumber muddy: boleh dipotong lebih dalam
    ("mid",           700.0,  1.1,  2.5, 1.5),
    ("upmid",        1800.0,  1.1,  2.5, 1.0),
    ("harsh",        3800.0,  1.2,  3.0, 0.0),   # tajam/cempreng: TIDAK PERNAH diangkat
    ("high",         8000.0,  1.0,  2.5, 1.5),
    ("air",         13000.0,  0.9,  2.0, 1.0),
]
EDGES = [(20, 50), (50, 100), (100, 200), (200, 400), (400, 1000),
         (1000, 3000), (3000, 6000), (6000, 11000), (11000, 16000)]

DEADBAND_DB = 0.8      # selisih di bawah ini dibiarkan
# adeclick MATI secara default, dan itu keputusan hasil ukur, bukan kehati-hatian
# kosong. Dua pengukuran mendasarinya. Pertama, pada materi yang sudah bersih
# memasang adeclick menggeser keseimbangan pita 1.43 dB, sementara re-encode MP3
# murni hanya 0.018 dB -- filter ini tidak gratis. Kedua, indeks klik tidak cukup
# andal untuk dijadikan ambang otomatis: pada materi 320 kbps dengan hi-hat dan
# clap tajam ia membaca 28/menit walau tidak ada satu pun klik disuntikkan.
# Indeksnya tetap dilaporkan supaya bisa dibandingkan antar lagu, dan --declick
# tersedia ketika telinga memang mendengar crackle.
TP_CEILING  = -1.0     # hanya trim gain kalau true peak melewati ini -- bukan limiter


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"gagal: {' '.join(cmd[:5])}...\n{p.stderr[-1200:]}")
    return p


def require():
    if shutil.which("ffmpeg") is None:
        sys.exit("ERROR: ffmpeg tidak ada di PATH.")
    try:
        import numpy, scipy, soundfile  # noqa: F401
    except ImportError as e:
        sys.exit(f"ERROR: paket Python kurang ({e.name}).")


def decode(path: Path, dst: Path):
    run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-map", "0:a:0",
         "-c:a", "pcm_f32le", "-f", "wav", str(dst)])


def spectrum(path: Path):
    """Keseimbangan pita jangka panjang, dinormalkan ke total energi supaya
    beda loudness tidak bocor jadi beda warna. Plus true peak dan durasi."""
    import numpy as np, soundfile as sf, scipy.signal as sg
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "a.wav"
        decode(path, w)
        x, sr = sf.read(str(w), dtype="float64", always_2d=True)
    m = x.mean(axis=1)
    n = len(m)
    f, P = sg.welch(m, sr, nperseg=min(1 << 15, n), noverlap=min(1 << 15, n) // 2)
    e = []
    for lo, hi in EDGES:
        sel = (f >= lo) & (f < hi)
        e.append(float(P[sel].sum()) if sel.any() else 1e-20)
    tot = sum(e) or 1.0
    db = [10 * np.log10(max(v / tot, 1e-12)) for v in e]
    # Indeks klik: residual terhadap median filter, diskor pada MAD bergulir.
    # Diukur karena adeclick TIDAK gratis -- diuji pada materi bersih, memasang
    # adeclick tanpa perlu menggeser keseimbangan pita 1.43 dB, sementara
    # re-encode MP3 murni hanya 0.018 dB. Jadi filter ini hanya boleh menyala
    # kalau memang ada klik.
    res = m - sg.medfilt(m, 7)
    wmad = int(0.050 * sr) | 1
    scale = sg.oaconvolve(np.abs(res), np.ones(wmad) / wmad, mode="same") * 1.4826 + 1e-9
    hit = np.flatnonzero(np.abs(res) > np.maximum(15.0 * scale, 0.02 * np.abs(m).max()))
    ev = (1 + int((np.diff(hit) > int(0.012 * sr)).sum())) if hit.size else 0
    clicks = ev / max(n / sr / 60.0, 1e-9)

    up = sg.resample_poly(m, 4, 1)
    return dict(bands_db=db, sr=sr, dur=n / sr, clicks_per_min=round(clicks, 2),
                tp_db=float(20 * np.log10(max(np.abs(up).max(), 1e-12))),
                peak=float(np.abs(x).max()), dc=float(abs(x.mean(axis=0)).max()),
                clip_pct=float((np.abs(x) >= 0.9985).sum() / x.size * 100))


def corrections(ref_db, tgt_db):
    """Koreksi per pita, dibatasi aturan karakter tiap pita."""
    out = []
    for (name, fc, q, cut_max, boost_max), r, t in zip(BANDS, ref_db, tgt_db):
        d = r - t
        if abs(d) < DEADBAND_DB:
            g = 0.0
        elif d > 0:
            g = min(d, boost_max)
        else:
            g = -min(-d, cut_max)
        out.append(dict(nama=name, f=fc, q=q, selisih=round(d, 2), gain=round(g, 2)))
    return out


def process(src: Path, dst: Path, corr, info, args) -> list[str]:
    why, filt = [], []

    if info["dc"] > 0.005:
        filt.append("highpass=f=20:poles=2")
        why.append(f"DC {info['dc']:.4f} -> high-pass 20 Hz (di bawah sub, kick utuh)")
    if info["clip_pct"] >= 0.05:
        filt.append("adeclip")
        why.append(f"clipping {info['clip_pct']:.3f}% -> adeclip (rekonstruksi puncak)")
    ck = info.get("clicks_per_min", 0.0)
    if args.declick:
        filt.append("adeclick")
        why.append(f"indeks klik {ck:.1f}/mnt -> adeclick (diminta lewat --declick)")
    else:
        why.append(f"indeks klik {ck:.1f}/mnt -> adeclick TIDAK dipasang (default)")

    used = [c for c in corr if abs(c["gain"]) >= 0.1]
    for c in used:
        filt.append(f"equalizer=f={c['f']}:width_type=q:width={c['q']}:g={c['gain']}")
        arah = "angkat" if c["gain"] > 0 else "potong"
        why.append(f"{c['nama']:<7} {c['f']:>6.0f} Hz  selisih {c['selisih']:+.2f} dB "
                   f"-> {arah} {c['gain']:+.2f} dB")
    if not used:
        why.append("tidak ada pita yang melewati ambang -- EQ tidak dipasang sama sekali")

    # Pengaman true peak: trim gain datar saja, BUKAN limiter.
    trim = 0.0
    if filt:
        with tempfile.TemporaryDirectory() as td:
            probe = Path(td) / "p.wav"
            run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", ",".join(filt),
                 "-c:a", "pcm_f32le", str(probe)])
            tp = spectrum(probe)["tp_db"]
        if tp > TP_CEILING:
            trim = round(TP_CEILING - tp, 2)
            filt.append(f"volume={trim}dB")
            why.append(f"true peak sesudah EQ {tp:+.2f} dBTP -> trim gain datar {trim:+.2f} dB "
                       "(tanpa limiter, dinamika tidak disentuh)")

    br = info.get("bitrate") or 320
    run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-i", str(src),
         "-map", "0:a:0", "-map_metadata", "1",
         *(["-af", ",".join(filt)] if filt else []),
         "-c:a", "libmp3lame", "-b:a", f"{max(192, min(320, br))}k",
         "-ar", str(info["sr"]), "-id3v2_version", "3", str(dst)])
    return why


def bitrate(path: Path) -> int:
    p = run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=bit_rate:format=bit_rate", "-of", "json", str(path)])
    d = json.loads(p.stdout)
    b = (d.get("streams") or [{}])[0].get("bit_rate") or (d.get("format") or {}).get("bit_rate") or 0
    return int(round(int(b) / 1000)) if int(b) else 320


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--ref", type=Path, required=True, help="lagu acuan karakter")
    ap.add_argument("--declick", action="store_true",
                    help="pasang adeclick. Mati secara default: lihat catatan di atas")
    a = ap.parse_args()
    require()

    src, out, ref = (a.source.expanduser().resolve(), a.output.expanduser().resolve(),
                     a.ref.expanduser().resolve())
    if not ref.is_file():
        sys.exit(f"ERROR: acuan tidak ada: {ref}")
    if not src.is_dir():
        sys.exit(f"ERROR: folder sumber tidak ada: {src}")
    if out == src or src in out.parents:
        sys.exit("ERROR: folder hasil harus di luar folder sumber.")

    files = sorted([p for p in src.iterdir()
                    if p.is_file() and p.suffix.lower() in {".mp3", ".wav", ".flac", ".m4a"}],
                   key=lambda p: p.name.lower())
    if not files:
        sys.exit(f"ERROR: tidak ada berkas audio di {src}")

    print(f"ACUAN  : {ref.name}\nSUMBER : {src}  (hanya dibaca)\nHASIL  : {out}\n")
    R = spectrum(ref)
    print("profil acuan (dB relatif total energi):")
    for (nm, *_), v in zip(BANDS, R["bands_db"]):
        print(f"   {nm:<7} {v:+7.2f}")
    print()

    out.mkdir(parents=True, exist_ok=True)
    log = [f"ACUAN  : {ref.name}", f"SUMBER : {src}", f"HASIL  : {out}", "",
           "profil acuan: " + "  ".join(f"{nm}={v:+.2f}" for (nm, *_), v
                                        in zip(BANDS, R["bands_db"])), ""]
    rows = []
    for i, p in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {p.name}")
        T = spectrum(p); T["bitrate"] = bitrate(p)
        corr = corrections(R["bands_db"], T["bands_db"])
        dst = out / p.name
        why = process(p, dst, corr, T, a)
        A = spectrum(dst)

        before = sum(abs(r - t) for r, t in zip(R["bands_db"], T["bands_db"])) / len(BANDS)
        after = sum(abs(r - t) for r, t in zip(R["bands_db"], A["bands_db"])) / len(BANDS)
        dur_ok = abs(A["dur"] - T["dur"]) < 0.05
        rows.append((p.name, before, after, T["tp_db"], A["tp_db"], dur_ok))
        for w in why:
            print(f"      - {w}")
        print(f"      = jarak ke acuan {before:.2f} -> {after:.2f} dB rata-rata | "
              f"durasi {T['dur']:.1f}s -> {A['dur']:.1f}s {'OK' if dur_ok else 'BERUBAH!'}")
        log += [f"[{i}] {p.name}"] + [f"      - {w}" for w in why]
        log += [f"      = jarak ke acuan {before:.2f} -> {after:.2f} dB | "
                f"durasi {T['dur']:.1f} -> {A['dur']:.1f} s "
                f"({'utuh' if dur_ok else 'BERUBAH'})", ""]

    log += ["=" * 72, "RINGKASAN QC", "=" * 72,
            f"{'berkas':<30}{'jarak sblm':>11}{'ssdh':>8}{'dBTP':>9}{'durasi':>9}"]
    for nm, b, aft, tp0, tp1, ok in rows:
        log.append(f"{nm[:28]:<30}{b:>11.2f}{aft:>8.2f}{tp1:>+9.2f}"
                   f"{'utuh' if ok else 'BERUBAH':>9}")
    log += ["", "Tidak dipakai: compressor, limiter, loudness normalization.",
            "Tidak ada pemotongan: durasi tiap lagu utuh.",
            "Sumber tidak diubah."]
    (out / "_QC_REPORT.txt").write_text("\n".join(log), encoding="utf-8")
    print(f"\nSelesai: {len(rows)} berkas. QC: {out / '_QC_REPORT.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
