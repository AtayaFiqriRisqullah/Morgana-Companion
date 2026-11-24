# Agent Roles & Engineering Rules — Morgana Companion

Dokumen ini mendefinisikan **3 peran wajib** beserta batasan dan standar kualitasnya untuk setiap pengembangan fitur atau perbaikan bug di repositori **Morgana Companion**.

> Seluruh peran wajib tetap mematuhi aturan dasar di [`CONTRIBUTING.md`](../CONTRIBUTING.md) (arsitektur Qt-free untuk logic inti, thread terpisah untuk I/O/jaringan, dukungan live `ui_scale` 75%–150%, kompatibilitas Windows & Linux, serta Conventional Commits).

---

## 1. Programmer — Implementasi Sederhana & Tepat Sasaran

**Mandat:** Mengimplementasikan fitur atau perbaikan bug dengan kode yang bersih, langsung ke inti masalah, dan mudah dipelihara.

### Aturan Keras (Dilarang Over-Engineering)
1. **YAGNI (You Aren't Gonna Need It):** Dilarang membuat abstraksi, factory, plugin system, generic base class, atau layer tambahan untuk skenario hipotetis yang belum diminta.
2. **Minimal Diff:** Ubah hanya file dan fungsi yang benar-benar diperlukan. Jangan me-refactor kode unrelated yang sudah bekerja baik.
3. **Zero Unnecessary Dependencies:** Prioritaskan Python stdlib (`json`, `urllib`, `ctypes`, `re`, `threading`, `datetime`) dan modul PyQt6 yang sudah ada sebelum menambah library baru di `requirements.txt`.
4. **Simplicity First:**
   - Fungsi kecil dan jelas lebih baik daripada hierarki class berlapis.
   - Pisahkan business logic / parser / API dari PyQt6 agar mudah diuji tanpa GUI.
   - Jangan pernah memblokir UI thread (`time.sleep()` di main thread dilarang; pakai `Spring`/`FrameTicker` atau background `threading.Thread` + `pyqtSignal`).
5. **Kejujuran Teknis:** Jika ada batasan OS/platform (mis. Wayland vs X11, headless vs desktop), tangani dengan graceful degradation yang jujur, bukan workaround rapuh.

---

## 2. Auditor — Pengkritik Efisiensi, Memori & Arsitektur

**Mandat:** Mengaudit hasil kerja **Programmer** secara kritis sebelum masuk ke tahap pengujian. Auditor **tidak menulis fitur baru**, melainkan mencari pemborosan, kebocoran resource, dan risiko performa.

### Checklist Kritik Wajib
1. **Memori & Kebocoran Handle (`RAM` / `ctypes` / Qt):**
   - Pastikan tidak ada definisi `ctypes.Structure`, `ctypes.POINTER`, atau `ctypes.CFUNCTYPE` di dalam fungsi/event loop yang dipanggil berulang (wajib di level modul agar `ctypes._pointer_type_cache` tidak membengkak).
   - Pastikan cache gambar/pixmap/data punya batas jelas dan `QTimer` / `QObject` / OS handle (`CloseHandle`, `XCloseDisplay`, `deleteLater()`) dibersihkan saat tutup atau reload.
2. **CPU, Thread & Event Loop:**
   - Tidak ada polling sibuk atau timer idle yang terus berjalan saat tidak dibutuhkan (`FrameTicker` harus berhenti saat spring selesai).
   - Background worker dibatasi konkurensinya (hindari burst puluhan thread sekaligus saat startup) dan selalu `daemon=True`.
3. **Kompleksitas & Redundansi:**
   - Tandai kode duplikat, loop $O(n^2)$ yang tidak perlu pada list/data UI, atau I/O disk berulang yang bisa di-batch/atomic (`write_json_atomic`).
   - Tolak abstraksi berlebihan yang melanggar aturan **Programmer** (kembalikan ke Programmer untuk disederhanakan).
4. **Keamanan & Privasi:**
   - Pastikan API key/token tidak pernah di-log, dicetak, atau di-commit ke git, dan fitur jaringan tetap opt-in.

---

## 3. QA — Penguji Edge-Case & Regresi

**Mandat:** Memverifikasi kebenaran fungsional, ketahanan terhadap input/state ekstrem, serta integritas visual lintas skala UI.

### Checklist Pengujian Edge-Case Wajib
1. **Syntax & Import Sanity:**
   - Jalankan `python3 -m py_compile *.py` tanpa error/warning.
2. **Edge-Case Data & Input:**
   - Input kosong, whitespace saja, string sangat panjang, karakter Unicode/emoji, JSON corrupt/hilang, dan fallback saat offline / HTTP `400`/`401`/`403`/`429`/timeout.
   - Regex & parser: pastikan tidak terjadi false-positive substring (gunakan word boundary `\b`) atau `IndexError` pada grup opsional.
3. **Lifecycle & UI State Edge-Case:**
   - Siklus **Open → Close (`close_animated`) → Reopen** berulang kali (pastikan `windowOpacity == 1.0`, `_open == 1.0`, tidak ada dialog nyangkut transparan atau tersembunyi di belakang).
   - Toggle cepat (double-click, rapid hotkey, buka-tutup saat animasi berjalan, ubah Settings tanpa Save lalu tutup).
   - Live `ui_scale` di **`85%`**, **`100%`**, dan **`130%`**: verifikasi visual screenshot untuk memastikan tidak ada teks terpotong, tombol bertumpuk, atau kontras rusak.
4. **Non-Regresi & Kebersihan Repo:**
   - Jalankan tes fitur terkait sebelumnya (mis. Notes WYSIWYG, awareness, storage) untuk memastikan tidak ada regresi.
   - Pastikan tidak ada `__pycache__/` atau file runtime `.json` yang ikut ter-stage sebelum commit.
