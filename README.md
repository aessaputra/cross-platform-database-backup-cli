# Cross-Platform Database Backup CLI

Cross-platform database backup CLI — full backups to S3 (v1).

> v1 supports **full backups only**. Incremental/differential is reserved for v2.

## Install

```bash
pipx install dbbackup
# or
pip install -e .
```

## Usage

```bash
dbbackup --help
dbbackup --version
dbbackup backup --help
dbbackup restore --help
dbbackup test-connection --help
dbbackup schedule --daemon --help
```
