# 🚢 MaritimeLiner — Fixed Liner Logistics Management System

**MaritimeLiner** adalah sistem manajemen operasional logistik maritim terintegrasi untuk armada kapal petikemas berjadwal tetap (*fixed liner service*). Sistem ini dilengkapi dengan dashboard *real-time*, modul CRUD armada dan jadwal rotasi pelabuhan, algoritma optimasi relokasi petikemas kosong (*Empty Container Repositioning* / ECR), serta simulator konsumsi bahan bakar kapal (*bunker*).

---

## 📌 Spesifikasi Perangkat Lunak (Software Specifications)

Sistem ini dirancang dengan arsitektur yang ringan, mandiri (*self-contained*), dan dapat dijalankan di lingkungan lokal tanpa konfigurasi infrastruktur yang rumit.

* **Backend Framework:** Python 3.9+ menggunakan **FastAPI** (asinkron, performa tinggi, dan dokumentasi OpenAPI otomatis).
* **ASGI Web Server:** **Uvicorn** (server HTTP super cepat berbasis `uvloop` dan `httptools`).
* **Database Engine:** **SQLite 3** (database relasional lokal tersimpan dalam berkas `maritime_liner.db`).
* **Frontend Stack:**
  * Single Page Application (SPA) berbasis Vanilla HTML5 & JavaScript murni.
  * **Tailwind CSS** (via CDN) dengan palet visual kustom *Modern Maritime Dark Mode*.
  * **Chart.js** (via CDN) untuk visualisasi diagram donat distribusi pelayaran dan diagram batang neraca peti kemas.
  * **Lucide Icons** (via CDN) untuk ikonografi antarmuka maritim.
* **Modul Komputasi Matematika:**
  * Formulasi Linear Programming untuk minimasi biaya transportasi (*Transportation Problem*).
  * Pemodelan konsumsi bahan bakar kubik ($F = k \cdot v^3$).

---

## ⚡ Fitur Utama & Kemampuan Sistem (What It Can Do)

1. **Dashboard Command Center (Overview):**
   * **4 KPI Maritim Utama:** Memantau jumlah *Active Voyages*, tingkat keandalan jadwal (*Schedule Reliability %*), indeks ketimpangan peti kemas (*Empty TEU Imbalance*), dan estimasi konsumsi bahan bakar (*Bunker VLSFO/day*).
   * **Visualisasi Interaktif:** Menampilkan status perjalanan armada (*En Route*, *Scheduled*, *Completed*) serta perbandingan stok vs permintaan kontainer per pelabuhan secara langsung.

2. **Fixed Schedules & Rotations Management (CRUD):**
   * Pengelolaan trayek tetap lingkar Nusantara: **Jakarta (IDTPP) → Surabaya (IDSUB) → Makassar (IDMAK) → Balikpapan (IDBPN) → Jakarta**.
   * Penambahan jadwal pelayaran baru (*voyage*), pembaruan urutan pelabuhan singgah (*port legs*), serta pengubahan status perjalanan (*SCHEDULED* $\rightarrow$ *EN_ROUTE* $\rightarrow$ *BERTHED* $\rightarrow$ *COMPLETED*).

3. **Fleet Management (CRUD):**
   * Pencatatan data master armada kapal: Nama kapal, nomor IMO, kapasitas muat (TEU), tonase bobot mati (*DWT*), dan profil konsumsi BBM harian di laut (*sea consumption rate*).
   * Proteksi integritas data: Pencegahan penghapusan unit kapal yang sedang dalam status pelayaran aktif.

4. **Empty Container Repositioning (ECR) Optimizer:**
   * Menghitung solusi relokasi peti kemas kosong teroptimal dari pelabuhan yang mengalami surplus (*stock*) ke pelabuhan yang mengalami defisit (*demand*).
   * Menggunakan formulasi minimasi biaya transportasi matriks antarpelabuhan untuk menekan biaya angkut kontainer kosong (*zero-revenue asset*).
   * Kemampuan memperbarui data pasokan dan permintaan per pelabuhan secara dinamis untuk pengujian skenario logistik yang berbeda.

5. **Bunker Speed & Fuel Simulator:**
   * Widget kalkulator interaktif berbasis hukum kubik kecepatan kapal:
     $$F \approx k \cdot v^3$$
   * Mensimulasikan dampak pengurangan kecepatan berlayar (*slow steaming*) terhadap penghematan tonase BBM harian serta implikasinya terhadap estimasi waktu tiba (*ETA*).

---

## 📂 Struktur Direktori Proyek

```text
maritime-liner/
│
├── app.py                  # Server FastAPI, rute CRUD API, dan modul optimasi
├── init_db.py              # Skrip inisialisasi tabel SQLite & data seeder bawaan
├── maritime_liner.db       # Database SQLite lokal (terbuat otomatis)
├── templates/
│   └── index.html          # Frontend SPA (UI, Tailwind CSS, Chart.js, Modal CRUD)
└── README.md               # Dokumentasi proyek
```

---

## 🚀 Panduan Penggunaan & Instalasi (Step-by-Step)

Ikuti langkah-langkah di bawah ini untuk menjalankan aplikasi pada komputer lokal Anda:

### 1. Prasyarat Lingkungan
Pastikan Anda telah menginstal **Python 3.9** atau versi yang lebih baru pada sistem Anda. Verifikasi instalasi melalui terminal:
```bash
python --version
# atau
python3 --version
```

### 2. Instalasi Pustaka (Dependencies)
Buka terminal / Command Prompt pada direktori utama proyek, kemudian instal dependensi minimal yang diperlukan:
```bash
pip install fastapi uvicorn
```
*(Opsional: Jika algoritma optimasi Anda menggunakan modul SciPy atau rendering template Jinja2, jalankan `pip install scipy jinja2`)*.

### 3. Inisialisasi Database Lokal
Sebelum menjalankan server untuk pertama kali, jalankan skrip pembuatan skema tabel SQLite dan pengisian data dummy awal:
```bash
python init_db.py
```
> Berkas `maritime_liner.db` akan dibuat secara lokal berisi master data pelabuhan Nusantara, data armada kapal, dan jadwal pelayaran aktif.

### 4. Menjalankan Server Aplikasi
Jalankan server pengembangan lokal menggunakan **Uvicorn** dengan perintah berikut:
```bash
uvicorn app:app --reload --host 127.0.0.1 --port 8080
```
Jika gagal, ganti port dengan port lain contohnya 5000. 
**Penjelasan Parameter:**
* `app:app`: Mengacu pada berkas `app.py` dan objek instansiasi FastAPI bernama `app`.
* `--reload`: Mengaktifkan mode deteksi perubahan kode otomatis (server akan *restart* otomatis saat file diubah).
* `--host 127.0.0.1`: Mengikat server pada alamat *localhost*.
* `--port 8080`: Menentukan port jaringan yang digunakan aplikasi (Port `8080`).

### 5. Mengakses Antarmuka Web
Setelah server berhasil berjalan (ditandai dengan pesan `Uvicorn running on http://127.0.0.1:8080`), buka peramban web (*browser*) Anda dan akses alamat:
```text
http://127.0.0.1:8080
```
Atau jika gagal, maka gunakan alamat berikut:
```text
http://127.0.0.1:5000
```

---

## 🛠️ Panduan Operasional Pengguna di Antarmuka

1. **Melihat Ringkasan Operasional:** Buka tab **Dashboard Overview** di sidebar untuk meninjau metrik performa armada dan neraca kontainer.
2. **Mengelola Kapal:** Buka tab **Fleet Management**, gunakan tombol `+ Add New Vessel` untuk mendaftarkan kapal baru, atau klik ikon edit/delete pada baris tabel untuk mengubah parameter armada.
3. **Mengatur Jadwal Rute:** Buka tab **Fixed Schedules / Rotations** untuk memantau status sandar kapal pada rute Nusantara Ring atau memperbarui status voyage menjadi `COMPLETED`.
4. **Menjalankan Optimasi Relokasi:** Buka tab **Container Repositioning**, sesuaikan angka stok/permintaan kontainer di pelabuhan terkait, lalu klik tombol **"Run Transport Optimization"** untuk mendapatkan rekomendasi alokasi berbiaya minimum.
5. **Menghentikan Server:** Tekan kombinasi tombol `Ctrl + C` pada jendela terminal yang menjalankan Uvicorn.
