# Revisi nama: PT Alyssa Auto Logistik → CV Alyssa Trans Utama

Status: **rencana. Belum diterapkan ke mana pun.**

Sasaran perubahan ada di repo `alyssalogistik/Felis-Alyssa`. Repo itu sedang
dinyatakan tidak boleh disentuh (lihat `CLAUDE.md`), jadi rencananya disiapkan
di sini lebih dulu. Jangan menerapkan apa pun dari dokumen ini tanpa izin
eksplisit pemilik repo.

Dasar: CV Alyssa Trans Utama adalah badan usaha yang terdaftar (sesuai NPWP yang
ditunjukkan pemilik). PT Alyssa Auto Logistik disebut pemilik dibuat untuk
memenuhi persyaratan mitra, bukan entitas yang berjalan sendiri.

Audit dilakukan baca-saja pada 2026-10-12, commit `bd7faa4`. **Nomor baris di
bawah harus diverifikasi ulang sebelum dipakai** — repo itu aktif dan sudah
berubah sejak audit.

---

## Kenapa ini bukan satu kali find-replace

Teks `PT Alyssa Auto Logistik` di repo itu muncul dalam **dua peran yang
berbeda**, dan hanya satu yang aman diganti massal.

Yang kedua, kode `PT_ALYSSA_AUTO_LOGISTIK`, bukan label tampilan — itu **nilai
yang tersimpan di database**. Di `supabase/migrations/0009_entitas_rekening.sql`
kolom `entitas` dipasang di empat tabel:

| Tabel | Isi |
|---|---|
| `transaksi_bank` | baris mutasi rekening koran |
| `unggahan_rekening_koran` | berkas rekening koran yang diunggah |
| `pembayaran_manual` | pembayaran yang dicatat manual |
| `tagihan_pemasok` | tagihan supplier |

Kolom itu `NOT NULL`, dijaga `CHECK (entitas in ('PT_ALYSSA_AUTO_LOGISTIK',
'CV_ALYSSA_TRANS_UTAMA'))`, dan **seluruh baris lama di-backfill sebagai
`PT_ALYSSA_AUTO_LOGISTIK`**. Artinya seluruh riwayat sebelum migration 0009
bertanda PT.

Konsekuensinya:

- Ganti nama **kode**-nya saja di aplikasi → bentrok dengan CHECK constraint,
  aplikasi menolak menulis.
- Ganti **isi datanya** jadi CV tanpa migration yang benar → seluruh riwayat PT
  berubah pemilik, dan pemisahan rekening hilang.

Kode aplikasinya sendiri sudah memperingatkan mode gagal ini.
`src/rekonsiliasi/entitas.js` menulis bahwa kedua perusahaan membayar sebagian
supplier yang sama, dan tanpa pemisahan itu transfer PT akan ikut muncul saat
mencari pembayaran dari rekening CV — sehingga tagihan yang tampak sudah dibayar
sebenarnya dibayar perusahaan lain. Berkas yang sama mencatat bahwa salah tanda
entitas **tidak menimbulkan galat apa pun** dan baru ketahuan berbulan kemudian
saat angka auditnya dipakai, ketika sudah tidak bisa dibedakan lagi baris mana
yang salah.

---

## Bagian A — tampilan. Aman, tidak menyentuh data.

Hanya nama yang dibaca orang. Tidak ada kaitan dengan kolom `entitas`, tidak ada
baris database yang berubah, tidak ada logika yang bergeser.

| Berkas | Yang diganti |
|---|---|
| `public/index.html` | `<title>`, `<h1>` di layar masuk, `<h1>` di header |
| `src/server.js` | teks log saat server menyala |
| `package.json` | field `description` |
| `README.md` | kalimat pembuka |
| `CLAUDE.md` | judul |

Perlu diputuskan juga: inisial `AL` pada favicon dan lambang di header. Kalau
nama berubah jadi Alyssa Trans Utama, inisialnya jadi `AT`.

Catatan: nama **repo**, nama package `felis-alyssa`, dan nama project Supabase
tidak perlu ikut berubah. Mengubahnya memutus remote, deployment, dan env var
tanpa memberi manfaat apa pun.

---

## Bagian B — entitas data. Berisiko, perlu migration.

Lokasi yang memakai kode entitas (bukan sekadar label):

- `src/rekonsiliasi/entitas.js` — definisi `ENTITAS`, sumber kebenaran
- `public/tautan.js`, `public/mekari.js` — salinan untuk frontend
- `src/rekonsiliasi/audit.js`, `src/rekonsiliasi/api.js`,
  `src/rekonsiliasi/laporan.js`, `src/mekari/api.js` — teks pilihan & penyaringan
- `public/index.html` — belasan `<option value="PT_ALYSSA_AUTO_LOGISTIK">` dan
  checkbox `entitas_akses` untuk hak akses per perusahaan
- `supabase/migrations/0009_entitas_rekening.sql` — kolom, constraint, backfill

### Pertanyaan yang masih mengunci bagian ini

**Apakah PT dan CV satu pembukuan, atau dua?**

- **Dua rekening/pembukuan terpisah** → pemisahan di aplikasi sudah benar.
  Jangan digabung. Kerjakan Bagian A saja, dan kalau perlu cukup ubah *label*
  `PT Alyssa Auto Logistik` tanpa menyentuh kode `PT_ALYSSA_AUTO_LOGISTIK`.
- **Satu pembukuan, PT hanya nama di atas kertas** → perlu migrasi resmi
  memindahkan baris PT ke CV.

Pertanyaan ini pernah diajukan ke pemilik dan belum dijawab. **Selama belum
dijawab, Bagian B tidak boleh dikerjakan.**

### Kalau nanti jawabannya "satu pembukuan"

Urutan yang aman, bukan find-replace:

1. Backup penuh database Supabase, dan pastikan backup itu benar-benar bisa
   dipulihkan — bukan sekadar ada.
2. Hitung dulu jumlah baris PT di keempat tabel, simpan angkanya sebagai
   pembanding sesudah migrasi.
3. Migration baru (`0016_...`) yang: melonggarkan CHECK constraint, meng-`update`
   baris PT jadi CV di keempat tabel, lalu memasang ulang CHECK dengan nilai yang
   tersisa saja.
4. Sesuaikan hak akses `entitas_akses` pengguna yang sebelumnya hanya PT.
5. Baru setelah itu bersihkan kode aplikasinya.
6. Verifikasi: tidak ada lagi baris `entitas = 'PT_ALYSSA_AUTO_LOGISTIK'`, dan
   total baris per tabel tidak berubah dari langkah 2.

Pertimbangkan juga menyimpan kolom jejak (misalnya `entitas_asal`) supaya baris
yang dulu bertanda PT masih bisa ditelusuri setelah digabung. Sekali digabung
tanpa jejak, asal-usulnya hilang permanen.

---

## Yang bukan bagian dari dokumen ini

Situs publik `alyssalogistik.co.id` **tidak ditemukan di GitHub**. Tidak ada
referensi ke domain itu di Felis-Alyssa, dan hanya ada dua repo di akun ini
(Felis-Alyssa dan DJ-Cutter). Felis-Alyssa sendiri adalah dashboard internal
ber-login yang deploy di Railway, bukan company profile.

Kalau yang dimaksud pemilik adalah tulisan di situs `.co.id`, sasarannya ada di
panel hosting situs itu, bukan di repo mana pun di sini.
