# DJ-Cutter

Dua perkakas untuk menyiapkan kumpulan lagu DJ. Keduanya membaca folder sumber
**read-only** dan menulis ke folder baru.

| Perkakas | Fungsi |
|---|---|
| `dj_cutter.py` | Merapikan sambungan OUTRO → PLAY antar track pada bar grid |
| `funkot_analyze.py` + `funkot_restore.py` | Restorasi & cleanup audio per lagu |

Keduanya butuh FFmpeg di PATH (`winget install Gyan.FFmpeg` di Windows) dan:

```bash
pip install -r requirements.txt
```

---

# 1. Restorasi audio (funkot)

```bash
python funkot_analyze.py "Funkot 001" -o analisis.json
python funkot_restore.py "Funkot 001" "Funkot 001 CLEAN" -j analisis.json
```

Opsi: `--lufs -9` · `--denoise-db 24` · `--no-denoise` · `--mono-bass` · `--flac`

Tiap filter dinyalakan oleh sebuah **hasil ukur**, bukan dipasang jaga-jaga, dan
`_LOG_PROSES.txt` mencatat alasan tiap keputusan. Yang tidak diubah: tempo,
pitch, key, groove — tidak ada filter dalam rantai ini yang melakukan
time-stretch atau resample materi musiknya.

Rantai: buang DC → notch dengung → declick → declip → denoise → EQ bedah →
loudness 2 lintasan → ceiling true-peak.

**Kompresor sengaja tidak dipakai.** Funkot umumnya sudah sangat padat di
sumbernya (crest < 8 dB); menambah kompresi di materi seperti itu menghasilkan
pumping, bukan punch.

### Sudah divalidasi

Diuji pada materi sintetis 128 kbps yang kerusakannya diketahui persis.

| Hal | Hasil |
|---|---|
| Batas pita MP3 | terdeteksi 16.9 kHz pada sumber 128 kbps |
| Dengung 50 Hz | +18.2 → +6.8 dB setelah notch; urutan antar lagu benar |
| Clipping | 0.402 / 0.241 / 0.073 % → 0.000 % |
| True peak | +1.2…+2.4 → semua ≤ −1.00 dBTP |
| DC offset | 0.0132 → 0.0003 |
| Sebaran loudness | 4.1 dB → 0.8 dB antar lagu |
| Durasi | tidak berubah — tempo/pitch utuh |
| Denoise | nr=12 → 4.0 dB · nr=18 → 7.1 dB · nr=24 → 8.8 dB lebih bersih |
| Sumber | checksum identik setelah diproses |

### Batasnya, supaya jujur

- **Indeks klik bersifat indikatif.** Terhadap posisi klik yang diketahui pada
  materi 128 kbps: urutan antar lagu benar dan nyaris tanpa false positive,
  tetapi hanya menemukan sekitar separuh klik — encoder lossy menghapus jejak
  impuls satu-sampel. Cukup untuk memutuskan perlu declick atau tidak.
- **Artefak "musical noise" tidak terukur** oleh pengukuran pita. Sampai di mana
  denoise boleh didorong adalah penilaian telinga, bukan angka.
- **Pita di atas batas LPF tidak bisa dikembalikan.** Kalau sumbernya 128 kbps,
  tidak ada "air" di atas ~16.9 kHz untuk diangkat. Skrip menolak memasang
  shelf treble di kasus itu dan mengatakan alasannya.
- Normalisasi lagu yang lebih dinamis ke target bisa memangkas crest beberapa
  dB. Log mencatat angkanya; kalau terlalu banyak, naikkan `--lufs`.

---

# 2. Sambungan OUTRO → PLAY

```bash
python dj_cutter.py "C:\path\sumber" "C:\path\hasil"
python dj_cutter.py "C:\path\sumber" "C:\path\hasil" --dry-run
```

Di Windows bisa juga klik dua kali `Run Ffonkot 003.bat` (path sudah terisi).

Memotong ekor tiap track dan kepala track berikutnya supaya serah terimanya
jatuh di bar grid 4/4: satu kick di sambungan, beat tidak lompat, attack tidak
terpotong, tidak ada dead air. Track tetap file terpisah.

Urutan diambil dari **nomor pada nama berkas**, bukan urutan folder: `01, 2, 03,
10` diproses 1, 2, 3, 10 walau Explorer menampilkannya `01, 03, 10, 2`. Nomor
yang hilang atau ganda adalah error, bukan tebakan.

Hasilnya menulis `_QC_REPORT.txt`: jarak kick di tiap sambungan, vonis double
kick dan beat jump, dead air, dan selisih BPM. Sambungan yang perlu didengar
ditandai `FLAG`.

Grid-nya satu fit global `t_k = phi + k*period` terhadap flux onset pita kick,
lewat koefisien Fourier envelope pada tiap kandidat periode — tidak melenceng
sepanjang lagu seperti estimasi median-of-intervals berantai.

Divalidasi pada materi 4/4 sintetis dengan posisi kick diketahui: tempo persis,
64/64 kick terdeteksi tanpa false positive, potongan ekor mendarat di bar line
dalam 5 ms, dan sambungan berjarak 1.03–1.04 beat diukur pada hasil gabungan
memakai detektor yang ditulis terpisah.
