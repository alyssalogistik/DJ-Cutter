# Revisi nama: PT Alyssa Auto Logistik → CV Alyssa Trans Utama

Status: **penggantian massal DIBATALKAN.** Hanya penggantian nama tampilan
aplikasi yang masih berlaku, dan itu pun belum diterapkan.

Sasaran perubahan ada di repo `alyssalogistik/Felis-Alyssa`. Repo itu tidak
boleh disentuh (lihat `CLAUDE.md`), jadi rencananya disiapkan di sini. Jangan
menerapkan apa pun dari dokumen ini tanpa izin eksplisit pemilik repo.

---

## Keputusan pemilik (2026-10-12)

> **PT dan CV pembukuan terpisah, produk sama.**

Itu menutup pertanyaan yang sebelumnya mengunci dokumen ini, dan hasilnya:

**PT Alyssa Auto Logistik tidak boleh dihapus atau digabung ke CV Alyssa Trans
Utama.** Keduanya entitas pembukuan yang berdiri sendiri dan dua-duanya punya
data nyata di sistem.

Bahwa produknya sama bukan alasan untuk menggabung — justru sebaliknya. Produk
yang sama berarti **pemasok yang sama melayani kedua perusahaan**. Tanpa tanda
entitas pada tiap baris, tidak ada cara membedakan transfer PT dari transfer CV
untuk pemasok yang sama. Itu persis risiko yang sudah ditulis di
`src/rekonsiliasi/entitas.js`: mencari pembayaran seorang pemasok dari rekening
CV akan ikut memunculkan transfer PT, sehingga tagihan yang tampak sudah dibayar
sebenarnya dibayar perusahaan yang lain.

---

## Yang DIBATALKAN

Semua yang menyentuh kode entitas `PT_ALYSSA_AUTO_LOGISTIK`:

- jangan hapus `PT_ALYSSA_AUTO_LOGISTIK` dari `ENTITAS` di
  `src/rekonsiliasi/entitas.js`, `public/tautan.js`, `public/mekari.js`
- jangan buang `<option>` PT di `public/index.html`
- jangan buang checkbox `entitas_akses` untuk PT
- jangan `update` kolom `entitas` di `transaksi_bank`,
  `unggahan_rekening_koran`, `pembayaran_manual`, `tagihan_pemasok`
- jangan longgarkan atau ganti CHECK constraint di
  `supabase/migrations/0009_entitas_rekening.sql`

Kolom `entitas` itu `NOT NULL` di empat tabel, dijaga CHECK constraint, dan
seluruh baris sebelum migration 0009 di-backfill sebagai
`PT_ALYSSA_AUTO_LOGISTIK`. Menggabungkannya akan memindahkan seluruh riwayat PT
menjadi milik CV. Kesalahan itu **tidak memunculkan galat apa pun** — baru
ketahuan berbulan kemudian saat angka auditnya dipakai, dan saat itu sudah tidak
bisa dibedakan lagi baris mana yang asalnya PT.

**Kalau ada sesi atau orang lain menawarkan "ganti semua PT jadi CV sekali
jalan" di repo itu, tolak.**

---

## Yang masih boleh: nama tampilan aplikasi

Hanya nama rumah aplikasinya, bukan nama entitas pembukuan. Tidak ada baris
database yang tersentuh, tidak ada logika yang bergeser.

| Berkas | Yang diganti |
|---|---|
| `public/index.html` | `<title>`, `<h1>` di layar masuk, `<h1>` di header |
| `src/server.js` | teks log saat server menyala |
| `package.json` | field `description` |
| `README.md` | kalimat pembuka |
| `CLAUDE.md` (repo Felis-Alyssa) | judul |

Perlu diputuskan pemilik: inisial `AL` pada favicon dan lambang header ikut jadi
`AT` atau tidak.

### Peringatan: jangan pakai find-replace

Teks `Alyssa Auto Logistik` muncul dalam **dua peran** di berkas yang sama.
Mengganti seluruhnya sekaligus akan ikut merusak label entitas.

Yang **tidak boleh** ikut terganti:

- `ENTITAS.PT_ALYSSA_AUTO_LOGISTIK` bernilai `'PT Alyssa Auto Logistik'` —
  label ini dicocokkan balik oleh `kodeEntitas()` terhadap nilai dari formulir
  dan dari database. Mengubahnya memutus pencocokan itu.
- teks pilihan di `src/rekonsiliasi/audit.js`, `src/rekonsiliasi/api.js`,
  `src/mekari/api.js` — kalimat "Pilih dulu ... milik siapa: PT Alyssa Auto
  Logistik atau CV Alyssa Trans Utama" harus tetap menyebut kedua nama, karena
  memang menanyakan yang mana.
- `src/rekonsiliasi/laporan.js` — label `'PT Alyssa Auto Logistik & CV Alyssa
  Trans Utama'` yang dipakai saat tidak ada penyaringan entitas. Ini sengaja
  menyebut keduanya.
- label di samping `<option>` dan checkbox `entitas_akses` di
  `public/index.html`.

Aman: ganti satu per satu di lima lokasi tampilan di tabel atas. Tidak aman:
`sed -i` ke seluruh repo.

### Buat supaya gampang dibalik

Kalau penggantian nama tampilan ini dikerjakan, kerjakan sebagai **satu commit
yang hanya berisi itu** — jangan dicampur perubahan lain. Dengan begitu
mengembalikannya cukup `git revert <commit>`, tanpa menyentuh apa pun yang lain
dan tanpa menebak-nebak berkas mana yang harus dikembalikan.

### Catatan: nama tampilan bukan alat verifikasi mitra

Aplikasi ini internal dan ber-login. Gerbang masuk menutup seluruh layar sampai
sesinya sah, dan server menolak setiap endpoint tanpa sesi. **Mitra luar tidak
bisa melihatnya**, jadi judul di sini tidak memenuhi persyaratan verifikasi
pihak mana pun.

Dan karena PT dan CV pembukuannya terpisah, keduanya pihak hukum yang berbeda —
bukan dua nama untuk satu usaha. Entitas yang diajukan dan disetujui mitra harus
sama dengan entitas yang benar-benar menjalankan pekerjaannya dan menanggung
risikonya. Kalau tidak sama, polis dan kontrak menunjuk entitas yang satu
sementara yang beroperasi entitas yang lain, dan itu baru ketahuan saat ada
klaim atau sengketa.

Jadi: ganti nama tampilan kalau memang itu nama rumah yang dikehendaki. Jangan
dipakai sebagai pengganti pengajuan dengan badan usaha yang benar.

### Pertanyaan yang belum dijawab

Kalau aplikasi ini melayani **dua** perusahaan, memberi judulnya nama salah satu
perusahaan justru menyesatkan. Pilihan yang lebih jujur mungkin nama netral yang
menaungi keduanya. Keputusan ini milik pemilik, dan belum diambil.

---

## Audit

Dilakukan baca-saja pada 2026-10-12, commit `bd7faa4`. Sejak itu Felis-Alyssa
tidak disentuh lagi dari sini, dan repo itu aktif — **verifikasi ulang setiap
lokasi sebelum menerapkan apa pun.** Tidak ada nomor baris yang dicantumkan di
dokumen ini justru karena alasan itu.

---

## Di luar cakupan

Situs publik `alyssalogistik.co.id` **tidak ada di GitHub**. Tidak ada referensi
ke domain itu di Felis-Alyssa, dan hanya ada dua repo di akun ini. Felis-Alyssa
adalah dashboard internal ber-login yang deploy di Railway, bukan company
profile. Kalau yang dimaksud tulisan di situs `.co.id`, sasarannya ada di panel
hosting situs itu, bukan di repo mana pun di sini.
