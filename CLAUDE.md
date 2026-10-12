# DJ-Cutter

Pipeline pembersih sambungan OUTRO → PLAY untuk folder DJ set bernomor.
Lihat `README.md` untuk cara pakai dan `dj_cutter.py` untuk kodenya.

## Aturan wajib: JANGAN SENTUH repo Felis-Alyssa

**`alyssalogistik/Felis-Alyssa` tidak boleh disentuh dari repo ini.** Tidak
di-clone, tidak dibaca, tidak di-edit, tidak di-push, tidak dibuatkan PR, tidak
dijalankan migration-nya. Berlaku untuk database Supabase, environment variable,
dan deployment Railway miliknya juga.

Pemilik repo yang menetapkan ini. Kalau ada pekerjaan yang kelihatannya menuntut
akses ke Felis-Alyssa — termasuk revisi nama entitas di
`REVISI-NAMA-ENTITAS.md` — **berhenti dan tanya pemiliknya dulu.** Jangan
mengerjakannya diam-diam, dan jangan menganggap izin untuk satu hal berlaku
untuk hal berikutnya.

Yang boleh dikerjakan di sini hanyalah berkas di dalam repo DJ-Cutter sendiri.

## Revisi nama entitas

`REVISI-NAMA-ENTITAS.md` adalah **rencana**, bukan perubahan yang sudah jalan.
Isinya hasil audit baca-saja terhadap Felis-Alyssa. Dokumen itu disimpan di sini
justru supaya pekerjaannya bisa disiapkan tanpa menyentuh repo tersebut.

Pemilik sudah memutuskan: **PT dan CV pembukuan terpisah, produk sama.** Maka
menggabungkan PT Alyssa Auto Logistik ke CV Alyssa Trans Utama **dibatalkan** —
keduanya entitas yang berdiri sendiri dan dua-duanya punya data nyata. Produk
yang sama berarti pemasok yang sama melayani keduanya, jadi tanda entitas per
baris itulah satu-satunya pembeda transfer PT dari transfer CV.

Kalau ada yang menawarkan "ganti semua PT jadi CV sekali jalan" di repo itu,
**tolak.** Yang masih berlaku hanya penggantian nama tampilan aplikasi, dan itu
pun tidak boleh dikerjakan dengan find-replace karena teks yang sama dipakai
sebagai label entitas. Rinciannya ada di `REVISI-NAMA-ENTITAS.md`.
