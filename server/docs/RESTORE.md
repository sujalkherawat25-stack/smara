# Smara database: backups and restore

Two copies of the database exist:

| Copy | Where | When | Encrypted | Kept |
|---|---|---|---|---|
| Local dump | `/var/backups/smara` on the Oracle VM (`smara-backup` cron, 02:45 UTC) | nightly | no (root-only, mode 600) | 7 days |
| **Off-server copy** | Cloudflare R2 bucket `smara-offsite-backups` (see below) | 21:15 UTC = 02:45 IST nightly | **yes, GPG** | 14 daily + 8 weekly |

The local dump does not protect against losing the VM. The off-server copy does.

## What runs where

- `/usr/local/bin/smara-offsite-backup.sh` (from `server/deploy/offsite-backup.sh`): dumps the DB, encrypts it to the backup **public** key, uploads it, verifies the size, prunes old copies, and sends a Telegram alert to the ops chat on failure.
- `/etc/cron.d/smara-offsite-backup` (from `server/deploy/smara-offsite-backup.cron`): runs the script as root with `flock`.
- `/etc/smara-offsite/offsite.env` (root-only): remote path and key fingerprint. Template: `server/deploy/offsite.env.example`.
- `/root/.config/rclone/rclone.conf` (root-only): R2 credentials. Never in git.
- `/var/log/smara-offsite-backup.log`: run log. Rotated weekly by `/etc/logrotate.d/smara-offsite-backup`.
- `/var/lib/smara-offsite/last_success`: timestamp of the last successful run.
- The **private** key is not on the server. It is kept offline by the owner.

## Check that backups are landing

```bash
sudo tail -20 /var/log/smara-offsite-backup.log
sudo cat /var/lib/smara-offsite/last_success
```

A line `ok smara_<stamp>.sql.gz.gpg size=...B remote=...B` means the copy was uploaded and its size matched.

## Restore the newest off-server copy (on a PC, never on the server)

Prerequisites: `gpg`, `rclone` and Docker. The private key `smara-offsite-PRIVATE.asc` is imported into a dedicated keyring.

1. Install rclone and configure a remote named `r2` with the same R2 credentials (or use the R2 dashboard to download the file).
2. List the copies and take the newest:
   ```bash
   rclone lsf --format "tsp" r2:smara-offsite-backups/ | sort -r | head -5
   ```
3. Download it to an empty folder:
   ```bash
   mkdir -p ~/smara-restore && rclone copyto r2:smara-offsite-backups/smara_YYYYMMDD_HHMMSS.sql.gz.gpg ~/smara-restore/backup.sql.gz.gpg
   ```
4. Import the private key into a dedicated keyring and run the restore check. It decrypts, starts a throwaway `postgres:16-alpine` container on `127.0.0.1:25433`, restores, and prints row counts. It never touches `smara_postgres`.
   ```bash
   export GNUPGHOME=~/smara-restore/gnupg && mkdir -p -m 700 "$GNUPGHOME"
   gpg --batch --import /path/to/smara-offsite-PRIVATE.asc
   bash server/deploy/restore-check.sh ~/smara-restore/backup.sql.gz.gpg
   ```
5. Compare the four counts (`tenants`, `parties`, `obligations`, `chat_messages`) with production:
   ```bash
   sudo docker exec smara_postgres psql -U smara -d smara -tA -c "select count(*) from tenants"
   ```
   The dump was taken at 21:15 UTC, so production counts can be higher if messages arrived later. Compare against the time of the dump.

## Restore into production (disaster only)

Do this only with the owner present. Stop the app first (`docker compose ... stop smara_app`), keep the damaged volume, restore into a new database, then point the app at it. Never overwrite `smara_pgdata` without a copy of it.

```bash
# on the server, after the decrypted file is at /root/restore/db.sql.gz (never leave it there)
gunzip -c /root/restore/db.sql.gz | docker exec -i smara_postgres psql -U smara -d postgres -c "create database smara_restored"
gunzip -c /root/restore/db.sql.gz | docker exec -i smara_postgres psql -U smara -d smara_restored -v ON_ERROR_STOP=1
```

## Key rotation

1. Generate a new GPG key pair offline (`gpg --quick-gen-key "smara-offsite-backup" rsa4096 encrypt never`).
2. Export only the **public** key and import it on the server (`gpg --import`), then set `GPG_RECIPIENT` in `/etc/smara-offsite/offsite.env` to the new fingerprint.
3. Run `sudo /usr/local/bin/smara-offsite-backup.sh` once and confirm the new file restores with the new private key.
4. Keep the old private key until the copies encrypted with it are pruned (14 days for daily, 8 Sundays for weekly), then destroy it.

## Losing the private key

Old copies become unreadable. This is why the private key must be stored in two offline places (for example a password manager and a USB drive kept elsewhere).
