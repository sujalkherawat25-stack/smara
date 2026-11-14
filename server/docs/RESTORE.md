# Smara database: backups and restore

Two copies of the database exist:

| Copy | Where | When | Encrypted | Kept |
|---|---|---|---|---|
| Local dump | `/var/backups/smara` on the Oracle VM (`smara-backup` cron, 02:45 UTC) | nightly | no (root-only, mode 600) | 7 days |
| **Off-server copy** | Owner's PC: `C:\Users\sujal\smara-offsite-archive\` (pulled daily) | 21:15 UTC on the server, pulled 09:00 IST | **yes, GPG** | 14 daily + 8 weekly |

The local dump does not protect against losing the VM. The off-server copy does, because it is on a different machine.
This is the interim setup. An S3-compatible bucket (Backblaze B2 or Cloudflare R2) can replace the PC pull later: set `OFFSITE_MODE=rclone` in `/etc/smara-offsite/offsite.env` and run `rclone` with a root-only remote.

## What runs where

**Oracle VM (server)**
- `/usr/local/bin/smara-offsite-backup.sh` (from `server/deploy/offsite-backup.sh`): dumps the DB read-only (`pg_dump` inside `smara_postgres`), gzips, encrypts to the backup **public** key, and leaves the file in `/home/ubuntu/smara-offsite-outbox/` with a SHA-256 sidecar. It keeps unpulled files for 30 days. It alerts the ops Telegram chat on failure.
- `/etc/cron.d/smara-offsite-backup`: runs it nightly at 21:15 UTC (02:45 IST), under `flock`.
- `/etc/smara-offsite/offsite.env` (root-only): key fingerprint and mode. Template: `server/deploy/offsite.env.example`.
- `/etc/logrotate.d/smara-offsite-backup`; log: `/var/log/smara-offsite-backup.log`; last success: `/var/lib/smara-offsite/last_success`.
- The server holds only the public key. It cannot decrypt the backups.

**Owner's PC (this machine)**
- `C:\Users\sujal\smara-offsite\pull-offsite.sh` (from `server/deploy/pc/pull-offsite.sh`): runs daily at 09:00 local through Windows Task Scheduler task `SmaraOffsitePull`. It downloads each waiting backup over the existing SSH key, checks the SHA-256 against the server's, moves it into the archive, and only then deletes the server copy. It keeps 14 daily and 8 weekly copies locally, and sends a Telegram alert on failure.
- `C:\Users\sujal\smara-offsite-archive\`: the encrypted archive and `pull.log`.
- The **private** key is kept offline by the owner. Do not leave it in the archive folder.

## Check that backups are landing

On the server:
```bash
sudo tail -5 /var/log/smara-offsite-backup.log
sudo cat /var/lib/smara-offsite/last_success
```

On the PC:
```bash
tail -5 "$HOME/smara-offsite-archive/pull.log"
```
A line `archived smara_<stamp>.sql.gz.gpg ... sha256=...` means the copy is in the archive and matches the server.

Run the pull once by hand, if needed:
```bash
bash "$HOME/smara-offsite/pull-offsite.sh"
```

## Restore the newest off-server copy (on a PC, never on the server)

Prerequisites: `gpg`, Docker, and the **private** key `smara-offsite-PRIVATE.asc` in a dedicated keyring.

1. Pick the newest archived file:
   ```bash
   ls -1 "$HOME/smara-offsite-archive" | grep '^smara_' | sort -r | head -3
   ```
2. Import the private key into a dedicated keyring. Then run the restore check. It decrypts the file, starts a throwaway `postgres:16-alpine` container on `127.0.0.1:25433`, creates any roles the dump's GRANTs reference, restores, and prints the four row counts. It never touches `smara_postgres`.
   ```bash
   export GNUPGHOME="$HOME/smara-restore-gnupg" && mkdir -p -m 700 "$GNUPGHOME"
   gpg --batch --import /path/to/smara-offsite-PRIVATE.asc
   bash server/deploy/restore-check.sh "$HOME/smara-offsite-archive/smara_YYYYMMDD_HHMMSS.sql.gz.gpg"
   ```
3. Compare `tenants`, `parties`, `obligations` and `chat_messages` with production:
   ```bash
   ssh -i <key> ubuntu@140.245.251.13 "sudo docker exec smara_postgres psql -U smara -d smara -tA -c 'select count(*) from tenants'"
   ```
   The counts can differ if data arrived after the dump. Compare against the dump time.

## Restore into production (disaster only)

Do this only with the owner present. Keep the damaged volume, restore into a new database, then point the app at it. Never overwrite `smara_pgdata` without a copy.

```bash
# on the server, with the decrypted dump at /root/restore/db.sql.gz (delete it afterwards)
docker exec smara_postgres psql -U smara -d postgres -c "create database smara_restored"
gunzip -c /root/restore/db.sql.gz | docker exec -i smara_postgres psql -U smara -d smara_restored -v ON_ERROR_STOP=1
```
Roles referenced by the dump's GRANTs (for example `smara_agent_ro`) must exist first; the restore check shows how.

## Key rotation

1. Generate a new GPG key pair offline: `gpg --quick-gen-key "smara-offsite-backup" rsa4096 encrypt never`.
2. Import only the new **public** key on the server, set `GPG_RECIPIENT` in `/etc/smara-offsite/offsite.env`, and run `sudo /usr/local/bin/smara-offsite-backup.sh` once.
3. Check that the new file restores with the new private key, then keep the old private key until the old copies are pruned (14 daily, 8 weekly), and then destroy it.

## Losing the private key

Old copies become unreadable. Store the private key in two offline places, for example a password manager and a USB drive kept elsewhere.
