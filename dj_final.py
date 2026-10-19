#!/usr/bin/env python3
"""
dj_final.py -- satu perintah untuk seluruh folder: samakan karakter ke lagu
acuan, lalu rapikan awal/akhir tiap lagu supaya OUTRO lagu A menyambung ke
PLAY lagu B secara natural.

Dirakit dari kode yang sudah diuji terpisah: pencocokan pita dari
funkot_match.py dan grid bar + QC sambungan dari dj_cutter.py. Yang baru di
sini hanya perakitannya, dan satu hal yang penting -- seluruh tahap dikerjakan
pada WAV float, MP3 di-encode SEKALI di akhir. Menjalankan dua perkakas
berurutan akan menghasilkan dua generasi MP3 dan kerugian kualitas yang
sia-sia.

    python dj_final.py --ref "CLASSIC_4_FINAL_V4.mp3" "DJ CUTTER" "DJ_CUTTER_FINAL_V1"

Sumber hanya dibaca. Jumlah berkas keluar dipastikan sama dengan masuk.
"""

from __future__ import annotations

import argparse, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MAX_TRIM_BARS = 2.0        # pangkas lebih dari ini = dilaporkan, bukan dikerjakan diam-diam
AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"gagal: {' '.join(cmd[:5])}...\n{p.stderr[-1200:]}")
    return p


def require():
    if shutil.which("ffmpeg") is None:
        sys.exit("ERROR: ffmpeg tidak ada di PATH.")
    try:
        import numpy, scipy, soundfile, librosa  # noqa: F401
    except ImportError as e:
        sys.exit(f"ERROR: paket Python kurang ({e.name}).")


def track_no(p: Path):
    m = re.search(r"\d+", p.stem)
    return int(m.group()) if m else None


def ordered(src: Path) -> list[Path]:
    f = [p for p in src.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXT]
    if not f:
        sys.exit(f"ERROR: tidak ada berkas audio di {src}")
    return sorted(f, key=lambda p: (track_no(p) is None, track_no(p) or 0, p.name.lower()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--ref", type=Path, help="lagu acuan karakter (opsional)")
    ap.add_argument("--no-seam", action="store_true", help="lewati perapian sambungan")
    ap.add_argument("--bitrate", type=int, default=0, help="0 = ikuti sumber")
    a = ap.parse_args()
    require()

    import numpy as np, soundfile as sf, librosa
    import funkot_match as FM
    import dj_cutter as DC

    src, out = a.source.expanduser().resolve(), a.output.expanduser().resolve()
    if not src.is_dir():
        sys.exit(f"ERROR: folder sumber tidak ada: {src}")
    if out == src or src in out.parents:
        sys.exit("ERROR: folder hasil harus di luar folder sumber.")

    files = ordered(src)
    ref_db = None
    if a.ref:
        ref = a.ref.expanduser().resolve()
        if not ref.is_file():
            sys.exit(f"ERROR: acuan tidak ada: {ref}")
        ref_db = FM.spectrum(ref)["bands_db"]

    print(f"SUMBER : {src}  ({len(files)} berkas, hanya dibaca)")
    print(f"ACUAN  : {a.ref.name if a.ref else '(tidak dipakai)'}")
    print(f"HASIL  : {out}\n")
    for p in files:
        n = track_no(p)
        print(f"  {n if n is not None else '--':>3}  {p.name}")
    print()

    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="djfinal_"))
    log = [f"SUMBER : {src}", f"ACUAN  : {a.ref.name if a.ref else '(tidak dipakai)'}",
           f"HASIL  : {out}", f"berkas sumber: {len(files)}", ""]
    rows, seams, written = [], [], []

    try:
        # ---- Tahap 1: ukur + samakan karakter, hasilnya WAV float ---------
        stage1, info = [], []
        for i, p in enumerate(files, 1):
            print(f"[1/{len(files)}] ukur+EQ  {p.name}", flush=True)
            T = FM.spectrum(p)
            T["bitrate"] = FM.bitrate(p)
            why, filt = [], []
            if T["dc"] > 0.005:
                filt.append("highpass=f=20:poles=2")
                why.append(f"DC {T['dc']:.4f} -> high-pass 20 Hz")
            if T["clip_pct"] >= 0.05:
                filt.append("adeclip")
                why.append(f"clipping {T['clip_pct']:.3f}% -> adeclip")
            if ref_db is not None:
                corr = FM.corrections(ref_db, T["bands_db"])
                used = [c for c in corr if abs(c["gain"]) >= 0.1]
                for c in used:
                    filt.append(f"equalizer=f={c['f']}:width_type=q:"
                                f"width={c['q']}:g={c['gain']}")
                    why.append(f"{c['nama']:<7}{c['f']:>6.0f} Hz selisih {c['selisih']:+.2f} "
                               f"-> {c['gain']:+.2f} dB")
                if not used:
                    why.append("karakter sudah sesuai acuan -- EQ tidak dipasang")
            w = tmp / f"s1_{i:03d}.wav"
            run(["ffmpeg", "-v", "error", "-y", "-i", str(p),
                 *(["-af", ",".join(filt)] if filt else []),
                 "-c:a", "pcm_f32le", "-f", "wav", str(w)])
            stage1.append(w); info.append((T, why))

        # ---- Tahap 2: rapikan awal/akhir di bar grid ----------------------
        cuts, anal = [], []
        for i, (p, w) in enumerate(zip(files, stage1), 1):
            x, sr = sf.read(str(w), dtype="float32", always_2d=False)
            mono = x.mean(axis=1) if x.ndim > 1 else x
            mono = np.ascontiguousarray(mono, dtype=np.float32)
            print(f"[2/{len(files)}] grid     {p.name}", flush=True)
            A = DC.analyze(mono, sr)
            if a.no_seam:
                h, t_, disc = 0.0, len(mono) / sr, 0.0
            else:
                h = DC.head_cut_time(A)
                t_, disc = DC.tail_cut_time(A)
                if t_ <= h:
                    h, t_ = 0.0, len(mono) / sr
                # Jaga durasi: pangkas berlebihan dilaporkan, tidak dikerjakan.
                lead, tail = h, len(mono) / sr - t_
                if lead > MAX_TRIM_BARS * A.bar_period:
                    info[i - 1][1].append(
                        f"PANGKAS AWAL DITOLAK: {lead:.2f}s > {MAX_TRIM_BARS} bar "
                        f"({MAX_TRIM_BARS * A.bar_period:.2f}s) -- awal dibiarkan utuh")
                    h = 0.0
                if tail > MAX_TRIM_BARS * A.bar_period:
                    info[i - 1][1].append(
                        f"PANGKAS AKHIR DITOLAK: {tail:.2f}s > {MAX_TRIM_BARS} bar "
                        f"-- akhir dibiarkan utuh")
                    t_ = len(mono) / sr
            cuts.append((h, t_)); anal.append(A)

        # ---- Tahap 3: potong, QC sambungan, encode MP3 SEKALI -------------
        prev_tail = None
        for i, (p, w, (h, t_), A) in enumerate(zip(files, stage1, cuts, anal), 1):
            x, sr = sf.read(str(w), dtype="float32", always_2d=False)
            seg = DC.apply_cut(x, sr, h, t_, DC.P.tail_fade_ms)
            m = seg.mean(axis=1) if seg.ndim > 1 else seg
            m = np.ascontiguousarray(m, dtype=np.float32)

            if prev_tail is not None:
                q = DC.check_seam(prev_tail, m, sr, anal[i - 2].beat_period,
                                  files[i - 2].name, p.name,
                                  anal[i - 2].bpm, A.bpm)
                seams.append(q)
            prev_tail = m

            cw = tmp / f"s3_{i:03d}.wav"
            sf.write(str(cw), seg, sr, subtype="FLOAT")
            T, why = info[i - 1]
            br = a.bitrate or max(192, min(320, T["bitrate"]))
            dst = out / (p.stem + ".mp3")
            run(["ffmpeg", "-v", "error", "-y", "-i", str(cw), "-i", str(p),
                 "-map", "0:a:0", "-map_metadata", "1",
                 "-c:a", "libmp3lame", "-b:a", f"{br}k", "-ar", str(sr),
                 "-id3v2_version", "3", str(dst)])
            written.append(dst)

            dur_in, dur_out = T["dur"], (t_ - h)
            rows.append((p.name, dur_in, dur_out, h, dur_in - t_, A.bpm))
            print(f"[3/{len(files)}] tulis    {dst.name}  "
                  f"{dur_in:.1f}s -> {dur_out:.1f}s (awal -{h*1000:.0f}ms, "
                  f"akhir -{(dur_in-t_)*1000:.0f}ms)")
            log += [f"[{i}] {p.name}  ->  {dst.name}"]
            log += [f"      - {x_}" for x_ in why]
            log += [f"      = durasi {dur_in:.2f}s -> {dur_out:.2f}s | "
                    f"BPM {A.bpm:.2f} | pangkas awal {h*1000:.0f} ms, "
                    f"akhir {(dur_in-t_)*1000:.0f} ms", ""]

        # ---- laporan QC ----------------------------------------------------
        log += ["=" * 76, "RINGKASAN", "=" * 76,
                f"{'berkas':<30}{'durasi in':>11}{'out':>9}{'awal':>9}{'akhir':>9}{'BPM':>8}"]
        for nm, di, do, hh, tt, bpm in rows:
            log.append(f"{nm[:28]:<30}{di:>11.2f}{do:>9.2f}{hh*1000:>8.0f}m"
                       f"{tt*1000:>8.0f}m{bpm:>8.2f}")
        log += ["", "SAMBUNGAN OUTRO -> PLAY", "-" * 76]
        bad = 0
        for s in seams:
            ok = s.ok
            bad += 0 if ok else 1
            log.append(f"{'OK  ' if ok else 'FLAG'}  {s.left}  ->  {s.right}")
            log.append(f"        jarak kick {s.min_seam_ioi_ms:.1f} ms "
                       f"(beat {s.expected_beat_ms:.1f} ms) | "
                       f"double kick {'YA' if s.double_kick else 'tidak'} | "
                       f"beat lompat {'YA' if s.beat_jump else 'tidak'} | "
                       f"dead air {s.dead_air_ms:.1f} ms | "
                       f"BPM drift {s.bpm_drift_pct:.2f}%")
            for n_ in s.notes:
                log.append(f"        catatan: {n_}")
        log += ["", f"berkas sumber : {len(files)}", f"berkas hasil  : {len(written)}",
                f"sambungan     : {len(seams)} ({len(seams)-bad} bersih, {bad} perlu dicek)",
                "", "Tidak memakai compressor, limiter, atau loudness normalization.",
                "MP3 di-encode sekali saja (seluruh tahap dikerjakan pada WAV float).",
                "Sumber tidak diubah."]
        (out / "_QC_REPORT.txt").write_text("\n".join(log), encoding="utf-8")

        print("\n" + "\n".join(log[-10:]))
        if len(written) != len(files):
            print(f"\n!! PERINGATAN: {len(files)} masuk tapi {len(written)} keluar")
            return 1
        print(f"\nSelesai: {len(written)}/{len(files)} berkas. QC: {out / '_QC_REPORT.txt'}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
