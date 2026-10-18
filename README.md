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

# 2. Memecah DJ set jadi track (`dj_split.py`)

```bash
python dj_split.py "MIX.mp3" -o kandidat.json
```

**Deteksi saja — tidak pernah memotong.** Keluarannya daftar kandidat titik
potong beserta keyakinannya, untuk diperiksa dan disetujui dulu. Di DJ set dua
lagu sering tumpang tindih 30–60 detik, jadi batasnya penilaian, bukan fakta.

Caranya: fitur beat-synchronous (MFCC + chroma) → self-similarity matrix →
novelty Foote, lalu tiap kandidat diuji empat hal — jarak timbre, jarak
harmoni, pergeseran BPM, dan **apakah materi kembali** sesudahnya. Yang terakhir
itu pembeda breakdown dari ganti lagu: sesudah breakdown musik balik ke lagu
yang sama, sesudah ganti lagu tidak. Titik akhirnya di-snap ke bar line.

### Hasil ukur pada dua mix uji yang batasnya diketahui persis

| | Mix 1 (tanpa breakdown) | Mix 2 (ada breakdown di dalam lagu) |
|---|---|---|
| Ganti lagu ditemukan | **4 / 4** | **3 / 3** |
| False positive | 0 | 3 (semuanya breakdown) |
| Error waktu | ≤ 6,9 s, **semua di dalam crossfade** | ≤ 6,6 s |
| Rentang keyakinan (benar) | 0,65 – 0,92 | 0,44 – 0,82 |
| Rentang keyakinan (palsu) | — | 0,27 – 0,65 |

**Recall 7/7, presisi 7/10.**

### Batasnya, supaya jujur

- **Keyakinan tidak memisahkan bersih.** Di Mix 2 satu ganti lagu asli (0,44)
  skornya di bawah dua breakdown (0,65 dan 0,57). Angka itu bahan pertimbangan,
  bukan vonis — persis sebabnya perkakas ini tidak memotong sendiri.
- Dua pendekatan dicoba dan **terukur gagal** sebelum yang sekarang: menekan
  peak dengan jarak minimum panjang lagu membuat breakdown membunuh batas asli
  59 detik di sebelahnya, dan membandingkan rata-rata MFCC mentah tidak punya
  daya pisah sama sekali (semua kandidat 0,96–1,00) karena bentuk spektrum
  rata-rata lagu dance 4/4 mana pun serupa.
- Grid bar di-fit sekali untuk seluruh mix. Kalau BPM bergeser jauh sepanjang
  set, snap bar di ujung bisa meleset.
- Waktu proses ±55 detik untuk mix 11 menit.

---

# 3. Sambungan OUTRO → PLAY

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
