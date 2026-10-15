#!/usr/bin/env python3
"""
funkot_restore.py -- apply the repairs that funkot_analyze.py measured.

Every filter is switched on by a measurement in the analysis JSON, so nothing
is applied "just in case". Tempo, pitch, key and groove are never touched: no
filter in this chain time-stretches or resamples the musical content.

    python funkot_analyze.py "<SUMBER>" -o analisis.json
    python funkot_restore.py "<SUMBER>" "<HASIL>" -j analisis.json

Source folder is read-only. Output goes to a new folder.
"""

from __future__ import annotations

import argparse, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path

TARGET_LUFS = -9.0      # club/DJ level; set with --lufs
TARGET_TP   = -1.0      # dBTP ceiling, leaves room for MP3 encoding
TARGET_LRA  = 11.0


def run(cmd, quiet=True):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0 and quiet:
        raise RuntimeError(f"gagal: {' '.join(cmd[:5])}...\n{p.stderr[-1500:]}")
    return p


def require():
    if shutil.which("ffmpeg") is None:
        sys.exit("ERROR: ffmpeg tidak ada di PATH.")


def build_chain(d: dict, args) -> tuple[list[str], list[str]]:
    """Return (filters, reasons). Each filter is justified by a measurement."""
    f, why = [], []

    if abs(d.get("dc_offset", 0)) > 0.005:
        f.append("highpass=f=20:poles=2")
        why.append(f"DC {d['dc_offset']:.4f} -> high-pass 20 Hz "
                   "(di bawah sub-bass funkot, groove tidak tersentuh)")

    for hz, lvl in sorted(d.get("hum", {}).items(), key=lambda kv: -kv[1]):
        if lvl >= 6.0:
            g = max(-12.0, -round(min(lvl - 2.0, 12.0), 1))
            f.append(f"equalizer=f={hz}:width_type=q:width=30:g={g}")
            why.append(f"dengung {hz} Hz +{lvl:.1f} dB -> notch Q30 {g:+.1f} dB")

    if d.get("clicks_per_min", 0) >= 5.0:
        f.append("adeclick")
        why.append(f"indeks klik {d['clicks_per_min']:.1f}/mnt -> adeclick")

    if d.get("clip_pct", 0) >= 0.05 or d.get("flat_runs", 0) > 0:
        f.append("adeclip")
        why.append(f"clipping {d['clip_pct']:.3f}% -> adeclip (rekonstruksi puncak)")

    nf = d.get("noise_floor_db", -120)
    if nf > -60.0 and not args.no_denoise:
        # Measured end-to-end on material with a -49 dBFS floor, noise taken
        # relative to programme level so the loudness pass cannot flatter it:
        #   nr=12 -> 4.0 dB cleaner, nr=18 -> 7.1 dB, nr=24 -> 8.8 dB.
        # 18 is the default because it lands in the 6-9 dB window; the 6-14 kHz
        # band did not lose energy at any of the three. "tn=1" (noise tracking)
        # measurably made it worse, so it is deliberately off.
        # Caveat worth stating: a band measurement cannot see musical-noise
        # artefacts. Where the ceiling really is, is an ear judgement.
        nfc = int(max(-80, min(-20, round(nf) + 4)))
        f.append(f"afftdn=nf={nfc}:nr={args.denoise_db}")
        why.append(f"noise floor {nf:.1f} dBFS -> afftdn nf={nfc} nr={args.denoise_db}")

    if d.get("mud_index", 0) > 0.42:
        f.append("equalizer=f=350:width_type=q:width=1.0:g=-2.5")
        why.append(f"mud {d['mud_index']*100:.0f}% -> cut -2.5 dB @350 Hz Q1.0")

    lpf = d.get("lowpass_hz", 20000)
    if d.get("air_index", 1) < 0.02 and lpf >= 14000:
        f.append("treble=f=8000:width_type=q:width=0.7:g=2")
        why.append(f"air {d['air_index']*100:.1f}% -> high-shelf +2 dB @8 kHz "
                   f"(di bawah batas {lpf/1000:.1f} kHz, tidak mengangkat artefak)")
    elif d.get("air_index", 1) < 0.02:
        why.append(f"TIDAK ada boost treble: batas pita hanya {lpf/1000:.1f} kHz, "
                   "menaikkan di sana hanya mengangkat artefak codec")

    if args.mono_bass:
        f.append("asplit=2[lo][hi];[lo]lowpass=f=120:poles=2,"
                 "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1[lom];"
                 "[hi]highpass=f=120:poles=2[hip];[lom][hip]amix=inputs=2:normalize=0")
        why.append("mono-bass <120 Hz (diminta lewat --mono-bass)")

    if d.get("crest_db", 99) < 8.0:
        why.append(f"TIDAK ada kompresi: crest {d['crest_db']:.1f} dB, "
                   "materi sudah padat; compressor hanya menghasilkan pumping")
    return f, why


def measure_loudnorm(src: Path, pre: list[str], args) -> dict:
    """Pass 1 of loudnorm: measure, so pass 2 can correct linearly."""
    chain = pre + [f"loudnorm=I={args.lufs}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json"]
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src),
             "-af", ",".join(chain), "-f", "null", "-"], quiet=False)
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr, re.S)
    if not m:
        raise RuntimeError("loudnorm pass-1 tidak mengembalikan hasil ukur")
    return json.loads(m[-1])


def restore(src: Path, dst: Path, d: dict, args) -> list[str]:
    filters, why = build_chain(d, args)
    st = measure_loudnorm(src, filters, args)
    ln = (f"loudnorm=I={args.lufs}:TP={TARGET_TP}:LRA={TARGET_LRA}"
          f":measured_I={st['input_i']}:measured_TP={st['input_tp']}"
          f":measured_LRA={st['input_lra']}:measured_thresh={st['input_thresh']}"
          f":offset={st['target_offset']}:linear=true:print_format=summary")
    why.append(f"loudness {st['input_i']} -> {args.lufs} LUFS, "
               f"true peak {st['input_tp']} -> {TARGET_TP} dBTP (loudnorm 2 lintasan, linear)")
    why.append("alimiter -1.0 dBFS sebagai pengaman terakhir")

    chain = filters + [ln, "alimiter=limit=0.891:level=disabled"]
    br = d.get("bitrate_kbps") or 320
    br = max(192, min(320, br if br >= 192 else 320))
    args_enc = ["-c:a", "libmp3lame", "-b:a", f"{br}k"] if dst.suffix.lower() == ".mp3" \
        else ["-c:a", "flac"]
    run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-i", str(src),
         "-map", "0:a:0", "-map_metadata", "1", "-af", ",".join(chain),
         "-ar", str(d.get("sr") or 44100), *args_enc, "-id3v2_version", "3", str(dst)])
    return why


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("-j", "--json", type=Path, default=Path("analisis_funkot.json"))
    ap.add_argument("--lufs", type=float, default=TARGET_LUFS)
    ap.add_argument("--mono-bass", action="store_true")
    ap.add_argument("--no-denoise", action="store_true")
    ap.add_argument("--denoise-db", type=int, default=18,
                    help="afftdn nr. Terukur: 12=4.0dB, 18=7.1dB, 24=8.8dB lebih bersih")
    ap.add_argument("--flac", action="store_true", help="keluarkan FLAC, bukan MP3")
    a = ap.parse_args()
    require()

    src, out = a.source.expanduser().resolve(), a.output.expanduser().resolve()
    if not src.is_dir():
        sys.exit(f"ERROR: folder sumber tidak ada: {src}")
    if out == src or src in out.parents:
        sys.exit("ERROR: folder hasil harus di luar folder sumber.")
    if not a.json.exists():
        sys.exit(f"ERROR: {a.json} tidak ada. Jalankan funkot_analyze.py dulu.")

    diags = {d["file"]: d for d in json.loads(a.json.read_text(encoding="utf-8"))}
    out.mkdir(parents=True, exist_ok=True)
    log = [f"SUMBER : {src}  (hanya dibaca)", f"HASIL  : {out}",
           f"TARGET : {a.lufs} LUFS / {TARGET_TP} dBTP", ""]
    print("\n".join(log))

    done = 0
    for i, (name, d) in enumerate(diags.items(), 1):
        p = src / name
        if not p.exists():
            print(f"[{i}] LEWAT {name}: tidak ada di folder sumber"); continue
        dst = out / (Path(name).stem + (".flac" if a.flac else ".mp3"))
        print(f"[{i}/{len(diags)}] {name} ...", flush=True)
        why = restore(p, dst, d, a)
        done += 1
        log.append(f"[{i}] {name}  ->  {dst.name}")
        log += [f"      - {w}" for w in why] + [""]
        for w in why:
            print(f"      - {w}")

    log.append(f"Selesai: {done} berkas diproses. Sumber tidak diubah.")
    (out / "_LOG_PROSES.txt").write_text("\n".join(log), encoding="utf-8")
    print(f"\nSelesai: {done} berkas. Log: {out / '_LOG_PROSES.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
