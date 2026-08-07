<div align="center">

[![Grok Register — Toolkit Otomatisasi Registrasi GUI, CLI, dan Web UI](assets/banner.png)](https://github.com/AaronL725/grok-register)

Grok Register adalah toolkit Python yang dirancang untuk riset alur otomatisasi, verifikasi lingkungan pengujian, dan pembelajaran pribadi. Proyek ini menyediakan antarmuka **Web Control Plane**, **GUI (Desktop)**, **CLI (Terminal)**, integrasi 4 penyedia temp-mail (termasuk Cloudflare Temp Mail), otomatisasi halaman Chromium, penyimpanan akun aman, integrasi token pool grok2api, serta ekspor kredensial CPA xAI OIDC.

<p align="center">
  <b>Bahasa / Language / 语言:</b>
  <a href="README.md"><b>[ 简体中文 ]</b></a> |
  <a href="README.id.md"><b>[ Bahasa Indonesia ]</b></a>
</p>

</div>

---

> [!IMPORTANT]
> Proyek ini hanya digunakan untuk penelitian alur otomatisasi, verifikasi lingkungan pengujian, dan pembelajaran pribadi. Pengguna wajib mematuhi Ketentuan Layanan situs target, hukum dan peraturan setempat, serta batasan layanan pihak ketiga. Dilarang keras menggunakan proyek ini untuk penyalahgunaan, peretasan, atau tujuan komersial tanpa izin.

## 📋 Daftar Isi

- [Fitur Utama](#-fitur-utama)
- [Persyaratan Lingkungan](#-persyaratan-lingkungan)
- [Instalasi](#-instalasi)
- [Konfigurasi & Penggunaan Web UI](#-konfigurasi--penggunaan-web-ui)
- [Penggunaan CLI & Desktop GUI](#-penggunaan-cli--desktop-gui)
- [Lisensi](#-lisensi)

---

## 🚀 Fitur Utama

1. **Tiga Mode Antarmuka**:
   - **Web Control Plane (FastAPI + Modern UI)**: Antarmuka Web responsif dengan dukungan **Dual Language (Bahasa Mandarin & Bahasa Indonesia)**, tanpa emoji, siap pakai via browser.
   - **Desktop GUI**: Menggunakan Python Tkinter.
   - **CLI Mode**: Berjalan langsung via terminal / command line.

2. **Dukungan 4 Penyedia Email**:
   - **Cloudflare Temp Mail**: Mendukung Cloudflare Worker Email gratis milik pribadi.
   - **DuckMail**, **CloudMail**, dan **YYDS Mail**.

3. **Manajemen Proxy & Multi-Thread**:
   - Dukungan proxy HTTP / SOCKS5.
   - Mode registrasi paralel multi-thread (1 hingga 8 worker browser).

4. **Integrasi & Ekspor Akun**:
   - **grok2api Pool Sync**: Penambahan token otomatis ke remote pool atau file lokal.
   - **CPA xAI OIDC Export**: Ekspor kredensial OIDC otomatis setelah registrasi berhasil.

---

## 🛠️ Persyaratan Lingkungan

- **Python**: versi `3.9+` (direkomendasikan Python 3.11).
- **Browser**: Google Chrome / Chromium terinstal di sistem.

---

## 📦 Instalasi

1. Clone repositori:
```bash
git clone https://github.com/AaronL725/grok-register.git
cd grok-register
```

2. Buat dan aktifkan virtual environment:
```bash
python3 -m venv .venv
source .venv/bin/activate  # Di Linux/macOS
# .venv\Scripts\activate   # Di Windows
```

3. Instal dependensi:
```bash
pip install -r requirements.txt fastapi uvicorn
```

---

## 🌐 Konfigurasi & Penggunaan Web UI

Jalankan server Web Control Plane:
```bash
python web/server.py
```
Akses di browser melalui: **`http://127.0.0.1:8092`**

Fitur Web UI:
- **Pengalih Bahasa (i18n)**: Klik tombol `[ 中文 | ID ]` di kanan atas untuk berpindah antara Bahasa Mandarin dan Bahasa Indonesia secara instan.
- **Form Konfigurasi**: Lengkap untuk parameter registrasi, mail provider (Cloudflare API Base, API Key, Default Domain), grok2api pool, dan CPA Export.
- **Terminal Log Live**: Pantau proses registrasi secara real-time dengan opsi Salin Log dan Bersihkan Log.

---

## 💻 Penggunaan CLI & Desktop GUI

### Mode Desktop GUI (Tkinter)
```bash
python grok_register_ttk.py
```

### Mode CLI (Terminal)
```bash
python grok_register_ttk.py cli --count 5
```

---

## 📄 Lisensi

Proyek ini dirilis di bawah lisensi [MIT License](LICENSE).
