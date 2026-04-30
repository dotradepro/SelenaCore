# Backup Manager

The `backup_manager` system module produces local tar.gz archives of
SelenaCore state. Phase 1 covers manual + scheduled local backups,
restoration with safety nets, and a settings UI. Cloud E2E upload and
QR-code device transfer ship in the source tree (`cloud_backup.py`,
`qr_transfer.py`) but are not wired up yet.

## What gets backed up

Two categories, both kept under `/var/lib/selena/backups/`:

| Category | Paths | Default | Toggle |
|---|---|---|---|
| `core` | `/var/lib/selena/selena.db`, `/var/lib/selena/widget_layout.json`, `/var/lib/selena/modules/`, `/etc/selena/` | enabled | always on |
| `secrets` | `/secure/` (excluding `vault_key`) | enabled | optional |

The SQLite database is captured via the [Online Backup API](https://www.sqlite.org/backup.html)
into a tempfile before being added to the archive, so a backup taken while
selena-core is writing never produces a torn database.

`/secure/vault_key` is **always excluded** — restoring a vault key from one
device onto another would compromise the cryptographic per-device key
separation. The vault is re-derived on first boot after restore.

Voice models (`/var/lib/selena/models/`) and recordings are intentionally
not part of any category — they are large and re-downloadable.

## Manual backup

Open the module's *Settings* page in the dashboard:

1. Tick the categories you want to include (Core is locked on).
2. Press **Backup now**. The archive appears in the *Backups* list
   within a few seconds.

REST equivalent:

```bash
curl -X POST https://<host>/api/ui/modules/backup-manager/backup/create
```

The response includes the archive name, size, and SHA-256 digest.

## Scheduled backup

In the *Schedule* card, pick one of:

- **Disabled** — no automatic backups.
- **Daily** at HH:MM (default 03:00).
- **Weekly** on Sunday at HH:MM.
- **Custom cron** — any expression accepted by the `scheduler` system
  module, e.g. `cron:30 4 * * *` or `every:6h`.

Saving the form publishes a `scheduler.register` event with job ID
`backup-manager.scheduled` and event type `backup.scheduled.fire`. The
backup module subscribes to that event and runs `create_backup()` when it
fires. Disabling the schedule publishes `scheduler.unregister`.

## Restore

1. In the *Backups* list, press **Restore** on the archive you want.
2. A modal warns that data will be overwritten. Type the archive name to
   confirm.
3. The module first creates a `selena_prerestore_<timestamp>.tar.gz`
   snapshot of the current state (separate retention pool, default 3
   copies).
4. The chosen archive is extracted to `/`. Path-traversal members are
   rejected before extraction begins.
5. After successful extraction, the module attempts
   `systemctl restart selena-core` so the restored database is picked up
   without manual intervention. If `systemctl` is unavailable (dev
   environment), the response notes that and the operator must restart by
   other means.

If you need to roll back the restore, the pre-restore snapshot is in the
same backup directory and can be restored using the same flow.

## File layout

```
/var/lib/selena/
├── selena.db                           ← captured via Online Backup API
├── widget_layout.json
├── modules/
│   └── backup_manager/
│       └── settings.json               ← user choices (categories, schedule)
└── backups/
    ├── selena_backup_YYYYMMDDTHHMMSSZ.tar.gz       ← regular pool
    └── selena_prerestore_YYYYMMDDTHHMMSSZ.tar.gz   ← pre-restore pool
/etc/selena/                            ← config files
/secure/                                ← OAuth tokens, module credentials (vault_key excluded)
```

Retention is per-pool: `max_backups` (default 5, range 1–50) for regular
backups; `PRERESTORE_RETENTION` (default 3) for pre-restore snapshots.

## API

All endpoints are mounted at `/api/ui/modules/backup-manager/`.

| Method | Path | Notes |
|---|---|---|
| GET | `/config` | Current settings |
| PATCH | `/config` | Save settings; re-registers schedule |
| GET | `/list` | All archives (regular + prerestore) with size and timestamp |
| POST | `/backup/create` | Manual backup honouring current categories |
| POST | `/backup/{name}/restore` | Body `{"confirm": "<name>"}` required |
| DELETE | `/backup/{name}` | Remove archive |
| GET | `/backup/{name}/download` | Stream `.tar.gz` |
| POST | `/backup/upload` | Multipart upload of an external `.tar.gz` |
| GET | `/widget/data/state` | Pill + rows + actions payload for a future dashboard widget (already wired) |
| POST | `/widget/action/create` | Inline "Backup now" handler dispatched by the widget's ActionButton |
| GET | `/settings` | The HTML settings page |

## Configuration

Environment overrides:

| Variable | Default | Purpose |
|---|---|---|
| `BACKUP_DEST` | `/var/lib/selena/backups` | Where archives are written |
| `BACKUP_MANAGER_STATE_DIR` | `/var/lib/selena/modules/backup_manager` | Where `settings.json` lives |
| `PRERESTORE_RETENTION` | `3` | How many `selena_prerestore_*` archives to keep |

## Out of scope (phase 2+)

- Cloud E2E backup (`cloud_backup.py`) — needs settings for password +
  platform URL + UI.
- QR transfer for new-device bootstrap (`qr_transfer.py`).
- Voice models and recordings as separate categories.
- Dashboard widget — the `/widget/data/state` and
  `/widget/action/create` endpoints are already in place; only the
  manifest's `widget` block and a template registration are missing.
