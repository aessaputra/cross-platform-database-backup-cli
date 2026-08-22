# Connection URL — Design Spec (Alternatif B: Hybrid Parsed + Inferred — dua mode setara)

**Date:** 2026-08-22
**Status:** Locked — koreksi dua mode setara (Structured vs URL), Alternatif B
**Scope:** Connection URL/URI untuk dbbackup (PostgreSQL/Neon, MySQL, MongoDB, MongoDB Atlas, SQLite) — dua mode eksklusif
**Non-goals:** No source-code/tests/pyproject/README/workflow change, no commit/push pada langkah ini
**Depends on:** `docs/superpowers/specs/2026-08-22-backup-cli-design.md` (locked baseline)

## Prinsip terkunci

dbbackup mendukung **dua mode koneksi yang setara**, bukan `url` menggantikan `structured`:

1. **Structured connection mode** — `--db` + `--host`/`--port`/`--user`/`--database` + password flags
2. **Connection URL mode** — `--url <connection-url>`

`--db` **bukan legacy, bukan deprecated, bukan fallback**. Kedua mode adalah penggunaan normal dan valid; `--url` ditambahkan tanpa merusak structured mode.

**Mode eksklusif:** `--url` **tidak boleh** dikombinasikan dengan `--host` / `--port` / `--user` / `--database` / password flags (`--password`, `--password-env`, `--password-stdin`, `--ask-password`). Kombinasi tersebut adalah conflicting connection configuration → `exit 10` actionable (bukan override, bukan warning).

Arsitektur: URL diparse menjadi `ConnectionOpts` + query params (`extra`); kontrak `DBAdapter(ConnectionOpts)` tidak diubah; tidak ada raw URL passthrough ke `pg_dump`/`mysqldump`/`mongodump`; tidak menyimpan raw URL bila tidak diperlukan (hindari credential leakage).

## Seksi 1 — CLI surface

### 1.1 Flag

- `--url TEXT` canonical. Help: `Database connection URL (postgresql://, mysql://, mongodb://, mongodb+srv://, sqlite://). Exclusive with --host/--port/--user/--database and password flags.`
- `--uri TEXT` hidden alias, identik dengan `--url` (satu implementasi, dua option string).
- Berlaku untuk `backup`, `restore`, `test-connection`. `schedule --daemon` tidak menerima `--url` CLI (jobs dari TOML — lihat Seksi 4).

### 1.2 Aturan --db

1. Structured mode **tanpa** `--url`: `--db` **wajib** (perilaku existing tetap).
2. URL mode (`--url` ada): `--db` **opsional** — DBMS diinfer dari scheme URL.
3. `--url` + `--db` bersamaan: `--db` **hanya** untuk validasi konsistensi dengan scheme. Valid: `--db postgres` + `postgresql://...`; invalid: `--db mysql` + `postgresql://...` → `exit 10` `scheme postgresql conflicts with --db mysql`. `--db` **tidak** pernah diam-diam meng-override scheme.
4. Jangan menyebut `--db` sebagai legacy/deprecated di source code, README, spec, plan, CLI help, atau dokumentasi.

Scheme mapping (case-insensitive):

- `postgresql` | `postgres` → `postgres`
- `mysql` → `mysql`
- `mongodb` | `mongodb+srv` → `mongo`
- `sqlite` → `sqlite`

Scheme tidak didukung → `exit 10` `unsupported URL scheme '<scheme>'`.

### 1.3 Aturan pencampuran (mutually exclusive)

Jika `--url` diberikan bersamaan dengan salah satu dari `--host`, `--port`, `--user`, `--database`, atau password flags (`--password`, `--password-env`, `--password-stdin`, `--ask-password`):

→ `exit 10` dengan pesan actionable, contoh: `ERROR: --url cannot be combined with --host. Use either --url or structured connection flags (--db/--host/--port/--user/--database).`

- Tidak ada precedence `URL base → CLI override`. Dua mode adalah **dua konfigurasi terpisah**, bukan dua layer yang digabung.
- Flag storage (`--storage`, `--s3-bucket`, dll.) tidak termasuk aturan ini dan tetap independent.

Contoh valid:

```bash
dbbackup backup --url "postgresql://user:password@host:5432/db?sslmode=require"
dbbackup backup --db postgres --host host --port 5432 --user user --database db
dbbackup backup --db postgres --url "postgresql://user:password@host:5432/db?sslmode=require"  # ok, --db untuk validasi
dbbackup test-connection --url "mongodb+srv://user:pass@cluster.mongodb.net/mydb?authSource=admin"
dbbackup restore --url "postgresql://user:pass@host/db" --key prod/mydb-20260822.sql.gz
```

Contoh invalid:

```bash
dbbackup backup --url "postgresql://user@host/db" --host other-host
# → ERROR: --url cannot be combined with --host.
```

## Seksi 2 — Parsing dan ConnectionOpts.extra

### 2.1 Parser

- Gunakan `urllib.parse.urlparse` + `parse_qs(keep_blank_values=True)` + `urllib.parse.unquote` untuk `user`, `password`, `host`, `database`. Jangan regex manual.
- `password` dengan `:`/`@`/`/` wajib percent-encoded di URL (`p@ss` → `p%40ss`); `unquote` kembalikan nilai asli sebelum masuk `ConnectionOpts.password`.
- Validasi: URL tanpa `://`, scheme kosong, atau `unquote`/parse gagal → `exit 10` `invalid --url: ...` (redacted).

### 2.2 Mapping ke ConnectionOpts

- `host`, `port`, `user`, `password`, `database` dari URL → `ConnectionOpts`. Port default bila absen: postgres 5432, mysql 3306, mongo 27017; `mongodb+srv` tanpa port (SRV lookup, tidak diisi port di `ConnectionOpts`).
- `sqlite`:
  - `sqlite:////abs/path.db` → `/abs/path.db`
  - `sqlite:///rel/path.db` → `rel/path.db`
  - `sqlite:///:memory:` ditolak untuk backup (`exit 10`).
  - `sqlite` tidak memakai `host`/`port`/`user`.
- `mongodb+srv`: host dapat berupa `cluster.mongodb.net` (multi-host comma dipisah di parser toleran). Path `/database` opsional; query `authSource`, `tls`, `replicaSet` diteruskan.
- PostgreSQL `?sslmode=require&channel_binding=require` dan MySQL `?ssl-mode=REQUIRED` — keys case-sensitive dipertahankan apa adanya.

### 2.3 extra (query params)

- Tambah `ConnectionOpts.extra: dict[str, str] = {}` (alternatif nama `query_params`; pilih satu saat implementasi).
- Semua query params masuk `extra` dengan `last-value-wins` dan `unquote` pada value. Adapter membaca key yang dikenali, mengabaikan sisanya dengan `log.debug` (tidak fail).
- Tidak menyimpan `raw_url` di `ConnectionOpts` untuk menghindari kebocoran dan menjaga kontrak `DBAdapter(ConnectionOpts)` tetap.

### 2.4 Contoh

- `postgresql://user:pass@host:5432/mydb?sslmode=require`
- `postgresql://neondb_owner:p%40ss@ep-xxx.neon.tech/neondb?sslmode=require&channel_binding=require`
- `mysql://user:p%40ss@host:3306/mydb?ssl-mode=REQUIRED`
- `mongodb://user:pass@host:27017/mydb?authSource=admin`
- `mongodb+srv://user:pass@cluster.mongodb.net/mydb?authSource=admin&tls=true`
- `sqlite:////abs/app.db` dan `sqlite:///rel/app.db`

## Seksi 3 — Config layered dan env (DATABASE_URL)

### 3.1 Dua mode di config

Config juga mengikuti dua mode eksklusif. Sumber `url` vs field terpisah:

```
CLI --url / --uri  >  env DATABASE_URL  >  env DBBACKUP_URL  >  TOML [connection].url  >  field terpisah (host/port/user/database)
```

- `DATABASE_URL` boleh didukung sebagai sumber Connection URL, tapi **hasilnya diperlakukan sama seperti `--url`** — yaitu mode URL eksklusif, tunduk pada aturan Seksi 1.3 (tidak boleh digabung dengan field terpisah di TOML/job yang sama).
- `DBBACKUP_URL` sebagai alias env eksplisit untuk dbbackup; `DATABASE_URL` prioritas lebih tinggi bila keduanya set (PaaS convention).
- `TOML [connection].url` (string). Jika `url` dan field terpisah keduanya ada **di TOML yang sama** (atau env `url` + field terpisah), perlakukan sebagai conflicting configuration → `exit 10` actionable (bukan `url` menang + warning). TOML tidak boleh berisi dua mode sekaligus.
- Global `[connection]` vs per-job `url` — lihat Seksi 4 untuk scheduler: per-job harus konsisten, global `url` hanya default bila job tidak mendefinisikan koneksi sendiri.

### 3.2 TOML

Mode URL:

```toml
[connection]
url = "postgresql://user:pass@host/mydb?sslmode=require"
```

Mode structured (existing, tetap valid):

```toml
[connection]
host = "db.example.com"
port = 5432
user = "backup"
database = "mydb"
```

Jangan menaruh keduanya sekaligus dalam `[connection]` yang sama.

- `allow_plaintext_password` tidak berlaku untuk `url` — `url` yang mengandung password diperlakukan sama: warning tidak ditambah, tapi redaction wajib (Seksi 5).
- Dokumentasi harus warning: jangan commit URL berisi password ke git; prefer env (`DATABASE_URL` / `DBBACKUP_URL`).

### 3.3 Validasi

- `url` kosong/whitespace → diabaikan (fallback ke field terpisah hanya bila tidak ada `url` efektif).
- TOML/env `url` dengan scheme unknown → `exit 10` actionable.
- TOML/env `url` yang mengandung password dengan karakter khusus harus percent-encoded; parser `unquote` di memory, tidak di-log.

## Seksi 4 — Scheduler

Scheduler mengikuti prinsip dua mode yang sama:

- Setiap `[[schedule.jobs]]` boleh menggunakan **URL connection** (`url = "..."`) **atau** **structured connection fields** (`host`/`port`/`user`/`database` + `db_type`), **jangan keduanya**.
- Validasi: `url` dan field terpisah keduanya ada dalam satu job → `exit 10` fail-fast saat daemon start (pesan actionable, redacted). Tidak ada fallback diam-diam.
- `url` kosong/absen → job memakai structured mode existing (backward compat penuh). `db_type` tetap required di structured job tanpa `url`.
- Global `[connection].url` menjadi default untuk jobs tanpa `url` **dan** tanpa structured fields sendiri; per-job `url` tidak dikombinasikan dengan global structured fields.
- Inheritance `storage`/`local_path` tidak berubah.
- `db_type` inference untuk job URL: dari `url` scheme (mapping Seksi 1.2) bila job tidak set `db_type`; bila `db_type` dan `url` keduanya ada, validasi konsistensi sama seperti CLI (`exit 10` bila konflik).

Contoh (dua jobs, dua mode):

```toml
[[schedule.jobs]]
id = "neon-nightly"
url = "postgresql://neondb_owner:p%40ss@ep-xxx.neon.tech/neondb?sslmode=require&channel_binding=require"
cron = "0 3 * * *"
s3_bucket = "my-backups"
s3_prefix = "prod/neon"

[[schedule.jobs]]
id = "local-sqlite"
cron = "0 4 * * *"
db_type = "sqlite"
database = "./app.db"
storage = "local"
local_path = "/data/backups"
```

Contoh invalid (satu job dua mode):

```toml
[[schedule.jobs]]
id = "bad"
url = "postgresql://user@host/db"
host = "other-host"  # → exit 10
cron = "0 3 * * *"
```

## Seksi 5 — Redaction dan driver

### 5.1 Redaction

- Perluasan `core/redact.py`:
  - `_RE_URL_CREDS` sudah ada (`scheme://user:***@host`) — pastikan menutup `url` di log/`BackupResult.error`/Slack.
  - Tambah coverage untuk `?password=` / `?passwd=` / `?pwd=` di query string bila ada.
  - Tidak pernah `print`/`log` raw URL. Semua path error yang mengandung URL wajib `redact(url)` sebelum `err_console.print`/`log.error`.
- Password percent-encoded di-decode hanya di memory untuk `ConnectionOpts`; tidak di-log.

### 5.2 Driver (no passthrough)

- URL di-parse menjadi `ConnectionOpts` + `extra`; **tidak** diteruskan raw ke `pg_dump`/`mysqldump`/`mongodump`.
- Kontrak `DBAdapter.backup(ConnectionOpts)` tetap. Adapter yang butuh query param (mis. `sslmode`, `authSource`) membaca dari `extra`; yang tidak dikenal diabaikan.
- Alasan: jaga kompatibilitas adapter, hindari kebocoran kredensial ke `ps`/`subprocess` log, dan `mongodb+srv` tetap ditangani di layer CLI (bukan driver raw).

## Alternatif yang ditolak

- **A Strict Parsed** — `--db` tetap required, query params dibuang. Ditolak karena UX buruk (copy-paste Neon tetap butuh `--db`).
- **C Raw Passthrough** — `raw_url` ke driver. Ditolak karena breaking semua adapter, redaction lebih sulit, `ps` bocor.

## Open items untuk implementation (bukan bagian spec ini)

- Nama field `extra` vs `query_params` — putuskan saat implementasi; spec memakai `extra`.
- Default port injection: apakah `ConnectionOpts.port` diisi default (5432/3306/27017) bila URL tanpa port, atau tetap 0 dan adapter tentukan default. Rekomendasi: isi default untuk konsistensi `test-connection`.
- `mongodb+srv` comma-separated hosts: parser toleran, simpan `host` sebagai string penuh, adapter split bila perlu.

---
*Spec dikoreksi ke dua mode setara (Structured vs URL) — mutually exclusive, tanpa override. Alternatif B locked. Tidak ada source code diubah pada langkah ini.*
