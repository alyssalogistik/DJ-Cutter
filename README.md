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

# 2. Satu perintah untuk seluruh folder (`dj_final.py`)

```bash
python dj_final.py --ref "CLASSIC_4_FINAL_V4.mp3" "DJ CUTTER" "DJ_CUTTER_FINAL_V1"
```

Merangkai dua tahap yang sudah diuji terpisah — pencocokan pita dari
`funkot_match.py` dan grid bar + QC sambungan dari `dj_cutter.py` — lalu
menulis satu MP3 per lagu plus satu `_QC_REPORT.txt`.

Satu hal yang hanya ada di sini dan penting: **seluruh tahap dikerjakan pada WAV
float, MP3 di-encode sekali di akhir.** Menjalankan dua perkakas berurutan
menghasilkan dua generasi MP3 dan kerugian kualitas yang sia-sia.

Penjaga durasi: pemangkasan awal atau akhir yang melebihi 2 bar **ditolak dan
dilaporkan**, tidak dikerjakan diam-diam.

### Hasil ukur pada 18 berkas uji

| | |
|---|---|
| Berkas masuk → keluar | **18 → 18** |
| Kick pertama tepat di awal | **17 / 18** |
| Sambungan bersih | 16 / 17 |
| Durasi | hanya hening awal/akhir yang dipangkas, 0,6–1,8 s |
| Checksum sumber | 18 / 18 identik |
| Waktu proses | ±90 detik |

Satu sambungan di-flag karena beat lompat, dan memang benar: kedua lagu beda
BPM 1,59 %. Tanpa time-stretch itu tidak bisa diselesaikan, dan perkakas ini
melaporkannya alih-alih diam-diam menambal.

### Catatan: satu perbaikan sempat membuat keadaan lebih buruk

Pada set 18 lagu itu, satu lagu kehilangan kick pertamanya — fase bar jatuh
satu beat terlambat dan lagunya dibuka pada kick KEDUA, 460 ms lewat. Perbaikan
pertama mengganti jangkar head cut dari downbeat ke kick pertama hasil deteksi,
dan hasilnya **lebih buruk**: event lemah selama fade-in terbaca sebagai kick
pertama, sehingga hening awal tidak pernah dipangkas. Kick tepat di awal turun
dari 16/18 jadi 9/18, dan sambungan ter-flag naik dari 1 jadi 8.

Yang dipakai sekarang penjaga sempit: tetap pakai downbeat, dan mundur satu bar
**hanya** kalau ada kick yang benar-benar kuat sebelumnya. Hasilnya 17/18.
Keduanya dicatat di kode supaya tidak diulang.

---

# 3. Mencocokkan karakter ke lagu acuan (`funkot_match.py`)

```bash
python funkot_match.py --ref "CLASSIC_4_FINAL_V4.mp3" "DJ CUTTER" "DJ_CUTTER_FINAL_V1"
```

Acuan **diukur, bukan ditebak**: keseimbangan spektrum jangka panjang lagu acuan
diambil per pita (dinormalkan ke total energi supaya beda loudness tidak bocor
jadi beda warna), lalu tiap target dikoreksi menuju angka itu.

Aturan karakter tertanam di tabel pita: badan kick (70 Hz) dan kehangatan
(140 Hz) boleh diangkat sampai +2,5 dB tapi pelit dipotong, sementara pita
tajam (3,8 kHz) **tidak pernah diangkat sama sekali**. Koreksi dibatasi 2–3 dB
per pita, dan pita yang selisihnya di bawah 0,8 dB tidak disentuh.

Tidak memakai compressor, limiter, atau loudness normalization. Kalau true peak
melewati −1 dBTP sesudah EQ, yang dipasang hanya trim gain datar — dinamika
tidak disentuh. Durasi tidak pernah dipotong.

### Hasil ukur pada acuan + tiga target yang karakternya diketahui

| Target | Jarak ke acuan sebelum | sesudah | Durasi |
|---|---|---|---|
| Tajam & tipis | 5,76 dB | 4,27 dB | utuh |
| Boomy & tumpul | 1,97 dB | **0,74 dB** | utuh |
| Kontrol (identik acuan) | 0,00 dB | **0,02 dB** | utuh |

Kontrol itu ujian terpentingnya: lagu yang sudah benar **tidak di-EQ sama
sekali** dan keluar nyaris identik. Target "tajam & tipis" sengaja hanya
diperbaiki sebagian — selisihnya sampai −12,5 dB di satu pita sementara batas
koreksi 3 dB. Perkakas ini memilih kurang mengoreksi daripada melakukan operasi
besar pada musiknya.

### `adeclick` mati secara default — ini keputusan hasil ukur

Pada materi yang sudah bersih, memasang `adeclick` menggeser keseimbangan pita
**1,43 dB**, sementara re-encode MP3 murni hanya **0,018 dB**. Filter itu tidak
gratis. Dan indeks klik tidak cukup andal untuk jadi ambang otomatis: pada
materi 320 kbps dengan hi-hat dan clap tajam ia membaca 28/menit walau nol klik
disuntikkan. Indeksnya tetap dilaporkan untuk perbandingan antar lagu, dan
`--declick` tersedia kalau telinga memang mendengar crackle.

---

# 4. Memecah DJ set jadi track (`dj_split.py`)

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

# 5. Sambungan OUTRO → PLAY

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
