#!/usr/bin/env bash
# SelenaCore external update script.
#
# Invoked by update_manager via:
#   sudo systemd-run --on-active=1 --unit=selena-update-<sanitized_tag> \
#       --no-block /opt/selena-core/scripts/apply-update.sh <tag> <action>
#
# Never run directly from the smarthome-core process: it lives under a
# ReadOnlyPaths sandbox and Restart=always, both of which break in-process
# self-update. This script runs in its own transient unit.
#
# Actions:
#   install   — backup current install (hardlink delta), rsync staging,
#               write .version, install deps, rebaseline integrity manifest,
#               rotate backups, restart core.
#   rollback  — restore latest backup, restart core.
#
# Exit codes:
#   0   ok
#   2   no backup to roll back to
#   3   integrity manifest rebaseline failed (do NOT start core — it would
#       be detected as tampered and dropped into SAFE MODE)
#   4   staging directory missing
set -euo pipefail

TAG="${1:?tag required}"
ACTION="${2:-install}"

INSTALL="${SELENA_INSTALL_DIR:-/opt/selena-core}"
STAGING="${SELENA_STAGING_DIR:-/var/lib/selena/update/staging}/$TAG"
BACKUP_BASE="${SELENA_BACKUP_DIR:-/opt/selena-backup}"
LOG="${SELENA_UPDATE_LOG:-/var/log/selena/update.log}"
FLAG="${UPDATE_IN_PROGRESS_FLAG:-/secure/.update_in_progress}"
KEEP="${SELENA_BACKUPS_KEEP:-3}"

mkdir -p "$(dirname "$LOG")"
exec >> "$LOG" 2>&1
echo "[$(date -Is)] === $ACTION $TAG START ==="

mkdir -p "$(dirname "$FLAG")"
touch "$FLAG"
trap 'rm -f "$FLAG"' EXIT

# Stop core BEFORE touching files. An explicit stop overrides Restart=always.
systemctl stop smarthome-core || true

if [ "$ACTION" = "rollback" ]; then
    LATEST=$(ls -1dt "$BACKUP_BASE"/*/ 2>/dev/null | head -n1 || true)
    if [ -z "$LATEST" ]; then
        echo "ERROR: no backup found in $BACKUP_BASE"
        exit 2
    fi
    echo "rollback from $LATEST"
    rsync -a --delete \
        --exclude='config' --exclude='.venv' --exclude='node_modules' \
        --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
        "$LATEST" "$INSTALL/"
elif [ "$ACTION" = "install" ]; then
    if [ ! -d "$STAGING" ]; then
        echo "ERROR: staging directory missing: $STAGING"
        exit 4
    fi

    # Hardlink delta backup. Saves disk on Pi/Jetson — only changed files
    # consume new inodes; unchanged files are linked from PREV.
    TS=$(date +%Y%m%d-%H%M%S)
    mkdir -p "$BACKUP_BASE/$TS"
    PREV=$(ls -1dt "$BACKUP_BASE"/*/ 2>/dev/null | head -n1 || true)
    if [ -n "$PREV" ] && [ "$PREV" != "$BACKUP_BASE/$TS/" ]; then
        rsync -a --link-dest="$PREV" "$INSTALL/" "$BACKUP_BASE/$TS/"
    else
        rsync -a "$INSTALL/" "$BACKUP_BASE/$TS/"
    fi

    # Apply: rsync staging → install dir.
    # config/ stays as-is (user data); .venv/ stays (host venv survives
    # across upgrades unless requirements changed); _private/ and tests/
    # are not part of the runtime install.
    rsync -a --delete \
        --exclude='config' --exclude='.venv' --exclude='node_modules' \
        --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
        --exclude='_private' --exclude='tests' \
        "$STAGING/" "$INSTALL/"

    echo "$TAG" > "$INSTALL/.version"

    # Dependencies: native venv vs Docker compose.
    if [ -x "$INSTALL/.venv/bin/pip" ] && [ -f "$INSTALL/requirements.txt" ]; then
        echo "deps: native pip install"
        "$INSTALL/.venv/bin/pip" install -q -r "$INSTALL/requirements.txt"
    elif [ -f "$INSTALL/docker-compose.yml" ] && command -v docker >/dev/null 2>&1; then
        echo "deps: docker compose build"
        ( cd "$INSTALL" && docker compose build core )
    else
        echo "deps: skipped (neither .venv nor docker-compose found)"
    fi

    # Rebaseline integrity manifest. MUST happen before starting core,
    # otherwise the agent will see hash mismatches and drop the hub into
    # SAFE MODE within 30 seconds. -T disables TTY allocation (we're under
    # systemd-run, no TTY available).
    REBASE_OK=0
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx selena-agent; then
        echo "rebaseline: docker exec selena-agent"
        if docker exec -T selena-agent python -m agent.manifest --rebuild; then
            REBASE_OK=1
        fi
    elif [ -x "$INSTALL/.venv/bin/python3" ]; then
        echo "rebaseline: host venv"
        if "$INSTALL/.venv/bin/python3" -m agent.manifest --rebuild; then
            REBASE_OK=1
        fi
    fi
    if [ "$REBASE_OK" -eq 0 ]; then
        echo "ERROR: manifest rebaseline failed — refusing to start core"
        exit 3
    fi

    # Rotate backups (keep last N).
    if [ "$KEEP" -gt 0 ]; then
        ls -1dt "$BACKUP_BASE"/*/ 2>/dev/null | tail -n +"$((KEEP+1))" \
            | xargs -r rm -rf
    fi
else
    echo "ERROR: unknown action: $ACTION (expected install|rollback)"
    exit 5
fi

systemctl start smarthome-core
echo "[$(date -Is)] === $ACTION $TAG DONE ==="
