---
name: dev_cycle
description: Jalankan siklus pengembangan 3 peran berurutan (Programmer -> Auditor -> QA) sesuai aturan di .agents/agents.md untuk setiap fitur atau perbaikan bug.
---

# Workflow `/dev_cycle`

Gunakan workflow ini untuk mengeksekusi task coding secara disiplin melalui **3 tahap berurutan** berdasarkan aturan di [`../agents.md`](../agents.md).

---

## Tahap 1 — Programmer (Implementasi Minimal & Bersih)
1. Baca konteks file yang relevan dan pahami kebutuhan user secara persis.
2. Rancang solusi paling sederhana yang memenuhi kebutuhan (**dilarang over-engineering**, tanpa dependensi baru yang tidak perlu, logic inti bebas PyQt6).
3. Terapkan perubahan kode secara terfokus dengan tetap menjaga komentar/docstring yang sudah ada.
4. Pastikan semua edge dasar (validasi input, `sys.platform` guard untuk Windows/Linux, atomic JSON write) sudah ditangani.

**Output Tahap 1:** Ringkasan file yang diubah + alasan desain singkat.

---

## Tahap 2 — Auditor (Audit Efisiensi, Memori & Kompleksitas)
Tinjau seluruh diff hasil **Tahap 1** sebagai **Auditor**:
1. **Audit Memori & Resource:** Periksa apakah ada alokasi berulang di hot-path (mis. `ctypes.Structure`/`POINTER` di event filter/polling), thread burst berlebih, timer yang tidak berhenti saat idle, atau cache tak terbatas.
2. **Audit Kompleksitas:** Identifikasi bagian yang over-engineered, duplikat, atau I/O yang bisa dipersingkat.
3. **Audit Non-Blocking & Keamanan:** Pastikan panggilan jaringan/berat berjalan di background thread dan tidak ada secret/token yang bocor ke log.

**Gerbang Tahap 2:**
- Jika ditemukan masalah efisiensi atau over-engineering → **kembali ke Tahap 1** untuk perbaikan sebelum lanjut ke QA.
- Jika bersih → catat hasil audit dan lanjut ke **Tahap 3**.

---

## Tahap 3 — QA (Pengujian Edge-Case, Visual & Regresi)
Uji perubahan secara menyeluruh sebagai **QA**:
1. **Kompilasi:** Jalankan `python3 -m py_compile *.py`.
2. **Uji Fungsional & Edge-Case:** Jalankan script verifikasi (`QT_QPA_PLATFORM=offscreen`) yang menguji:
   - Input kosong/ekstrem/error HTTP/offline fallback.
   - Siklus buka → tutup (`close_animated`) → buka ulang (cek opacity/state tidak rusak).
   - Stress test memori/thread jika menyentuh event filter, timer, audio, atau polling OS.
3. **Uji Visual Multi-Skala (jika menyentuh UI):** Render dan periksa visual di skala `85%`, `100%`, dan `130%` untuk memastikan tidak ada clipping atau overlap.
4. **Cek Regresi & Kebersihan:** Pastikan tes fitur lama terkait tetap lulus dan working tree bebas `__pycache__` maupun file runtime `.json`.

**Gerbang Tahap 3:**
- Jika ada test gagal atau cacat visual → **kembali ke Tahap 1** (lalu audit ulang di **Tahap 2** jika struktur berubah).
- Jika 100% lulus → siapkan Conventional Commit yang rapi dan laporkan ringkasan hasil tiap peran kepada user.
